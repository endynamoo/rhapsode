# Third-party components

Murmur's own source code is released under the MIT licence in [`LICENSE`](LICENSE).
This file lists the third-party software, model weights, and data it uses, and
the licences they carry. It is a notice, not legal advice.

## Python runtime dependencies

Installed from PyPI by `./run.sh` / `build_app.sh`; not copied into this
repository.

| Component | Used for | Licence |
| --- | --- | --- |
| `kokoro-onnx` | Kokoro ONNX inference wrapper | MIT |
| `onnxruntime` | Model execution | MIT |
| `numpy` | Numerics | BSD-3-Clause |
| `soundfile` | WAV I/O | BSD-3-Clause |
| `misaki[ja,zh]` | Japanese/Chinese G2P | Apache-2.0 |
| `pyopenjtalk` | Japanese G2P dictionaries | MIT |
| `fastapi` | HTTP API | MIT |
| `uvicorn` | ASGI server | BSD-3-Clause |
| `python-multipart` | Uploads | Apache-2.0 |
| `httpx` | HTTP client | BSD-3-Clause |
| `pypdfium2` / PDFium | PDF text, bookmarks, page rendering | Apache-2.0 / BSD-3-Clause |
| `python-docx` | DOCX extraction | MIT |
| `trafilatura` | Article/HTML extraction | Apache-2.0 |
| `beautifulsoup4` | HTML/EPUB parsing | MIT |
| `Pillow` | Image drawing and page images | MIT-CMU |
| `pyobjc-framework-Vision` | On-device OCR | MIT |
| `pywebview` | Native macOS window | BSD-3-Clause |
| `diffusers` | Sana image pipeline | Apache-2.0 |
| `transformers` | Sana text encoder | Apache-2.0 |
| `accelerate` | Model loading | Apache-2.0 |
| `safetensors` | Weight loading | Apache-2.0 |
| `huggingface_hub` | Weight downloads | Apache-2.0 |
| `tokenizers` | Text tokenisation | Apache-2.0 |
| `torch` | Metal tensor backend | BSD-3-Clause |
| `PyInstaller` (build only) | `.app` bundling | GPL-2.0 with bootloader exception |

### Optional High image engine (macOS)

Installed on demand by `./scripts/setup_flux.sh` into `~/.murmur/image-venv`;
weights are downloaded separately and are not bundled.

| Component | Used for | Licence |
| --- | --- | --- |
| mflux | FLUX inference on Apple MLX | MIT |
| FLUX.2 [klein] 4B weights | High-quality image generation | Apache-2.0 |
| Qwen3-4B text encoder | FLUX.2 conditioner | Apache-2.0 |

## Copyleft runtime components

Two dependencies of `kokoro-onnx` are **GPL-3.0**:

| Component | Used for | Licence |
| --- | --- | --- |
| `phonemizer` | Python bindings to the eSpeak NG C library | GPL-3.0 |
| `espeakng-loader` + eSpeak NG data | English text-to-phoneme conversion | GPL-3.0 |

They are loaded as separate Python/C libraries and are not modified by Murmur.
Because they are GPL, a **binary distribution that bundles them** (for example a
prebuilt `Rhapsode.app`) must comply with GPL-3.0: include the licence texts and
offer the corresponding source. Upstream sources are available at:

- https://github.com/bootphon/phonemizer
- https://github.com/espeak-ng/espeak-ng
- https://github.com/thewh1teagle/espeakng-loader

If you want to share Murmur without that obligation, share the source and let the
recipient run `./run.sh`, which installs dependencies directly from PyPI into
their own environment. Murmur's own code remains MIT either way.

## Model weights

Weights are downloaded at runtime and are **not** part of this repository.

| Model | Licence | Notes |
| --- | --- | --- |
| Kokoro-82M (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) | Apache-2.0 | Voice styles from the Kokoro model card. |
| Sana 0.6B / 1.6B | NVIDIA Open Model License | Sana code is Apache-2.0; the released weights use NVIDIA's open model terms. Review them before redistributing the weights. |
| Gemma text encoder used by Sana | Gemma Terms of Use | Downloaded with the Sana pipeline. |
| Sana Sprint 0.6B | NVIDIA Open Model License | Optional fast preset. |
| FLUX.2 [klein] 4B | Apache-2.0 | Optional High image engine; downloaded by `scripts/setup_flux.sh`. |

Murmur downloads these weights on demand into `~/.murmur/models/hf`. It does not
ship them inside the source tree. If you redistribute a bundle that includes the
weights, you are redistributing the models and must follow their terms.

## System tools

- **ffmpeg / ffprobe** are not bundled. Film export uses the `ffmpeg` found on
  `PATH` (`brew install ffmpeg`). Homebrew's build is LGPL/GPL depending on the
  enabled codecs, and it is installed by the user rather than shipped with Murmur.
- **Apple frameworks** (Vision, Translation, NaturalLanguage, FoundationModels)
  are part of macOS and are used through the system SDK.

## Fonts and dictionaries

- Japanese/Chinese dictionaries come from `misaki` extras and `pyopenjtalk`;
  see those packages for the bundled data licences.
- The app icon and all UI artwork are original to Murmur (MIT).

## Replacing the remaining GPL components

`kokoro-onnx` imports `phonemizer` and eSpeak NG unconditionally when it is
constructed. Removing them requires replacing the English grapheme-to-phoneme
step — for example with `misaki`'s English G2P (Apache-2.0, requires spaCy
models) or another permissive G2P — and constructing the ONNX session without
`kokoro-onnx`'s tokenizer. That is tracked as future work; until then this notice
describes the actual state of the code.
