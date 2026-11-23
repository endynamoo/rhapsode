# Changelog

Notable changes to Rhapsode, newest first. Versions are the app's own
(`murmur.__version__`); `known-good-*` tags are checkpoints of the `stable`
branch and do not get a heading here.

The format follows Keep a Changelog. Add an entry under `## [Unreleased]` in the
same commit as the change, grouped Added / Changed / Fixed / Removed / Security and
phrased for a user, not for a diff reader.

## [Unreleased]

- **Audio you can actually play, and one sentence in three languages.** The demo now includes
  `docs/demo/audio/one-sentence-four-voices.mp3` (one sentence read in English, Japanese, Spanish
  and French, the last three translated locally) and `reading-sample.mp3` (26 seconds of the demo
  book), both produced through the app's own `/api/tts` and published as files because GitHub
  renders no media from a repository. A three-panel clip shows the same sentence with the Japanese,
  Spanish and French bar, so the front page still shows the feature working.


### Added

- **A fresh-install check that runs the artefact instead of the checkout.**
  `scripts\\fresh-user.ps1` gives the installer a brand-new profile - its own
  `LOCALAPPDATA`, `APPDATA`, `USERPROFILE` and `TEMP`, no models, no stills engine - then
  installs, starts, asks it what a first-time user would, quits, uninstalls, and proves
  nothing was written outside that profile and that the real uninstall entry was restored.
  22 checks in about two minutes; `-FullFirstRun` includes the 340 MB voice-model
  download. It exists because "works on my machine" is not evidence: this development box
  has the weights, the image environment and a populated data directory that no stranger
  has.

- **Five demo clips, recorded from this build.** The README now shows the reader marking the
  spoken word, a scanned PDF page marked on the page itself, a library search across three
  books, a chapter read over a still the stills engine painted, and an English sentence read
  in Japanese through a local translation server - the last one with sound, muxed from the
  audio the app actually played. The GIFs sit in the README, and every clip's full-motion MP4,
  the demo corpus and the recording method live in `docs/demo/`, so a clip can be reproduced
  rather than trusted.

- **A Quit control, for browser mode.** Closing a tab never stopped the server, so the
  voice model - and, once Cinema had been opened, the stills engine holding most of the
  card - stayed loaded with nothing on screen to say so, and there was no endpoint to
  ask it to stop. Settings now has **Quit Rhapsode**: it asks once, then releases
  everything and stops the server. It refuses requests from other devices, so a phone on
  the network cannot stop the app.

### Changed

- **The README opens with the clips.** "In motion" - the five recordings of this build -
- **Linux and macOS free the models the same way Windows does.** There is no job object
  on POSIX, so every long-lived child is now started in its own session and told its
  parent's pid, and the stills painter watches that pid: when the app disappears -
  including when it is killed in the middle of painting a tile - the painter exits
  instead of holding the card. Verified on Linux: a parent killed with SIGKILL, a child
  started by the app's own `proc.spawn`, gone within a second. A painter started by hand
  is unaffected.

- **The README says who the app is for**: somebody turning anything they can read into an audiobook, somebody who likes generated manga stills, and somebody who wants to lift the parts into a different program. It now also says what to expect from pull requests (bugs first, reviews slow) and where the family of apps is going, including what the next one does (Opsis turns a book into a film of directed, generated and polished scenes; an alpha is running, and the open beta is targeted for before the new year) - and it replaces "the parts that were actually hard" with what each technology in the build is and why this mix works: the voice returns phoneme timings, so the highlight, the warm-ahead, the still beats and the MP4 cut are all read off the same clock instead of being guessed. It also corrects two stale claims: a platform table that still promised CPU stills, and a version badge stuck one release behind.

### Fixed

- **An old CPU engine profile no longer leaves the stills engine switched off for good.**
  0.3.0 offered a CPU profile that turned every scene into a placeholder; the button is gone,
  but the saved `engine.json` stayed, and Settings blamed the hardware instead of saying the
  machine was set to CPU. The profile is now reconciled on start - `cpu` on a machine whose
  card can paint becomes the recommended profile, the file is rewritten so it does not come
  back, and the engine panel says once what happened. A machine that really has no GPU keeps
  `cpu`.
- **`build_app.sh` creates `build_assets/` before the Swift helpers write their logs into
  it.** On a fresh clone both redirects failed, so the warning explaining *why* a helper did
  not build had nothing to print - the reason was the one thing worth keeping.

- **The Japanese and Chinese voices are an optional extra, and say so.** `pyopenjtalk`
  ships only as source, so a source install had to compile it: a contributor who picked a
  Japanese voice was told by pip to install Microsoft C++ Build Tools, which nothing here
  documented. `requirements.txt` no longer pulls it; `requirements-lang.txt` adds `misaki`
  and `pyopenjtalk` for those two languages, and the installers already ship them. Without
  the extra the app runs normally and every surface that offers the voice says why:
  `/api/health` reports each language's availability, the voice picker shows
  *(not installed)* and disables those options with the reason on hover, and speaking
  anyway returns that sentence instead of a compiler traceback. `python -m murmur.bootstrap`
  also fetches the 23 MB Open JTalk dictionary, so a downloaded install speaks Japanese
  offline instead of reaching for GitHub at the first sentence.

- **The missing-model message is something an installer user can follow now.** A packaged
  build told them to run `python -m murmur.bootstrap`, which does not exist in a frozen
  install; it points at Settings -> Voice model -> **Download it again** instead, while a
  source checkout keeps the command. Found by the new harness on its first run.

- **The README stopped shipping broken characters.** A text round-trip through Windows'
  legacy encoding had double-encoded the punctuation in 44 lines of the front page - every
  em dash, the multiplication sign, the middle dots, the `§` in a cross-reference - so a
  reader saw three-character debris where a dash belonged. The same damage was in the
  stills module's comments and labels and in the installer's first line; all 51 places are
  repaired byte for byte, with no other change to those files. `tests/test_encoding.py`
  now fails if any shipped text file is not plain UTF-8, carries a BOM, loses a character,
  or contains a double-encoded run - and it is checked against the exact string that
  shipped, so the check itself cannot rot.

- **Quitting gives the models back.** The stills painter is a child process holding most
  of the card, and it was supposed to be reaped by the operating system through a
  kill-on-close job object. That job was never created: `murmur/proc.py` passed Windows a
  64-byte struct where the API requires 144, then read the returned 64-bit handle as a
  32-bit integer, and both failures were swallowed. So ending the app mid-paint left an
  orphan: measured at 5,156 MiB of dedicated GPU memory still held, plus a stale
  `flux-painter.lock` that stopped the next session from painting at all. The job is
  created now and checked, a child dies with the app even in the middle of a tile, and a
  deliberate quit releases the painter, the voice model, the indexer and the database in
  that order - from the lifespan, from the quit route and from the desktop entry.

## [0.3.4-beta] - 2026-11-19

### Added

- **Licensing now matches what actually ships, and the build is compliant**: `THIRD_PARTY.md` called YuNet MIT (it is Apache-2.0), blamed the MIT `espeakng-loader` for eSpeak's GPL-3.0, and omitted the copyleft inside wheels (libsndfile, libheif, 41 MB of OpenCV FFmpeg we never call). Every download now also carries a `licenses/` folder: the GPL-3.0 and LGPL texts, OpenCV's notice, a `NOTICE.txt` the installer shows, and `SOURCES.md` with versions, upstream links and the written offer. `tests/test_licences.py` keeps it true.
- **Run the CI workflows locally, Linux included**: `scripts\ci-local.ps1` mirrors `.github/workflows/` job for job - the suite in a venv built from `requirements-test.txt` alone, the browser journeys, a real server asked for `/api/health`, and the PyInstaller + Inno cycle with a silent install, the `licenses\` check and the uninstall. `-Job linux` runs `linux.yml` inside WSL through `scripts/ci-local-linux.sh`: the suite, a real server asserting the **Tesseract** OCR backend, and the browser journeys. For when Actions minutes run out. The macOS job needs a Mac and reports itself skipped, never passed.

### Changed

- **A hidden search note removes its own progress bar**, so nothing from the meaning-model download can be left painted above the results.
- **Repository housekeeping**: the source-tarball script works from any checkout, six files lost their byte-order marks, `.gitignore` covers a contributor's caches, and a workshop script plus a stale generator claim are gone.

### Fixed

- **First-run setup no longer dies on a plain console**: the download progress and its tick marks were printed as Unicode, which a legacy (cp1252) console cannot encode - the download aborted with an encoding error while reporting itself. The text now degrades to whatever the console can print.
- **Follow along now works in Book view**: both scroll paths ignored the switch: the chapter highlight kept the spoken sentence in sight and the advance to the next one scrolled regardless, so turning it off changed nothing. A sentence you jump to is still brought into view.
- **The translated-sentence bar no longer covers the sidebar**: centred on the window it landed on the theme and settings buttons and swallowed their clicks. It is anchored to the reading column and takes no pointer events.
- **Export asks where the file should go**: the save dialog comes first, the export is written to the chosen file, and the copy under the data folder is removed once the reader has it. Browsers that cannot choose a path say which folder the download went to.
- **A voice model file that is a stub is now named and repaired**: a 4-byte `kokoro-v1.0.onnx` passed every "does the model exist" check, so play threw a raw ONNX error and `python -m murmur.bootstrap` reported nothing to do. The engine, the downloader and `/api/health` now share one size check, bootstrap re-downloads a bad file, and Settings offers **Download it again**.
- **Dark themes: the voice dropdown opens a dark popup** instead of the platform's light default, because its options are painted with the theme's colours.
- **The Search dialog's footer sits inside the card**, so the keyboard hint and the button no longer touch the border.
- **HEIC imports work in an installed build**: the frozen app never bundled `pillow-heif`, so a `.heic` file opened from an installer would be refused while the same file worked from a source checkout.
- **The README badge caught up, and so did three visible messages**: the badge said 0.3.2 while the app said 0.3.3, and the setup banner, the private-access notice and an untitled chapter's exported film still said Murmur - that film now exports as "Rhapsode - chapter 1".

## [0.3.2-beta] - 2026-11-19
### Added

- **Highlight the spoken word** is now a switch in Settings (on by default), covering Book view and translated text. Manga never highlights — there are no words on screen to follow — so it stays still whatever the switch says.

### Fixed

- A damaged or mislabelled file is now **refused with a sentence** instead of a bare 500, and every setting keeps to its own type and range, so a playback rate arriving as text cannot break playback.
- **A scanned page now marks the word on the page itself**: the reader asks the on-device recogniser where each word sat, then draws the mark there. Text-layer pages never call it.
- Every sentence now starts with its first word already marked. The clip is held
  until it can decode, its opening word is highlighted, and only then does the
  voice start, so a sentence the reader reached by finishing the one before it can
  no longer begin with the sound a frame or two ahead of the highlight.
  `tests/ui/test_smoke.py` records what the page looks like at the instant the
  player is asked to start, and fails if a sentence has no word marked then.

- The word highlight is bound to the **clip that is actually playing**: each sentence's timings are stored beside the audio it was made for, so a sentence still synthesising while the previous one finishes can no longer lend its word times to the next one under a slow machine. If the highlight still drifts behind the voice after this build, that is a separate measurement and the recording of it is welcome.

## [0.3.0-beta] - 2026-11-19
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

- The Linux CI job **retries its package install** instead of reporting the runner's
  mirror as a test failure. Two of this repository's CI failures were `apt-get`
  fetching from `security.ubuntu.com` - once a long stall, once "Connection failed" -
  so the step now asks for five transport retries, makes three attempts of its own,
  and prints the `ffmpeg` and `tesseract` versions it ended up with.
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

- **The ladder scripts never open a credential dialog.** `check-ladder.ps1` and
  `promote.ps1` now take the token sentinel already holds instead of reaching for the
  credential manager, which on a machine driven by agents popped a window nobody could
  answer and hung the command. `check-ladder.ps1` also keeps going when it cannot fetch
  (saying so, and comparing the refs the clone already has) and reads its CI column
  through sentinel's read token instead of the deliberately logged-out `gh` CLI.
- A scene that failed and was then repainted no longer keeps its old error. Cinema's
  **Retry** paints in the request thread, so the still landed on disk while the queue
  still held the earlier failure: the panel reported **"Scene 1 ready"** and
  *"refusing to paint: only 2.3 GiB of 31 GiB system memory is free"* at the same
  time, under a still that was right there. A recorded failure now stops counting
  as soon as the scene has a still to show for it.
- **Remote access no longer calls this machine a Mac.** The row read "Off — this
  Mac only", the Tailscale note blamed "this Mac", and five modules filtered the
  advertised addresses by the literal label `This Mac` — which is wrong on
  Windows, where remote access is a first-class path. The label is now
  `netinfo.LOCAL_LABEL` ("This computer") in one place, the panel says "this
  computer", and the token note points at *the app's data folder* instead of the
  macOS-only `~/.murmur/access_token`. A test keeps the shared surfaces free of
  operating-system names.
- **Assets are revalidated instead of cached by heuristic**, so a reload cannot keep
  running an old `app.js` or view module after the files on disk changed. The
  browser was free to reuse whatever it already held, which made an update look
  like it had not been applied - it is how a shipped fix went missing during the
  0.3.0-beta cut. A local file answers the conditional request with `304` in well
  under a millisecond.
- The **Reading font row is never blank** while its list loads. Enumerating the
  machine's fonts is a scan, and the control used to stay empty until the answer
  arrived: a first-time reader opening Settings saw a nameless dropdown that reads
  like "this machine has no fonts". It now shows the saved font from the first
  frame and swaps in the installed families when they arrive.
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

- **`docs/CONCURRENCY.md` is no longer part of the repository.** It is a note about running
- **The CPU stills profile is no longer offered**: it could not paint anything - every scene came back as a placeholder - so choosing it read as a failure rather than a choice. Reading, narration and export are unaffected. CPU painting is planned for the 0.4 refactor.
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
