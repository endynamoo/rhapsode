# Third-party components

Rhapsode's own source code is released under the MIT license in [`LICENSE`](LICENSE).
This file lists the third-party software, model weights and data it uses, and the
licenses they carry. It is a notice, not legal advice, and it describes what the
code actually does — including the parts that are not MIT.

Verified against the installed distributions and the upstream model cards on
2026-11-19. Where a wheel ships a compiled library beside its Python code, the
library is listed separately, because its license is the one that matters.

## The one thing to know before distributing a build

The Windows installer carries **GPL-3.0 code**: `phonemizer` (GPL-3.0-or-later, 165 KB of
Python) and **eSpeak NG** — `espeak-ng.dll` plus 364 data files, 18.8 MB in total, from
`espeakng-loader`. They are a hard dependency of `kokoro-onnx`'s tokenizer
(`kokoro_onnx/tokenizer.py` imports `phonemizer` at module level and loads the eSpeak
library to turn text into phonemes), and `packaging/Rhapsode-win.spec` collects both
packages into the bundle. Both macOS specs collect them too, so every artifact in a
release — the Setup exe and both DMGs — carries them.

So a distributed `Rhapsode-<version>-Setup.exe` **is a distribution of those GPL-3.0
works**, and GPL-3.0 obligations attach to it: ship the license texts and provide
the corresponding source (or a written offer). Publishing the MIT source of
Rhapsode alone does not discharge that. A source checkout is not affected — the
dependencies arrive from PyPI into the user's own environment, and the user then
holds the licenses themselves.

The licence texts, the exact versions in this build and the written offer for
their source live in [`licenses/`](licenses/) - that folder, not this page, is
what a build ships and what the installer shows.

Two honest ways forward, and the maintainer's call:

1. **Comply.** Include the GPL-3.0 text for `phonemizer` and eSpeak NG in the
   installer, and state in the download page that the binary as distributed is
   GPL-3.0 because of them (Rhapsode's own code stays MIT). Upstream sources:
   <https://github.com/bootphon/phonemizer>, <https://github.com/espeak-ng/espeak-ng>,
   <https://github.com/thewh1teagle/espeakng-loader>.
2. **Replace eSpeak.** Kokoro's phonemes would have to come from a permissive G2P
   instead — `misaki`'s English G2P (Apache-2.0, brings spaCy models) and the
   Japanese/Chinese paths `misaki` already provides — and `kokoro-onnx`'s tokenizer
   bypassed. That is real work and is not scheduled; see the last section.

## Copyleft in the shipped runtime

| Component | Where it comes from | License | Used for |
| --- | --- | --- | --- |
| `phonemizer` | PyPI, required by `kokoro-onnx` | **GPL-3.0-or-later** | English text → phonemes |
| eSpeak NG (`espeak-ng.dll`, `espeak-ng-data/`) | `espeakng-loader` wheel | **GPL-3.0** | The phonemiser engine and its dictionaries |
| `libsndfile` (`libsndfile_x64.dll`) | inside the `soundfile` wheel | **LGPL-2.1** | Reading and writing the narration WAVs |
| `libheif` (`libheif-*.dll`) | inside the `pillow-heif` wheel | **LGPL-3.0** | HEIC/HEIF decoding (Windows, Linux; skipped on macOS) |
| FFmpeg (`opencv_videoio_ffmpeg*.dll`, 41 MB) | inside the `opencv-contrib-python-headless` wheel | **LGPL-2.1-or-later** | Nothing — Rhapsode uses OpenCV only for `FaceDetectorYN` and `resize` |
| `fugashi` / MeCab (`libmecab-*.dll`) | dependency of `misaki`'s Japanese path | MIT wrapper, MeCab triple-licensed (**we take BSD**) | Japanese tokenisation |
| `certifi`, `tqdm` | PyPI | **MPL-2.0** (file-level copyleft) | CA bundle, progress bars |

The LGPL entries are separate dynamically-loaded libraries, unmodified, in their
own files — the usual LGPL arrangement, which requires the license text and the
ability to swap the library, not release of Rhapsode's source. The GPL-3.0 entries
are the ones with real consequences, above.

## Python runtime dependencies (installed from PyPI, not copied into this repository)

| Component | Used for | License |
| --- | --- | --- |
| `kokoro-onnx` | Kokoro ONNX inference wrapper | MIT |
| `onnxruntime` | Model execution (TTS, detectors, search) | MIT |
| `numpy` | Numerics (wheel bundles OpenBLAS, BSD-3) | BSD-3-Clause |
| `soundfile` | WAV I/O (ships `libsndfile`, LGPL-2.1 — see above) | BSD-3-Clause |
| `misaki[ja,zh]` | Japanese/Chinese G2P | Apache-2.0 |
| `pyopenjtalk` | Japanese G2P dictionaries (bundles Open JTalk, modified BSD) | MIT |
| `jieba`, `pypinyin` | Chinese tokenisation and pinyin | MIT |
| `fastapi`, `starlette`, `pydantic` | HTTP API | MIT / BSD-3-Clause / MIT |
| `uvicorn` | ASGI server | BSD-3-Clause |
| `python-multipart` | Uploads | Apache-2.0 |
| `httpx`, `h11` | HTTP client | BSD-3-Clause / MIT |
| `pypdfium2` / PDFium | PDF text, bookmarks, page rendering | Apache-2.0 / BSD-3-Clause |
| `python-docx` | DOCX extraction | MIT |
| `trafilatura`, `justext`, `htmldate`, `courlan`, `lxml` | Article/HTML extraction and date parsing | Apache-2.0 / BSD-2-Clause / BSD-3-Clause |
| `beautifulsoup4` | HTML/EPUB parsing | MIT |
| `Pillow` | Image drawing and page images | MIT-CMU |
| `pillow-heif` | HEIC/HEIF plugin (see `libheif` above) | BSD-3-Clause |
| `opencv-contrib-python-headless` | YuNet face detection, image resizing (MIT packaging layer, Apache-2.0 OpenCV; the wheel also carries the FFmpeg above) | Apache-2.0 |
| `winsdk` | On-device OCR and system APIs on Windows | MIT |
| `pyobjc-framework-Vision` | On-device OCR on macOS | MIT |
| `pywebview`, `pythonnet`, `clr-loader` | Native window | BSD-3-Clause / MIT / MIT |
| `gguf` | Reading the quantised FLUX transformer | MIT |

`pyinstaller` (build only) is GPL-2.0-or-later **with the bootloader exception**,
which explicitly allows distributing bundled applications.

## Stills engine (optional, installed on demand into `image-venv`)

Weights are downloaded separately and are not bundled.

| Component | Used for | License |
| --- | --- | --- |
| `diffusers` | FLUX.2 inference on CUDA/ROCm | Apache-2.0 |
| `transformers`, `tokenizers` | FLUX.2 text encoder | Apache-2.0 |
| `accelerate`, `safetensors`, `huggingface_hub` | Model loading and downloads | Apache-2.0 |
| `torch` | Tensor backend (bundles ROCm/MKL libraries, permissive up to Apache-2.0/BSD/MIT; the GCC runtime `libgomp` it links is GPL-3.0 **with the GCC runtime exception**, which places no obligation on Rhapsode) | BSD-3-Clause |
| `mflux` (macOS path) | FLUX.2 inference on Apple MLX | MIT |
| `mlx` (macOS path) | Apple's tensor framework | MIT |

Setup: `./scripts/setup_flux.sh` on macOS, `scripts\setup_image.ps1` on Windows and
Linux. The app itself never imports torch.

## Model weights (downloaded at runtime, Apache-2.0 throughout)

| Model | Source | License |
| --- | --- | --- |
| Kokoro-82M (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) | `thewh1teagle/kokoro-onnx` releases | Apache-2.0 |
| FLUX.2 [klein] 4B | `black-forest-labs/FLUX.2-klein-4B` | Apache-2.0 |
| FLUX.2 [klein] 4B Q4_K_M (GGUF) | `unsloth/FLUX.2-klein-4B-GGUF` | Apache-2.0 |
| Qwen3 text encoder used by FLUX.2 | downloaded with the pipeline | Apache-2.0 |
| all-MiniLM-L6-v2, int8 ONNX | `Xenova/all-MiniLM-L6-v2` | Apache-2.0 |
| YuNet face detector (`yunet.onnx`) | `bukuroo/YuNet-ONNX` (upstream `opencv/opencv_zoo`) | Apache-2.0 |
| RTMDet person detector (`rtmdet-n-person.onnx`) | `bukuroo/RTMDet-ONNX` (upstream `opencv/opencv_zoo`) | Apache-2.0 |
| RTMPose (`rtmpose-m.onnx`) | `bukuroo/RTMPose-ONNX` (upstream `opencv/opencv_zoo`) | Apache-2.0 |

Rhapsode downloads them into its own data directory. It does not ship them inside
the source tree; a bundle that includes them is redistributing the models and must
follow their terms.

## Build and release tools

| Tool | Used for | License |
| --- | --- | --- |
| PyInstaller | Freezing the desktop app | GPL-2.0-or-later with bootloader exception |
| Inno Setup | The Windows installer | Inno Setup License (free to use and redistribute installers, commercial included) |
| Playwright (Python) | Browser tests | Apache-2.0 |
| `uv` (optional, in the run scripts) | Environment management | Apache-2.0 / MIT |

## System tools Rhapsode calls but does not ship

- **ffmpeg / ffprobe** — film rendering and MP3 export (`libmp3lame`). Found on
  `PATH` (`brew install ffmpeg` on macOS). A Homebrew or gyan.dev build may be
  LGPL or GPL depending on its codecs; Rhapsode invokes it as a separate program,
  which does not make Rhapsode a derivative of it, and it is the user's copy.
- **Tesseract** — OCR on Linux. Apache-2.0, installed by the distribution.
- **Apple Vision, Translation, NaturalLanguage, FoundationModels** — part of
  macOS, used through the system SDK.
- **Windows.Media.Ocr** — part of Windows, reached through `winsdk`.

## Fonts and artwork

- Japanese and Chinese dictionaries come from `misaki` extras and `pyopenjtalk`;
  the packages carry the data licenses.
- The app icon and all UI artwork are original to Rhapsode (MIT).
- No web fonts are bundled or fetched: the reader uses the system font families it
  finds, and film subtitles reference font names only.

## Removing the last GPL dependency (future work)

`kokoro_onnx/tokenizer.py` imports `phonemizer` and `espeakng_loader` at module
level, so eSpeak cannot be dropped without replacing the grapheme-to-phoneme step.
The candidate is `misaki`'s own English G2P (Apache-2.0, brings spaCy models) plus
the Japanese and Chinese paths `misaki` already covers, driving the ONNX session
directly instead of through `kokoro-onnx`'s tokenizer. Until that happens, this
notice describes the actual state of the code, and a binary distribution carries
GPL-3.0 obligations for those two components.

Separately, `opencv-contrib-python-headless` is used for exactly two things —
`FaceDetectorYN` and `resize` — yet its wheel adds 41 MB of bundled FFmpeg that
Rhapsode never calls. Running `yunet.onnx` through `onnxruntime` (which already
executes the other two detectors) and resizing with Pillow would remove that wheel
from the bundle entirely.
