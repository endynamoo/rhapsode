# Using Rhapsode

Everything on this page is day-to-day use. Install instructions are in
[../AGENTS.md](../AGENTS.md); manga stills are in [STILLS.md](STILLS.md); internals are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Keyboard and mouse

| Action | How |
|---|---|
| Play / pause | `Space` or the big button |
| Next / previous sentence | `→` / `←` |
| Jump to any sentence | click it |
| Seek | click the progress bar |
| Speed | `[` / `]` or the speed menu |
| Text size | `+` / `-` |
| Add a document | `⌘N` (`Ctrl+N` on Windows/Linux) |
| Switch Book / Manga view | `V` |
| Jump to a chapter | `C` (opens Contents) |
| Search your library | `⌘F` (`Ctrl+F`) or the magnifier |
| Hide UI / immersive mode | `F` or `Esc` |
| Settings | `⌘,` (`Ctrl+,`) |

Clicking a sentence starts reading from there. The current sentence stays bright
while the rest of the page dims, and the page follows along as it reads.

The sidebar has two tabs: **Library** and **Contents**. Opening a book or a PDF
with bookmarks switches to Contents automatically; clicking a chapter reads from
that chapter. In Book view the current word is highlighted inside the rendered
chapter, and the chapter scrolls to keep it in sight.

## Searching your library

Press `⌘F` / `Ctrl+F` (or the magnifier in the top bar) and type. Results are
grouped by book, each with the matching passage, the chapter or page, and the
sentence number. `↑` `↓` move through the results, `Enter` opens one, and the
book opens at that sentence with the matching words marked.

- **Any time / Read this week / Read this month** narrow the search to books you
  have actually opened or read in that window. Importing a book is not reading
  it, so a shelf you have not touched stays out of a recent search.
- **Keywords work immediately.** The first search after an import may say a book
  is waiting to be indexed; the panel offers **Index library**, and indexing runs
  in the background while you keep reading.
- **Meaning search is optional.** If the panel says *Keyword search only*, press
  **Download** once (23 MB, Apache-2.0, runs on the CPU) and Rhapsode can find a
  passage that talks about your query without using its words. To ignore the
  model entirely, set `MURMUR_SEMANTIC=off`.

The index lives in `search.db` next to `murmur.db`. It is derived from your
books: deleting it is safe and only costs a rebuild. Details, the API and the
ranking formula are in [SEARCH.md](SEARCH.md).

## Foreign voices and translation

Kokoro voices are language-specific: the Spanish voice only knows Spanish
phonemes. Rhapsode therefore translates each sentence into the voice's language
before speaking it, and shows the translated sentence in a strip above the player.
That strip gets its own word-level highlight, and the page keeps a proportional
word cursor derived from the translated timing, so the render still follows along.

- Detection, translation, and the language switch run on-device. On macOS that is
  Apple's Translation framework; nothing is sent anywhere. On Windows and Linux
  it is a local OpenAI-compatible server: set `MURMUR_TRANSLATE_URL` (and
  `MURMUR_TRANSLATE_MODEL` if the server needs a name), or leave both unset and
  Rhapsode picks up a server already listening on 8091, 8080, 8000, 1234 or 11434.
- Source language is detected automatically, so a Spanish PDF read by an English
  voice is translated into English too.
- Only sentences that need it are translated. Results are cached in the library
  database, so replaying a book costs nothing.
- Japanese and Mandarin are phonemized with misaki (`pyopenjtalk` / `jieba`),
  because Kokoro's default espeak path either slows Japanese down badly or cannot
  read Chinese at all.
- Turn it off with **Settings → Translate to voice language** to hear the original
  text read with a foreign accent.

Apple needs a language pack for each direction. If one is missing, Rhapsode falls
back to the original sentence and tells you to add it in **System Settings →
General → Language & Region → Translation Languages**. On Windows and Linux a
missing `MURMUR_TRANSLATE_URL` has the same effect: the voice reads the original
text.

## Quick Read from anywhere

`scripts/quick-read.sh` reads the current selection in whatever app is in front.

1. Open **Shortcuts.app** → **+** → add **Run Shell Script**.
2. Paste the full path to the script — `scripts/quick-read.sh` inside this repo.
3. In the shortcut's details, assign a keyboard shortcut (e.g. `⌃⌥R`).
4. Run it once and allow **Accessibility** access when macOS asks.

If a Rhapsode tab is already open it receives the text over Server-Sent Events and
starts playing immediately. Otherwise a tab opens (browsers may require one click
before audio can start).

The script first asks the app for its selected text via Accessibility, and only
falls back to simulating `⌘C` (restoring your clipboard afterwards) if that fails.

### Read a screenshot

Take a screenshot straight to the clipboard with `⌃⌘⇧4`, then press the same
hotkey. With no text selected, Rhapsode OCRs the clipboard image and starts reading
it — handy for slides, scanned pages, and anything that refuses to be selected.

## Scanned PDFs and images

OCR is on-device on every platform and needs no model download:

| Platform | Engine | Notes |
|---|---|---|
| macOS | Apple Vision | the engine behind Live Text; ships with `pyobjc-framework-Vision` |
| Windows | `Windows.Media.Ocr` | ships with `winsdk`; reads with the language packs Windows already has |
| Linux | Tesseract | `sudo apt install tesseract-ocr` — the one backend needing a separate install |

If no backend is present, text PDFs still work and scanned ones return a clear
error instead of an internal one. `health.ocr` tells you which case you are in.

- **Mixed PDFs** are handled page by page — any page with less than 40 characters
  of text gets OCR'd, the rest use their text layer.
- **Images** (PNG, JPEG, TIFF, BMP, WebP, HEIC) are OCR'd in one pass.
- Pages are rendered at 200 DPI; results are dehyphenated and rejoined into
  paragraphs before sentence splitting.

Limits and knobs (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `MURMUR_OCR_MAX_PAGES` | `200` | Pages OCR'd per PDF before stopping |
| `MURMUR_OCR_DPI` | `200` | Render resolution; raise for small type |
| `MURMUR_OCR_MIN_CHARS` | `40` | Below this a page counts as "scanned" |
| `MURMUR_OCR_LANGS` | auto | e.g. `en-US,fr-FR` to force languages |

## Access from other devices (Tailscale)

Rhapsode can bind to the network so your phone, tablet, or another laptop can use
it. It is **off by default**, and an agent installing it must leave it off unless
you ask.

- **Mac app:** Settings → **Remote access** → on, then restart Rhapsode. Or launch
  it directly with `open dist/Rhapsode.app --args --host 0.0.0.0`.
- **Dev server:** `MURMUR_HOST=0.0.0.0 ./run.sh`.

When the server is not loopback-only, every non-local request must present a token.
The first visit uses a link ending in `?token=…`; the token is then kept in a cookie
for a year. The local window and the Quick Read hotkey never need it, so the
on-machine experience is unchanged.

Settings shows ready-made links for each Tailscale IPv4 address, the MagicDNS name
(`your-machine.your-tailnet.ts.net`), and the LAN address. Links are only listed for
interfaces that are actually up: if Tailscale is stopped, Settings says so and
shows the LAN link instead. The secret lives in `access_token` inside the data
directory (mode 0600) and can be overridden with `MURMUR_TOKEN`. `/api/network` —
the only endpoint that reveals the token — is loopback-only.

Binding to `0.0.0.0` also exposes Rhapsode to the local network, not just Tailscale,
so use it on networks you trust; the token is the gate. To restrict it to the
tailnet, bind the Tailscale address instead: `--host 100.x.y.z`.

## Where your data lives

| Platform | Data directory |
|---|---|
| macOS | `~/.murmur` (kept for compatibility) |
| Windows | `%LOCALAPPDATA%\Rhapsode` |
| Linux | `$XDG_DATA_HOME/rhapsode` or `~/.local/share/rhapsode` |

Override with `MURMUR_HOME`. Inside it:

| Path | Contents |
|---|---|
| `models/` | `kokoro-v1.0.onnx`, `voices-v1.0.bin` |
| `murmur.db` | document library, positions, settings |
| `search.db` | derived search index — safe to delete |
| `models/minilm-l6-v2/` | optional search embeddings (23 MB) |
| `cache/` | synthesized WAV clips |
| `cache/pages/` | rendered PDF page images |
| `files/` | original PDFs and images (for the page view) |
| `exports/` | exported MP3s |
| `film/` | film scenes and rendered chapters |

Delete that folder to reset — back it up first if you care about the library. The
audio cache can also be trimmed from Settings.

Environment variables: `MURMUR_PORT` (default 8765), `MURMUR_HOME`,
`MURMUR_CACHE_MB`, `MURMUR_NO_OPEN=1`, `MURMUR_SEMANTIC=off` (ignore the search
model even when it is installed).

## Troubleshooting

**First sentence is slow.** The model is loading and warming. The status dot in the
sidebar turns green when it is ready; later sentences are fast.

**Nothing happens on a Quick Read hotkey.** macOS needs Accessibility permission
for Shortcuts. System Settings → Privacy & Security → Accessibility.

**No sound after a Quick Read.** The browser blocked autoplay because the tab had
no prior interaction. Click the "Listen" pill in the app.

**PDF looks garbled.** Scanned PDFs are OCR'd automatically, but a photo of
handwritten text or a low-resolution fax may come out poorly. Try
`MURMUR_OCR_DPI=300` and re-upload. Two-column academic layouts can interleave
columns, because Vision reads in geometric order.

**Port already in use.** `MURMUR_PORT=9000 ./run.sh`.

**Install-side problems** (missing model, no desktop window, missing Tesseract,
CUDA out of memory) are tabulated in [../AGENTS.md](../AGENTS.md) §7.
