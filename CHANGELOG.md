# Changelog

Every change lands here in the same commit that makes it. Entries are grouped
Added / Changed / Fixed / Removed / Security and written for someone using the app,
not for someone reading a diff. `scripts/changelog_render.py` turns this file into
the truncated copy at the bottom of the README and into a release's notes.

## [Unreleased]

### Added

- **The installer proves itself before release.** A CI job on a clean Windows runner
  builds the app and the setup program, installs it into a temporary directory,
  checks that the engine script shipped, runs it, starts the app and asks it for
  health, then uninstalls and confirms nothing is left behind.
- **A running changelog** (`CHANGELOG.md`) with a truncated copy at the bottom of the
  README; a test fails when the copy is stale, so it cannot drift from the code.
- `scripts/build_installer.ps1` builds and refuses to hand over an installer that is
  not the version in the tree - the mistake that once reinstalled the previous build
  while reporting success.
## [0.2.0-alpha] - 2026-11-17

### Added

- **FLUX.2 stills run on an 8 GB card.** A quantized (q4) transformer option brings
  the resident footprint from ~15 GiB to ~2.6 GiB, with the text encoder moving to
  the CPU when the card is small. Measured on an RX 9070 XT: 41 s for the first
  still, then about 3 s each.
- **Engine profiles** in Settings — `lite`, `balanced`, `max`, `cpu` — each stating
  what it costs in memory, with a **Restart engine** button so a switch takes effect
  without restarting the app.
- **One-click engine setup in the installer.** The setup program offers "Set up the
  stills engine": it creates the Python environment, picks the right torch wheel for
  an AMD or NVIDIA card, downloads the ~2.4 GB transformer and the detectors, and
  writes the profile for the hardware it found.
- **A stills polish pass**, on by default: each painted still is redrawn once to
  settle halftone mush and tighten faces, gated by face and person detection so a
  frame without people costs nothing. Every still records what the pass did.
- `/api/health` and the Settings panel now report the active profile, the painter's
  last job (seconds, size, steps, peak and free VRAM, free RAM), and cache hit rates.

### Changed

- **The resident painter loads the model once per session** instead of once per
  still, and survives an out-of-memory error by moving the text encoder to the CPU
  and retrying rather than dying.
- Repeat reads are served from memory: a translated sentence now costs 0.005 ms
  instead of 1.1 ms, and a cached clip's metadata is not re-read from disk.
- The app writes `app.log` and `painter.log` in its data directory. A windowed build
  used to swallow its own diagnostics entirely.

### Fixed

- **A still that already existed was being repainted, rewritten and cropped** on
  every chapter render — roughly a minute per still, and destructive: one still went
  from 2.65 MB to 505 KB across three renders. A cache hit is now inert, and the
  panel-splitting detector requires a genuinely uniform band between ink.
- **An orphaned painter could pin 13 GiB of VRAM** after the app closed. Every
  painter process now dies with the app.
- **Importing a PDF failed with "internal error"** — the temporary file was handed to
  the PDF reader while still open, which Windows forbids.
- **HTML and URL imports failed in the installed app** because two of the text
  extractor's dependencies were never packaged.
- **Screenshots and scanned pages failed on Windows** — the OCR call needs its own
  event loop when it runs inside the web request.
- RTF imports showed the font table in the document title.
- The installer could appear to install while leaving the previous version in place;
  the build now refuses to hand over an artifact that is not the current version.

### Security

- The painter refuses to start when system memory or VRAM is below its floor, naming
  the number that was too small. Three hard system locks in one day came from loading
  the model on a machine that was already full.

## [0.1.0-alpha] - 2026-11-16

### Added

- First public source drop: local read-aloud with Kokoro-82M, a book view with
  word-level highlighting, on-device OCR, translation, manga stills, chapter video,
  20 themes, Quick Read, and a Windows installer.
