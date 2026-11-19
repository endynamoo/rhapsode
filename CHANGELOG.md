# Changelog

Notable changes to Rhapsode, newest first. Versions are the app's own
(`murmur.__version__`); `known-good-*` tags are checkpoints of the `stable`
branch and do not get a heading here.

The format follows Keep a Changelog. Add an entry under `## [Unreleased]` in the
same commit as the change, grouped Added / Changed / Fixed / Removed / Security and
phrased for a user, not for a diff reader.

## [Unreleased]

### Added

- **Repair faces** is a new opt-in pass under Polish stills: each detected face is cropped, upsampled to 1024 px, re-drawn at a strength that can actually rebuild an eye, blended back through a feathered mask, and kept only if the repaint still contains a face and the region kept its ink - otherwise that face is restored from the copy taken before it. Two faces per still by default, `MURMUR_POLISH_FACE_MAX` to raise it. **Off by default**, because a crop repaint can redraw a character's face into someone else. `tests/test_face_repair.py` covers the crop maths, the cap, both revert paths and the cache variant without needing a GPU.
- A **26-second demo cut** for the landing page and the README, from `scripts/record_quick.py`: a title card that settles in, then one sentence of the book read aloud in **English, Spanish and Japanese**, then the manga view showing a painted scene and the review grid with three finished stills. The reading clip is the feature rather than a montage: each voice is selected on camera and the app translates the sentence into that voice's language before it speaks, with the translated line mirrored word by word under the player. Three takes are recorded separately, the waiting between a voice change is cut away, and they are joined with hard cuts, so every spoken word survives and the dead air does not; the language labels are lettered from an ASS file because a Windows command line cannot carry an em dash or a kana intact. Scenes are painted before the camera rolls, so the manga clip only ever shows finished art, and the audio is the app's own `/api/tts` output, placed where it played. `tests/test_record_quick.py` fails if the demo story stops planning three scenes, if the sentence it speaks stops being the reader's own first speech unit, or if the three languages collapse onto fewer than three voices.
- `scripts/record_demo.py` records the demo from the app itself: it boots its own server on a free port with a throwaway data folder, seeds a short book, paints the first still before the camera rolls, then drives library -> reading -> search -> Manga view -> Settings with Playwright and writes `dist/demo/demo.mp4` **with the voice**, a muted copy for autoplay, and six stills. Playwright records pixels only, so the audio is tapped off the app's own `<audio>` element through WebAudio and shifted by the measured delay, which puts the speech on the highlighted sentence. Re-run it after a release instead of re-shooting by hand: `python scripts/record_demo.py --models-from <folder with kokoro-v1.0.onnx>`.
- `docs/ARCHITECTURE.md` ends with **the decisions and the roads not taken**: loopback HTTP instead of Electron/Tauri, SQLite + FTS5 instead of a vector database, no telemetry, weights downloaded rather than bundled, the painter as a child process with a lock and a budget, polled jobs, one segmentation shared by speech/highlight/film/search, and the changelog living in the repository — each with what was rejected and what the choice costs.
- A browser smoke suite (`tests/ui/test_smoke.py`, Playwright) covering the library, opening a book, search, the theme dropdown, the Auto Generate switches and adding a document through the modal. It starts its own server on a throwaway data directory, runs in CI on Linux, and skips itself when playwright is not installed.
- **Auto Generate** in Settings (off by default): once you start a chapter film, the remaining chapters queue after it, one at a time, without a confirmation per chapter. A **Stop auto-generation** button drains the queued chapters and leaves the one already painting alone.
- Cinema reports what the stills engine is actually doing — planning, loading the model, painting, cleaning up, polishing, or showing a placeholder — instead of one static "Painting scene…", with the backend, model and profile on screen and a Retry button when a scene fails.

### Changed
- The first manga still now arrives **sooner, and says what it is doing**. Two
  changes, both measured rather than assumed. Checking whether the stills engine is
  installed used to launch the image interpreter and import torch and diffusers,
  which cost **6.0 s** of every cold first still to answer a question a directory
  listing answers in a millisecond; it now reads the environment and only falls
  back to importing when it finds a layout it does not recognise. And opening
  Cinema - which asks for the plan before any scene is clicked - now starts loading
  the engine while the reader is looking at the plan, instead of after the click.
  Settings -> Stills engine gains a **Load the engine when Cinema opens** switch
  (on by default) for machines where holding the GPU is unwelcome.

- Opening a book now **synthesises the sentence on screen in the background**, so
  pressing play starts almost immediately. The cost was always the voice model, not
  the network: a unit of about four seconds of speech takes roughly 0.8 s of CPU to
  render, and it was paid after the click, which made it the whole time-to-first-
  sound. Measured in the app's own browser journey on this machine: **1257 ms**
  from pressing Listen to the first audio frame before the change, **75 ms**
  after. A browser test asserts that opening a book issues the `/api/tts`
  request with no play interaction.
- The Settings panel moved out of `web/app.js` into `web/views/settings.js`, and the browser now loads `app.js` as an ES module. No bundler, no new build step, and no behaviour change: the view receives `$`, `$$`, `api`, `state` and the few helpers it needs instead of reaching into the module scope, so what it depends on is visible at the top of the file. This is the first slice of the UI split (#36); the browser suite grew a test that fails if the panel stops being wired.
- Startup work moved from FastAPI's deprecated `on_event("startup")` hook to a `lifespan` on both the server and the engine sidecar. Same calls, same order - store init, the voice model warm-up, the cache-prune thread, search indexing, the network banner - but a start no longer prints a `DeprecationWarning`. `tests/test_lifespan.py` holds the behaviour: no app registers an `on_event` handler, importing one raises no deprecation, and entering the app's context leaves an initialised store behind.
- The install and build guide is now **[INSTALL.md](INSTALL.md)**, written for a person rather than an automated agent: support status, per-platform prerequisites, GPU notes, verification steps and troubleshooting, unchanged in substance. `AGENTS.md` keeps the rules an agent must work to.
- The README now reads as an argument rather than a feature list: what it does, **the parts that were actually hard** (phoneme-timed highlight, the two-lane search index, the painter's memory budget, one loopback server), how it was built, and then how to try it — with a link to Releases for the prebuilt Windows installer and macOS DMGs.
- The server's system routes (health, engine profile, settings, voices, fonts, speech, audio, the event stream, quick read and clipboard OCR) moved out of `server.py` into `murmur/system_api.py`, and the "is this caller local, and which token did it present" logic into `murmur/access.py`.
- The `/api/docs` routes — add text, upload a file, import a URL, PDF page images and word boxes, EPUB chapters and assets, patch and delete — moved into `murmur/library_api.py`, with the PDFium lock and the chapter theming helpers. `server.py` is 1,080 lines down to 253: the app, the access guard, audio export and the static mounts. No route, status code or response body changed by either move.
- Auto Generate is two switches instead of one: **Scenes** (on by default — stills are painted a few scenes ahead while you read) and **Chapter films** (off by default — the remaining chapters queue when one finishes). An install that had the old single switch on keeps chapter films on.
- Theme is a dropdown in Settings — grouped Dark and Light, with a live preview of the selected palette beside it — instead of a scrollable grid of twenty swatches.
- Settings → Engine is now **Stills engine** and reads the same activity object as Cinema, so the two can no longer disagree about what is running.
- A scene that falls back to the placeholder is treated as delivered: it is shown, counted as done, and labelled, rather than re-planned on every poll.
- The in-app mark now uses the purple app icon's palette instead of white strokes on near-black, and its halo is purple rather than cyan, so the sidebar, the empty state, the favicon and the installed icon read as one brand.

### Fixed

- A polish pass no longer aborts when **opencv** is missing. `detectors.faces()`, `cv2_resize()` and the pose path imported `cv2` directly, so on a source install without it a `ModuleNotFoundError` escaped `detectors.inspect()` instead of being reported in its `error` field - the still then failed to polish at all, rather than polishing with the global pass only. Every cv2 use now goes through `detectors._cv2()`, which raises `DetectorUnavailable` with a reason the pass already knows how to record. Found by CI on a runner without opencv, not on a dev box. `detectors._session()` does the same for a missing **onnxruntime**, so the person and pose detectors report unavailability instead of raising it.
- The line under the book title in the player no longer reads *"Add a document to begin"* while a book is open. An unplayed book says *"Press play, or click any paragraph to start there"*, and a finished book says it is finished, instead of the empty-library prompt staying on screen until something is spoken.
- The Stills engine panel no longer says *"below the RAM floor - painting will refuse"* while the engine is resident and painting: the floor is a **cold-load** guard, and a resident worker holds the model, so free memory sits under it by design. It now only warns when no worker is running, which is the only time a load can be refused.
- Settings no longer reports the stills engine as **idle** while it is starting. During a cold load the panel read *"stills engine idle · Loading the stills engine — first still this session"*: the worker is not yet `running` while its weights load, so the summary claimed idle and the activity line immediately contradicted it. The summary now says *"stills engine starting"* for as long as a phase other than `idle` is reported and the worker is not up yet.
- Stills could not be painted from a **source install** at all: the painter was started as `python murmur/fluxgen.py`, which puts `murmur/` on `sys.path`, so `import profile` inside torch resolved to `murmur/profile.py` and the child died with `module 'profile' has no attribute 'run'` before it could load a model. The frozen build ships `fluxgen.py` where no sibling `profile.py` exists, which is why only source installs were affected. The painter (resident and one-shot) now runs with `-I`, isolated from the script directory and from user site-packages.
- The **Add document** panel no longer steals the first keystrokes from the title. `openAdd()` moved focus into the paste box from a 40 ms timer, so anyone who clicked the title field and started typing had those characters pulled into the text box and their document saved as *"Untitled"*. Focus now moves on the next frame and only when the user is not already in a field.
- Playback no longer warns "translation is not set up" when nothing needed translating. A new `murmur/language.py` reads the sentence's language first — script ranges decide Japanese, Chinese and Hindi, a function-word profile separates English, Spanish, French, Italian and Portuguese — so an English book read by an English voice never asks for a translation, and the warning appears only when the text really is in another language. `scripts/verify_translation.py` checks every voice language against a running lane.
- A voice model installed while the app was open no longer leaves the sidebar on "Loading voice model…" forever: the engine re-checks the weights, and if they are genuinely missing the pill says so and names the fix instead of spinning.
- RTF import falls back to the built-in reader when `textutil` cannot parse the file, so an unclosed RTF no longer arrives as an empty document.
- A placeholder scene says **why** it is a placeholder instead of blaming the machine. `imaging.placeholder_reason()` distinguishes the three causes a reader can act on: no stills engine in this data folder (naming the interpreter path and `scripts\setup_image.ps1`), an engine that exists but cannot import torch and diffusers, and a Mac build shipped without the stills helper. The sentence appears on the Cinema chip's tooltip and in Settings → Stills engine, where someone goes looking for the fix.
- The Windows installer's two task checkboxes share one left edge; splitting them into two group headings had pushed the stills row out of line.
- On macOS the "translation is not set up" warning names the real cause — Apple Translation needs macOS 26, or this build shipped without the Swift helper — instead of telling the reader to set an environment variable they have no reason to know.

### Removed

- `launch.bat` and `zip_and_relaunch.ps1` are no longer tracked. They were local shortcuts, and a repository someone is meant to read should not open with two scripts that only work on the author's machine.

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
