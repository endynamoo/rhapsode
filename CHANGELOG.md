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

- Troubleshooting page in the app: the three guards, where the logs live, and what was already fixed.
- The RAM probe the painting guard calls, and the RAM it measured shown in Settings.

### Changed

- The README front page states what actually ships: the per-user Windows installer, ROCm stills, and the real test count.
- The generated changelog block no longer repeats the "Changelog" heading above it.
- `AGENTS.md` is plain UTF-8 text again, so its arrows and dashes render as arrows and dashes.

### Fixed

- Painting refuses to start without enough free system RAM, and reports what was left instead of dying mid-still.
- A resident painter survives an out-of-memory kill instead of dying, and is reaped with the app, including the one-shot fallback.
- A still that is already painted is never repainted on a later session.
- Both halves of the app log to file, because the window and the server have each died silently.
- The engine switch posts JSON — the shared api helper sets no content type.

## [0.2.0-alpha] - 2026-11-17

### Added

- Named engine profiles (`lite`, `balanced`, `max`, `cpu`) with auto-install, and an Engine panel in Settings: profile summary, painter status and Restart engine.
- Stills polish pass: pose detection, a masked redraw of the hands, off by default, reported in `/api/health`.
- q4 GGUF transformer path — FLUX.2 klein on an 8 GB card, precision tier chosen from free VRAM.
- Resident painter: one model load per session, guarded by a lock that survives restarts.
- Instrumentation for the painter and the model caches; health reports the painter's precision.
- Measured q4 numbers and per-card precision tiers in the docs.

### Changed

- Hot caches are memory-resident and the database connection is shared.
- The installer says when to prefer the shared-card profile.

### Fixed

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
