# Rhapsode

A Speechify-style read-aloud and manga-film app for macOS that runs entirely on your Mac.
Powered by [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) — Apache-2.0,
82 million parameters, no cloud, no account, no per-word billing.

![status](https://img.shields.io/badge/TTS-Kokoro--82M-f5a26b) ![license](https://img.shields.io/badge/model-Apache--2.0-green)

## What it does

- **Reads anything** — paste text, drop a **PDF / EPUB / DOCX / RTF / TXT / Markdown**
  file, an **image**, or paste a URL and it strips the article out of the page.
- **OCR built in** — scanned PDFs and images are transcribed on-device by Apple's
  Vision framework. No Tesseract, no model download, no network.
- **Book view** — one reading mode adapts to the file: PDFs and images render
  page by page, EPUBs render their actual chapters, and text files use the
  reader. Word highlighting follows the voice in every rendition; PDF pages
  overlay the word boxes from the PDF text layer, with a caption fallback for
  scanned pages.
- **Contents** — a chapter list in the sidebar, built from the EPUB's own
  navigation or a PDF's bookmarks, that jumps the voice straight to a chapter.
- **Translation** — pick a Spanish, French, Italian, Portuguese, Hindi, Japanese,
  or Mandarin voice and every sentence is translated on-device before it is
  spoken, with the translation shown under the player.
- **Manga + chapter films** — each beat of the book gets a locally generated
  still, animated and subtitled. Watch it live, or export a chapter as MP4.
- **Word-level highlighting** — timing comes from Kokoro's phoneme duration
  output, not a guess, so the highlight tracks the voice.
- **Gapless-ish playback** — the next sentences are synthesized while the current
  one plays, so there is no visible wait.
- **54 voices, 9 languages** — American/British English, Spanish, French, Italian,
  Portuguese, Hindi, Japanese, Mandarin.
- **Speed control** 0.75×–3× with pitch preserved.
- **Remembers your place** in every document, plus voice and speed per document.
- **Export to MP3** — turn a document into an audio file.
- **Quick Read** — global hotkey that reads whatever is selected in any app.
- **20 themes** — ink, paper, cream, Nord, Tokyo Night, Dracula, Gruvbox, high
  contrast, and more. Palettes recolor the reader, controls, and EPUB chapters
  together.
- **Your fonts** — the reader lists the font families actually installed on the
  machine (CoreText on macOS, the registry on Windows, fontconfig on Linux) and
  applies the choice to text and EPUB chapters.
- **Private** — audio is synthesized and cached locally. Nothing leaves the machine.

## Documentation

- [Agent install guide](AGENTS.md) — step-by-step build/install instructions for
  macOS, Windows, and Linux that another agent can follow.
- [Architecture and UML](docs/ARCHITECTURE.md)
- [Cross-platform and GPU plan](docs/PLATFORMS.md)

Rhapsode currently ships for macOS. The platform plan covers Windows and Linux
builds, including NVIDIA (CUDA), AMD (DirectML/ROCm), Intel (OpenVINO/DirectML),
and CPU-only fallbacks.

## Quick start

Requirements: macOS on Apple Silicon (works on Intel too), [uv](https://docs.astral.sh/uv/),
and about 1 GB of disk for the model + dependencies.

```bash
cd "~/Documents/Default Project/murmur"
./run.sh
```

First run creates `.venv`, installs dependencies, downloads Kokoro-82M (~340 MB),
and opens <http://127.0.0.1:8765>. Later runs start in about a second. The Manga
dependencies (PyTorch + diffusers, ~2.5 GB) are installed too; set
`MURMUR_SKIP_FILM=1 ./run.sh` to skip them on a small disk.

To stop it, press `Ctrl+C` in the terminal (or close the tab and kill the process).

## Run it as a Mac app

Rhapsode also builds as a real `.app`: a native window (WKWebView) with the server
and the Kokoro weights inside the bundle. No browser, no terminal.

```bash
./build_app.sh
open dist/Rhapsode.app
```

The first build installs PyInstaller and pywebview, renders the app icon, and
bundles the weights from `~/.murmur/models` (the app ends up around 1.4 GB: model,
PyTorch/Metal, translation and prompt helpers, and Japanese/Chinese dictionaries).
Set `MURMUR_BUNDLE_MODELS=0` to skip that and read `~/.murmur/models` at runtime.

The bundle is ad-hoc signed for local use. It is not notarized, so if you copy it
to another Mac, right-click → Open the first time (or
`xattr -dr com.apple.quarantine Rhapsode.app`).

The app reuses an already-running Rhapsode on port 8765 (so it works alongside the
dev server and the Quick Read hotkey); otherwise it starts its own server on the
first free port. `dist/Rhapsode.app/Contents/MacOS/Rhapsode --selftest` checks that
the packaged server, weights, and web assets all load.

## Foreign voices and translation

Kokoro voices are language-specific: the Spanish voice only knows Spanish
phonemes. Rhapsode therefore translates each sentence into the voice's language
before speaking it, and shows the translated sentence in a strip above the player.
The strip gets its own word-level highlight, and the page keeps a proportional
word cursor derived from the translated timing, so the render still follows along.

- Detection, translation, and the language switch all run on-device through
  Apple's Translation framework. Nothing is sent anywhere.
- Source language is detected automatically, so a Spanish PDF read by an English
  voice is translated into English too.
- Only sentences that need it are translated. Results are cached in the library
  database, so replaying a book costs nothing.
- Japanese and Mandarin are phonemized with misaki (`pyopenjtalk` / `jieba`),
  because Kokoro's default espeak path either slows Japanese down badly or
  cannot read Chinese at all.
- Turn it off with **Settings → Translate to voice language** to hear the
  original text read with a foreign accent.

Apple needs a language pack for each direction. If one is missing, Rhapsode falls
back to the original sentence and tells you to add it in **System Settings →
General → Language & Region → Translation Languages**.

## Manga and chapter films

The **Manga** view turns narration into monochrome manga key images. Each
60–90 seconds of narration gets one still (about 4–8 images for a typical
chapter), shown with a calm crossfade and a constant slow zoom. **Create chapter
film** renders the same beats to a 1920×1080 MP4 with 1.2-second dissolves and
subtitles.

- **Key images, not every sentence.** The planner groups 3–20 sentences into one
  representative beat, so a 7-minute chapter is a handful of images instead of
  dozens. Subtitles still follow every spoken sentence.
- **Painted ahead, then served.** A background worker renders key stills into
  `~/.murmur/film/<doc>/scenes/`. The Manga view serves cached frames and shows
  a “Painting scene…” pill otherwise.
- **A cast per book.** The prompt writer extracts the recurring cast once; each
  character gets a clean full-body portrait on white. The film anchors to the
  portrait of the character named in the beat, using the previous frame only
  between anchors so faces and costumes do not drift.
- **Shot variety.** Framing rotates through establishing wide, medium two-shot,
  close-up, over-the-shoulder, low-angle and detail inserts instead of defaulting
  to a wide low-angle shot every time.
- **Monochrome, cleaned.** Every still is converted to true grayscale, blank
  panel edges are trimmed, and split spreads are collapsed to one panel. If OCR
  finds speech bubbles or lettering, FLUX edits only that frame to remove the
  text while keeping the composition.
- **Local only.** High quality uses **FLUX.2 klein 4B** (Apache-2.0) through
  **mflux** (MIT) at 1920×1080, four steps. Fast uses Sana 0.6B at 1280×720.
  Switch with **Settings → Manga quality**.
- **Steady motion.** No alternating Ken Burns direction or panning. Each image
  zooms 1.00 → 1.04 at the centre, rendered at 2560×1440/30 and downsampled to
  1920×1080/30. Beats dissolve into each other over 1.2 s.
- **Background film queue.** **Create chapter film** queues the job and returns
  immediately. You can keep reading, watch Manga, or queue another chapter; each
  job shows Painting → Narrating → Encoding → Ready with its own Download,
  Cancel and Dismiss controls.
- **Watch while painting.** Manga playback starts narration immediately instead
  of waiting for the whole chapter to be painted. The current scene loads on
  demand and the next two scenes are prefetched.
- **Cached re-films.** A chapter film is reused instantly when its plan, images,
  voice, quality, and render settings are unchanged. Changing one still
  re-renders only that chapter.
- **Encoding.** Film export uses the system `ffmpeg` (`brew install ffmpeg`). If
  that build includes libass, karaoke subtitles are burned in; otherwise they are
  attached as a soft `mov_text` track so the film still plays. H.264 + AAC,
  hardware encoded where possible.

Install the high-quality engine once:

```bash
./scripts/setup_flux.sh
```

It creates `~/.murmur/image-venv`, installs mflux, and downloads the 4.6 GB
FLUX.2 klein q4 model. The Murmur engine falls back to Sana if it is missing.

Rendering is now a shorter batch: a 30-minute chapter is roughly 20–30 key
images, typically 45–90 minutes at High on an M3 Pro including text cleanup.
Re-filming an unchanged chapter is instant.


## Murmur engine sidecar

The film pipeline can run as a standalone local service, separate from the
Rhapsode window:

```bash
./scripts/run_engine.sh
# or: MURMUR_ENGINE_PORT=8931 .venv/bin/python -m murmur.engine_api
```

It exposes the same film contract as the app:

- `GET /health`, `GET /version`
- `GET /api/film/plan`, `GET /api/film/images`
- `POST /api/film/scene`, `POST /api/film/scene/regenerate`
- `POST /api/film/prefetch`, `GET /api/film/progress`
- `POST /api/docs/<id>/film`, `GET /api/film/<job>`, `GET /api/film/<job>/video`

The packaged app can also be launched in engine-only mode:

```bash
dist/Rhapsode.app/Contents/MacOS/Rhapsode --engine
```

Rhapsode currently runs the same router in-process by default; the sidecar is
the first step toward a separately packaged Murmur engine for other frontends.

## Access from other devices (Tailscale)

Rhapsode can bind to the network so your phone, tablet, or another laptop can use it.

- **Mac app:** Settings → **Remote access** → on, then restart Rhapsode.
  Or launch it directly with `open dist/Rhapsode.app --args --host 0.0.0.0`.
- **Dev server:** `MURMUR_HOST=0.0.0.0 ./run.sh`.

When the server is not loopback-only, every non-local request must present a
token. The first visit uses a link ending in `?token=…`; the token is then kept
in a cookie for a year. The local window and the Quick Read hotkey never need it,
so the Mac experience is unchanged.

Settings shows ready-made links for each Tailscale IPv4 address, the MagicDNS
name (`your-mac.your-tailnet.ts.net`), and the LAN address. Links are only listed
for interfaces that are actually up: if Tailscale is stopped, Settings says so
and shows the LAN link instead. The secret lives in `~/.murmur/access_token`
(mode 0600) and can be overridden with `MURMUR_TOKEN`. `/api/network` — the only
endpoint that reveals the token — is loopback-only.

Binding to `0.0.0.0` also exposes Rhapsode to the local network, not just Tailscale,
so use it on networks you trust; the token is the gate. To restrict it to the
tailnet, bind the Tailscale address instead: `--host 100.x.y.z`.

## Using it

| Action | How |
|---|---|
| Play / pause | `Space` or the big button |
| Next / previous sentence | `→` / `←` |
| Jump to any sentence | click it |
| Seek | click the progress bar |
| Speed | `[` / `]` or the speed menu |
| Text size | `+` / `-` |
| Add a document | `⌘N` |
| Switch Book / Manga view | `V` |
| Jump to a chapter | `C` (opens Contents) |
| Hide UI / immersive mode | `F` or `Esc` |
| Settings | `⌘,` |

Clicking a sentence starts reading from there. The current sentence stays
bright while the rest of the page dims, and the page follows along as it reads.

The sidebar has two tabs: **Library** and **Contents**. Opening a book or a PDF
with bookmarks switches to Contents automatically; clicking a chapter reads from
that chapter. In Book view the current word is highlighted inside the rendered
chapter, and the chapter scrolls to keep it in sight.

## Quick Read from anywhere

`scripts/quick-read.sh` reads the current selection in whatever app is in front.

1. Open **Shortcuts.app** → **+** → add **Run Shell Script**.
2. Paste the full path to the script:
   ```
   ~/Documents/Default Project/murmur/scripts/quick-read.sh
   ```
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

OCR uses **Apple Vision** (the same engine behind Live Text), so it runs on the
Neural Engine and works offline. `pyobjc-framework-Vision` is the only dependency;
if it is missing, text PDFs still work and scanned ones return a clear error.

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

## How it works

```
web/            vanilla HTML/CSS/JS — no build step
murmur/
  server.py     FastAPI: documents, TTS endpoint, range-capable audio, export, SSE
  desktop.py    native window (pywebview) used by the .app
  translate.py  on-device translation worker (Apple Translation)
  speak.py      decides what the selected voice should say
  engine.py     Kokoro-82M via ONNX Runtime + disk cache
  text.py       sentence segmentation and phoneme→word alignment
  extract.py    PDF/EPUB/DOCX/HTML/URL/image extraction
  ocr.py        Apple Vision OCR: images, PDF pages, clipboard
  store.py      SQLite library and settings
  voices.py     voice catalogue and language mapping
  bootstrap.py  one-time model download
packaging/      PyInstaller spec, entry point and app icon
```

- **Sentence units.** Text is split on real sentence boundaries with abbreviation,
  initial and decimal protection. Tiny fragments (headings, `"Yes."`) are merged
  into a neighbour because Kokoro sounds worse on very short inputs, and overly
  long sentences are split at clause punctuation.
- **Timing.** `kokoro-onnx` exposes per-phoneme start/end times. Phonemes are
  folded into word groups (the phonemizer emits a space phoneme between words) and
  mapped onto the original tokens — exact for normal prose, proportionally
  interpolated for numbers and dates that expand into several spoken words.
- **Speed** is applied in the browser with `preservesPitch`, so changing speed is
  instant and never re-synthesizes.
- **Cache.** Clips are keyed by `sha1(model version + voice + text)`, written
  atomically to `~/.murmur/cache`, and evicted LRU-style at 2 GB.

## Data

Everything lives in `~/.murmur`:

| Path | Contents |
|---|---|
| `models/` | `kokoro-v1.0.onnx`, `voices-v1.0.bin` |
| `murmur.db` | document library, positions, settings |
| `cache/` | synthesized WAV clips |
| `cache/pages/` | rendered PDF page images |
| `files/` | original PDFs and images (for the page view) |
| `exports/` | exported MP3s |

Delete that folder to reset. Audio cache can also be trimmed from Settings.

Environment variables: `MURMUR_PORT` (default 8765), `MURMUR_HOME`,
`MURMUR_CACHE_MB`, `MURMUR_NO_OPEN=1`.

## Troubleshooting

**First sentence is slow.** The model is loading/warming. The status dot in the
sidebar turns green when it's ready; subsequent sentences are fast.

**Nothing happens on a Quick Read hotkey.** macOS needs Accessibility permission
for Shortcuts. System Settings → Privacy & Security → Accessibility.

**No sound after a Quick Read.** The browser blocked autoplay because the tab had
no prior interaction. Click the “Listen” pill in the app.

**PDF looks garbled.** Scanned PDFs are OCR'd automatically, but a page that is a
photo of handwritten text or a low-resolution fax may come out poorly. Try
`MURMUR_OCR_DPI=300` and re-upload. PDFs with two-column academic layouts can
interleave columns, since Vision reads in geometric order.

**Port already in use.** `MURMUR_PORT=9000 ./run.sh`.

## Licences

- Rhapsode's own code: MIT (see [`LICENSE`](LICENSE)).
- Kokoro-82M weights: Apache-2.0 (commercial use allowed).
- `kokoro-onnx`: MIT. Voice grades and language data from the Kokoro model card.
- Document parsing uses `pypdfium2`/PDFium (BSD/Apache) and a small built-in
  EPUB reader, so there is no AGPL code in the extraction path.
- The full dependency list, model terms, and the two remaining GPL components
  (`phonemizer`, `espeak-ng` via `espeakng-loader`) are documented in
  [`THIRD_PARTY.md`](THIRD_PARTY.md).

**Sharing it.** Send the source folder and have your friend run `./run.sh`. It
creates their own `.venv`, installs dependencies from PyPI, downloads the Kokoro
weights, and compiles the two optional Swift helpers (needs Xcode Command Line
Tools). A prebuilt `Rhapsode.app` also bundles `phonemizer` and eSpeak NG, which
are GPL-3.0, so redistributing that `.app` carries GPL obligations (source offer
+ licence texts). Rhapsode's own code stays MIT in both cases.

