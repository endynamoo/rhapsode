# Changelog

Notable changes to Rhapsode, newest first. Versions are the app's own
(`murmur.__version__`); `known-good-*` tags are checkpoints of the `stable`
branch and do not get a heading here.

The format follows Keep a Changelog. Add an entry under `## [Unreleased]` in the
same commit as the change, then run `python scripts/changelog_render.py` to
refresh the copy at the bottom of [README.md](README.md) — `tests/test_changelog.py`
fails when that copy is stale.

## [Unreleased]

### Added

- **Auto Generate** in Settings (off by default): once you start a chapter film, the remaining chapters queue after it, one at a time, without a confirmation per chapter. A **Stop auto-generation** button drains the queued chapters and leaves the one already painting alone.
- Cinema reports what the stills engine is actually doing — planning, loading the model, painting, cleaning up, polishing, or showing a placeholder — instead of one static "Painting scene…", with the backend, model and profile on screen and a Retry button when a scene fails.

### Changed

- Theme is a dropdown in Settings — grouped Dark and Light, with a live preview of the selected palette beside it — instead of a scrollable grid of twenty swatches.
- Settings → Engine is now **Stills engine** and reads the same activity object as Cinema, so the two can no longer disagree about what is running.
- A scene that falls back to the placeholder is treated as delivered: it is shown, counted as done, and labelled, rather than re-planned on every poll.

- The in-app mark now uses the purple app icon's palette instead of white strokes on near-black, and its halo is purple rather than cyan, so the sidebar, the empty state, the favicon and the installed icon read as one brand.

### Fixed

- A voice model installed while the app was open no longer leaves the sidebar on "Loading voice model…" forever: the engine re-checks the weights, and if they are genuinely missing the pill says so and names the fix instead of spinning.
- RTF import falls back to the built-in reader when `textutil` cannot parse the file, so an unclosed RTF no longer arrives as an empty document.
- The Windows installer's two task checkboxes share one left edge; splitting them into two group headings had pushed the stills row out of line.
- On macOS the "translation is not set up" warning names the real cause — Apple Translation needs macOS 26, or this build shipped without the Swift helper — instead of telling the reader to set an environment variable they have no reason to know.

## [0.2.0-alpha] - 2026-11-18

### Added

- Linux is verified on a clean runner: `.github/workflows/linux.yml` runs the unit suite, then a real `python -m murmur` server with Tesseract as the OCR backend, asserting `/api/health` is healthy and remote access is off.
- macOS release builds on clean runners: `.github/workflows/macos.yml` builds and self-tests the `.app` for arm64 (`macos-latest`) and x86_64 (`macos-15-intel`), wraps each in a drag-install DMG, and attaches both to a `v*` release; the Windows installer workflow attaches its Setup.exe the same way.
- `scripts/make_dmg.sh` for a local drag-install DMG.
- Search across your whole library with `Ctrl+F` / `⌘F` or the magnifier: results grouped by book, the matching passage marked, and a jump straight to that sentence, chapter or page. Keywords work with nothing extra installed; the optional 23 MB meaning model (Apache-2.0, CPU only) is offered as a download in the panel and can be switched off with `MURMUR_SEMANTIC=off`.
- "Read this week" and "Read this month" filters for search, based on when a book was actually opened or its position saved.
- Live progress for library indexing and the model download, plus an Index library button; an interrupted index resumes where it stopped instead of duplicating rows.
- Troubleshooting page in the app: the three guards, where the logs live, and what was already fixed.
- The RAM probe the painting guard calls, and the RAM it measured shown in Settings.
- Named engine profiles (`lite`, `balanced`, `max`, `cpu`) with auto-install, and an Engine panel in Settings: profile summary, painter status and Restart engine.
- Stills polish pass: pose detection, a masked redraw of the hands, off by default, reported in `/api/health`.
- q4 GGUF transformer path — FLUX.2 klein on an 8 GB card, precision tier chosen from free VRAM.
- Resident painter: one model load per session, guarded by a lock that survives restarts.
- Instrumentation for the painter and the model caches; health reports the painter's precision.
- Measured q4 numbers and per-card precision tiers in the docs.

### Changed

- The Windows installer and the macOS `.app` read their version from `murmur.__version__`, so a version bump is one line instead of three files.
- The macOS bundle identifier is `com.endynamoo.rhapsode`, not `local.rhapsode`.
- The prompt helper and the translation helper are optional at build time: a toolchain without Foundation Models or the macOS 26 Translation SDK still produces a working app, without that helper.
- The front page, the third-party notices and the app's copyright string say "license", not "licence".
- Opening or resuming a book now records reading activity, so recent-only search can tell a book you read last week from one you imported and never touched.
- The README front page states what actually ships: the per-user Windows installer, ROCm stills, and the real test count.
- The generated changelog block no longer repeats the "Changelog" heading above it.
- `AGENTS.md` is plain UTF-8 text again, so its arrows and dashes render as arrows and dashes.
- Hot caches are memory-resident and the database connection is shared.
- The installer says when to prefer the shared-card profile.

### Fixed

- Importing the app on macOS and Linux no longer crashes in `config.py` (a `str / str` while building the stills interpreter path); Windows never hit it because it takes the other branch.
- `./run.sh`, `./build_app.sh` and the other shell scripts are executable in git again; a fresh macOS or Linux checkout could not run them.
- `scripts\build_installer.ps1` builds on a clean machine without `.venv`, and survives PyInstaller's stderr logging under Windows PowerShell, so the installer workflow on CI can run it.
- Painting refuses to start without enough free system RAM, and reports what was left instead of dying mid-still.
- A resident painter survives an out-of-memory kill instead of dying, and is reaped with the app, including the one-shot fallback.
- A still that is already painted is never repainted on a later session.
- Both halves of the app log to file, because the window and the server have each died silently.
- The engine switch posts JSON — the shared api helper sets no content type.
- Hand repair works against quantized weights, accepts wrists only inside the person's box, and the arm-ratio flag is relaxed.
- A placeholder card is treated as missing art everywhere; `probe_imports` got its `pathlib` import back.

## [0.1.0-alpha] - 2026-11-16

### Added

- First Windows-capable build of the reader and stills app: PDF, EPUB, DOCX, RTF, TXT, Markdown and HTML imports.
- OCR through Windows' own engine, and translation through any local OpenAI-compatible server.
- FLUX.2 [klein] as the only stills engine, on every platform.
- Import-flow tests, spawn guards, and a Windows CI workflow.
- An ink-logo banner and an AI-provenance section in the README.

### Fixed

- No console windows on Windows, and real VRAM numbers reported.
- `run.ps1` is ASCII only, writes timestamped logs, and reports failures instead of claiming success.
- The two 500s the installed build threw, and hardened painting.
