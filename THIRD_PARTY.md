# Third-party components

Murmur's own source code is released under the MIT license in [`LICENSE`](LICENSE).
This file lists the third-party software, model weights, and data it uses, and
the licenses they carry. It is a notice, not legal advice.

## Python runtime dependencies

Installed from PyPI by `./run.sh` / `build_app.sh`; not copied into this
repository.

| Component | Used for | License |
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
| `diffusers` | FLUX.2 image pipeline | Apache-2.0 |
| `transformers` | FLUX.2 text encoder | Apache-2.0 |
| `accelerate` | Model loading | Apache-2.0 |
| `safetensors` | Weight loading | Apache-2.0 |
| `huggingface_hub` | Weight downloads | Apache-2.0 |
| `tokenizers` | Text tokenisation | Apache-2.0 |
| `torch` | Metal tensor backend | BSD-3-Clause |
| `PyInstaller` (build only) | `.app` bundling | GPL-2.0 with bootloader exception |

### Stills engine (optional, all platforms)

Installed on demand into `image-venv` inside the data directory; weights are
downloaded separately and are not bundled.

| Component | Used for | License |
| --- | --- | --- |
| mflux | FLUX.2 inference on Apple MLX (macOS) | MIT |
| diffusers | FLUX.2 inference on CUDA/ROCm (Windows, Linux) | Apache-2.0 |
| FLUX.2 [klein] 4B weights | Stills at both quality presets | Apache-2.0 |
| Qwen3 text encoder | FLUX.2 conditioner | Apache-2.0 |

Setup: `./scripts/setup_flux.sh` on macOS, `scripts\setup_image.ps1` on Windows.

## Copyleft runtime components

Two dependencies of `kokoro-onnx` are **GPL-3.0**:

| Component | Used for | License |
| --- | --- | --- |
| `phonemizer` | Python bindings to the eSpeak NG C library | GPL-3.0 |
| `espeakng-loader` + eSpeak NG data | English text-to-phoneme conversion | GPL-3.0 |

They are loaded as separate Python/C libraries and are not modified by Murmur.
Because they are GPL, a **binary distribution that bundles them** (for example a
prebuilt `Rhapsode.app`) must comply with GPL-3.0: include the license texts and
offer the corresponding source. Upstream sources are available at:

- https://github.com/bootphon/phonemizer
- https://github.com/espeak-ng/espeak-ng
- https://github.com/thewh1teagle/espeakng-loader

If you want to share Murmur without that obligation, share the source and let the
recipient run `./run.sh`, which installs dependencies directly from PyPI into
their own environment. Murmur's own code remains MIT either way.

## Model weights

Weights are downloaded at runtime and are **not** part of this repository.

| Model | License | Notes |
| --- | --- | --- |
| Kokoro-82M (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) | Apache-2.0 | Voice styles from the Kokoro model card. |
| all-MiniLM-L6-v2 (`minilm-l6-v2/model.onnx`) | Apache-2.0 | Optional search embeddings, int8 ONNX export by `Xenova/all-MiniLM-L6-v2`; 23 MB, CPU only, downloaded only when the user asks for meaning search. |
| FLUX.2 [klein] 4B | Apache-2.0 | The stills engine, both quality presets. Downloaded by `scripts/setup_flux.sh` (macOS) or `scripts/setup_image.ps1` (Windows/Linux). |
| FLUX.2 [klein] 4B Q4_K_M (GGUF) | Apache-2.0 | Quantized transformer from `unsloth/FLUX.2-klein-4B-GGUF`; 2.4 GB on disk and ~2.6 GiB resident, which is what lets an 8 GB card paint. |
| Qwen3 text encoder used by FLUX.2 | Apache-2.0 | Downloaded with the FLUX.2 pipeline. |
| YuNet face detector (`yunet.onnx`) | MIT | Stills polish pass: finds faces so a still with people gets re-drawn. |
| RTMDet person detector (`rtmdet-n-person.onnx`) | Apache-2.0 | Stills polish pass: decides whether a still contains people at all. |
| RTMPose (`rtmpose-m.onnx`) | Apache-2.0 | Locates wrists and joints for the hand repair and the anatomy check. |

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
  see those packages for the bundled data licenses.
- The app icon and all UI artwork are original to Murmur (MIT).

## Replacing the remaining GPL components

`kokoro-onnx` imports `phonemizer` and eSpeak NG unconditionally when it is
constructed. Removing them requires replacing the English grapheme-to-phoneme
step — for example with `misaki`'s English G2P (Apache-2.0, requires spaCy
models) or another permissive G2P — and constructing the ONNX session without
`kokoro-onnx`'s tokenizer. That is tracked as future work; until then this notice
describes the actual state of the code.
