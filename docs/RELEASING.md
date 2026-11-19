# Releasing macOS builds: Developer ID signing and notarization

Status: **proposal** (issue #24). Nothing here has been run yet: the Windows
workstation that wrote it cannot run `codesign`, `notarytool` or `stapler`, so
every command below is from Apple's current documentation and published
practice, and the verification transcript is still a checklist. Treat the first
run on a Mac as the validation, and fix the document where reality differs.

Today `build_app.sh` ends with `codesign --force --deep --sign -`, an ad-hoc
signature, and `packaging/Rhapsode.spec` passes `codesign_identity=None`. Ad-hoc
signing lets the app run on the machine that built it; a downloaded copy meets
Gatekeeper's "Apple could not verify …" dialog instead. Signing with a Developer
ID certificate and notarizing the result removes that dialog.

## 1. What is required, and what is not

| Item | Needed? | Why |
| --- | --- | --- |
| Apple Developer Program membership, 99 USD/year | **yes** | Developer ID certificates are only issued to members |
| `Developer ID Application` certificate | **yes** | signs the `.app`, its executables and the DMG |
| `Developer ID Installer` certificate | **no** | only for `.pkg` installers; a DMG needs none |
| Hardened Runtime (`--options runtime`) | **yes** | notarization refuses non-hardened code |
| Secure timestamp (`--timestamp`) | **yes** | part of a valid notarizable signature |
| Notarization + stapling | **yes** | Gatekeeper trusts it only after a ticket exists |

The **Team ID** (10 characters, next to the certificate in the developer
portal) is what ties nested code together: under the hardened runtime, library
validation accepts Apple system libraries and code signed with the *same* Team
ID. It is not a secret, but it belongs in the CI environment rather than in the
tree so forks can build unsigned.

**Enrolment.** An individual account (Apple ID + payment) is normally active the
same day. An organization needs a D-U-N-S number for the legal entity and takes
days to weeks. The account holder is the person who owns the app; if membership
lapses, already-notarized builds keep running (tickets live online and in the
stapled bundles), but no new build can be notarized and certificates eventually
expire. Rotate the certificate before expiry, not after.

## 2. Certificates and secrets

Create the certificate once, on a Mac:

1. Keychain Access → Certificate Assistant → *Request a Certificate From a
   Certificate Authority* → save the CSR to disk (2048-bit RSA, SHA-256).
2. developer.apple.com → Certificates → **+** → *Developer ID Application* →
   upload the CSR → download the `.cer` → double-click to install it.
3. In Keychain Access, select the certificate **and its private key**, right-click
   → *Export* → `.p12` with a strong password.

For notarization, prefer an **App Store Connect API key** over an Apple ID and
app-specific password: it has no 2FA coupling, can be revoked per pipeline, and
is what Apple's own documentation shows. App Store Connect → Users and Access →
Integrations → *App Store Connect API* → create a key with the **Developer**
role → download the `.p8` once.

CI secrets (names only, no values):

| Secret | Contents |
| --- | --- |
| `APPLE_CERT_P12_BASE64` | base64 of the exported `.p12` |
| `APPLE_CERT_PASSWORD` | the password given in step 3 |
| `APPLE_SIGNING_IDENTITY` | `Developer ID Application: Name (TEAMID)` |
| `APPLE_TEAM_ID` | the 10-character Team ID |
| `APPLE_API_KEY_ID` | API key ID |
| `APPLE_API_ISSUER_ID` | issuer ID |
| `APPLE_API_KEY_P8_BASE64` | base64 of `AuthKey_XXXX.p8` |

Forks without the secrets must keep building: every signing step is gated on the
secrets being present, and the fallback stays the current ad-hoc signature.

## 3. Signing the bundle

Two rules decide everything here:

- **Sign nested code first, the app bundle last.** The outer signature seals the
  contents, so anything signed afterwards invalidates it.
- **Do not use `--deep` to sign.** Apple's guidance is explicit; `--deep` is a
  verification convenience, not a signing plan, and it applies one entitlement
  set to every nested binary.

PyInstaller 6.x regenerates signatures for the binaries it collects when the
spec provides an identity, which is the shortest correct route for this bundle:
set `codesign_identity` and (if needed) `entitlements_file` in
`packaging/Rhapsode.spec`, then sign the finished `.app` once with
`--options runtime --timestamp`. `build_app.sh` should stop calling
`codesign --force --deep --sign -` and instead:

```bash
IDENTITY="${APPLE_SIGNING_IDENTITY:-}"
if [ -z "$IDENTITY" ]; then
  codesign --force --sign - dist/Rhapsode.app   # local/CI without secrets
else
  # Nested code PyInstaller did not sign itself (Mach-O files without an extension,
  # the optional Swift helpers in Contents/Frameworks/bin, anything the spec
  # excludes from signing).
  find dist/Rhapsode.app/Contents -type f -perm -u+x -print0 |
    xargs -0 -I{} sh -c 'file -b "{}" | grep -q "Mach-O" && \
      codesign --force --options runtime --timestamp --sign "$0" "{}"' "$IDENTITY"
  codesign --force --options runtime --timestamp --sign "$IDENTITY" dist/Rhapsode.app
fi
codesign --verify --deep --strict --verbose=2 dist/Rhapsode.app
```

The sweep catches what a directory list misses: `Python.framework/Versions/*/Python`
and similar binaries have **no file extension**, and `onnxruntime`/OpenCV ships
`.dylib`s inside `Contents/Frameworks`. Verify instead of assuming: after the
first signed build run

```bash
codesign -dv --verbose=4 dist/Rhapsode.app        # Authority, TeamIdentifier, Timestamp, Runtime Version
code=$(mktemp -d); ditto -x -k "$DMG" "$code"; codesign --verify --deep --strict --verbose=2 "$code/Rhapsode.app"
```

**Entitlements.** Start with none. CPython, onnxruntime and OpenCV do not need
JIT or unsigned-executable-memory entitlements; WKWebView is a system framework,
so library validation accepts it. Add an entitlement only after a *measured*
failure names it — usually `com.apple.security.cs.disable-library-validation`
for a third-party dylib signed with a different identity. Two things worth
testing explicitly, because they are this app's whole point:

- Kokoro TTS through `onnxruntime` (CPU and CoreML providers),
- stills through mflux/MLX on Apple Silicon (Metal shader compilation at runtime).

If either fails under the hardened runtime, capture the exact denial before
widening the entitlement file, and record the result in this document.

**Architecture.** Sign each arch's `.app` in its own job; a universal bundle is
not required and doubles build time. The DMG itself is signed too (section 4).

## 4. Notarization and stapling

Notarize the **app**, staple it, then build and notarize the **DMG**. Apple
accepts a DMG directly, but stapling the app first means a user who drags the app
out of the image still has a stapled ticket. Stapling matters offline: an
unstapled but notarized app needs Apple's servers on first launch.

```bash
ditto -c -k --keepParent dist/Rhapsode.app dist/Rhapsode.zip

xcrun notarytool submit dist/Rhapsode.zip \
  --key "$PWD/AuthKey.p8" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER_ID" \
  --wait --output-format json | tee notary.json

# The exit status is not the answer; the status field is.
grep -q '"status":"Accepted"' notary.json || {
  xcrun notarytool log "$(python3 -c 'import json;print(json.load(open("notary.json"))["id"])')" \
    --key "$PWD/AuthKey.p8" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER_ID"
  exit 1
}

xcrun stapler staple dist/Rhapsode.app
xcrun stapler validate dist/Rhapsode.app

scripts/make_dmg.sh                       # builds the image from the stapled app
codesign --force --timestamp --sign "$APPLE_SIGNING_IDENTITY" dist/Rhapsode-<version>-<arch>.dmg
xcrun notarytool submit dist/Rhapsode-<version>-<arch>.dmg \
  --key "$PWD/AuthKey.p8" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER_ID" --wait
xcrun stapler staple dist/Rhapsode-<version>-<arch>.dmg
```

`notarytool submit --wait` returning 0 means the *upload* worked. A rejected
build still exits 0; parse the status, and read the `notarytool log` — it names
the offending binary and the reason (unsigned nested code and a missing
timestamp are the two usual answers for Python bundles).

## 5. GitHub Actions sketch

Per-arch job, after the existing build and `--selftest`:

```yaml
env:
  APPLE_SIGNING_IDENTITY: ${{ secrets.APPLE_SIGNING_IDENTITY }}
steps:
  - name: Import signing certificate
    if: env.APPLE_SIGNING_IDENTITY != ''
    env: { P12: ${{ secrets.APPLE_CERT_P12_BASE64 }}, P12_PASS: ${{ secrets.APPLE_CERT_PASSWORD }} }
    run: |
      KEYCHAIN="$RUNNER_TEMP/sign.keychain-db"
      echo "$P12" | base64 --decode > "$RUNNER_TEMP/cert.p12"
      security create-keychain -p actions "$KEYCHAIN"
      security set-keychain-settings -lut 900 "$KEYCHAIN"
      security unlock-keychain -p actions "$KEYCHAIN"
      security import "$RUNNER_TEMP/cert.p12" -k "$KEYCHAIN" -P "$P12_PASS" \
        -T /usr/bin/codesign -T /usr/bin/security
      security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k actions "$KEYCHAIN"
      security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')
      rm -f "$RUNNER_TEMP/cert.p12"
  - name: Write notary API key
    if: env.APPLE_SIGNING_IDENTITY != ''
    run: echo "${{ secrets.APPLE_API_KEY_P8_BASE64 }}" | base64 --decode > "$RUNNER_TEMP/AuthKey.p8"
  - name: Sign, notarize, staple, DMG
    if: env.APPLE_SIGNING_IDENTITY != ''
    run: ./scripts/sign_and_notarize.sh        # the block from sections 3-4
    # ^ this script does not exist in the repository yet; see the checklist in section 8
  - name: Keychain cleanup
    if: always()
    run: security delete-keychain "$RUNNER_TEMP/sign.keychain-db" || true
```

Notes:

- Sign **inside each arch job**: macos-latest (arm64) and macos-15-intel each
  produce their own `.app` and DMG. The API key is written to `$RUNNER_TEMP`,
  never the workspace, so it cannot be picked up by an artifact upload.
- Keep the unsigned path: a fork or a PR from a contributor does not have the
  secrets, and must still produce a runnable (ad-hoc) app for its own CI.
- Do not notarize on pull requests: it is a network round-trip to Apple for an
  artifact nobody will ship. Gate it on `github.event_name == 'push'` to a `v*`
  tag.

## 5b. Attach the licence notice to the release

The bundles contain GPL-3.0 components (eSpeak NG through `phonemizer`), so the
release page is part of the licence obligation, not decoration:

- The release body must say that the build includes GPL-3.0 components and point
  at `licenses/SOURCES.md` in the tag, which carries the written offer.
- `licenses/` must be present in the tag being released - the installer copies it
  to `{app}\licenses` and `build_app.sh` copies it into the bundle's `Resources`.
- Just before tagging, confirm the versions in `licenses/SOURCES.md` still match
  the wheels the build uses (`phonemizer`, `espeakng-loader`, `soundfile`,
  `pillow-heif`, `opencv-contrib-python-headless`). A bump that changes one of
  them updates that file in the same commit.

## 6. Verification (what "done" looks like)

```bash
codesign -dv --verbose=4 dist/Rhapsode.app
#   Authority=Developer ID Application: <name> (<TEAMID>)
#   Timestamp=...
#   Runtime Version=...
codesign --verify --deep --strict --verbose=2 dist/Rhapsode.app
#   dist/Rhapsode.app: satisfies its Designated Requirement
spctl -a -vvv -t exec dist/Rhapsode.app
#   accepted
#   source=Notarized Developer ID
xcrun stapler validate dist/Rhapsode.app
#   The validate action worked!
```

Then the part scripts cannot prove, because the whole problem is first launch on
someone else's machine:

1. Download the DMG through a browser (or `curl` and
   `xattr -w com.apple.quarantine "0081;$(printf %x $(date +%s));Safari;"` the
   file) so it carries a quarantine attribute.
2. Open it, drag the app to `/Applications`, double-click.
3. Expect no "Apple could not verify" dialog. On macOS 15+ the right-click →
   Open shortcut no longer bypasses the dialog either, so this is the check that
   matters.
4. Run the app once: play a sentence (Kokoro), OCR a scanned page, and build one
   still (stills), since hardened runtime problems surface there first.

Record the transcript of a real build here when it exists. Until then this
document is a plan, not a claim.

## 6b. When Actions minutes are gone

The workflows are the release path, but they are not the only way to verify one. If the
organisation's minutes are exhausted (or a runner is down) and a build is needed anyway:

```powershell
.\scripts\ci-local.ps1 -Job unit,ui      # the suite and the browser journeys
.\scripts\ci-local.ps1 -Job installer    # PyInstaller + Inno, silent install, health, uninstall
```

`unit` builds a venv from `requirements-test.txt` alone, the way a runner does; `installer`
installs into a temp directory, checks that `licenses\` arrived, runs the shipped engine
script, starts the app and asks for `/api/health`, then uninstalls and confirms the app is
gone. What it cannot do is macOS - `build_app.sh` needs a Mac - so a release cut this way
either waits for minutes to reset or ships Windows and source only, with the DMGs attached
later. Say which, in the release notes, rather than leaving the gap implicit.

## 7. Risks and fallbacks

| Risk | Mitigation |
| --- | --- |
| Notarization rejects the bundle | read `notarytool log`; the usual causes are unsigned nested binaries, no timestamp, or `get-task-allow=true` |
| Hardened runtime breaks TTS/OCR/stills | test the four paths above; add the narrowest entitlement that fixes the measured failure, and note it here |
| `image-venv` interpreter is unsigned | it lives outside the bundle (user data), is not sealed by the app signature and is not downloaded with a quarantine flag; if Gatekeeper ever blocks it, sign it at install time |
| Account lapses or certificate expires | already shipped, stapled builds keep working; rotate the certificate and re-notarize the next release |
| Forks and contributors | secrets absent → ad-hoc path, unchanged behaviour |
| Cost | 99 USD/year; notarization is free and unlimited |

**Free alternatives.** There is no legitimate free way to remove the first-launch
dialog: a self-signed certificate is not trusted by Gatekeeper, and
`xattr -d com.apple.quarantine` is a per-machine workaround a user has to find.
Keep the documented first-launch steps for forks and unsigned builds, keep the
ad-hoc signature as the local default, and treat notarization as a property of
the official release, not of the source tree.

## 8. Files to change, and checklist

| File | Change |
| --- | --- |
| `packaging/Rhapsode.spec` | pass `codesign_identity` and `entitlements_file` from the environment |
| `build_app.sh` | replace the ad-hoc `--deep` call with the gated signing block; `--verify` afterwards |
| `scripts/make_dmg.sh` | sign the DMG when an identity is available |
| `scripts/sign_and_notarize.sh` | to be written: zip, submit, parse status, staple, DMG, verify |
| `.github/workflows/macos.yml` | import the certificate, write the API key, run the script, clean up |
| `INSTALL.md` §A2 | "ad-hoc signed, not notarized" becomes "notarized when the secrets are present" |
| `README.md`, `docs/PLATFORMS.md` | M5 release engineering: signing marked done and how it is verified |

- [ ] Enrol; owner decided; certificate exported and stored in a password manager
- [ ] Add the seven secrets to the repository
- [ ] First signed build on a Mac; entitlements measured, not guessed
- [ ] Notarization accepted for arm64 and x86_64
- [ ] Stapled DMG verified with `spctl` and launched from quarantine on a clean Mac
- [ ] Transcript pasted above; `INSTALL.md`/`README` updated
- [ ] Forks still build unsigned
