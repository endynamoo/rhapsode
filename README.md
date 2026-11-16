<img src="branding/banner.png" alt="Rhapsode — read-aloud and manga films, entirely on your own machine" width="100%">

A read-aloud and manga-film app that runs entirely on your own machine. Drop in a
book, a PDF, a scanned page or a screenshot and it reads to you in a local voice —
no account, no cloud, no per-word billing.

![tts](https://img.shields.io/badge/TTS-Kokoro--82M-f5a26b) ![licence](https://img.shields.io/badge/licence-MIT-green) ![status](https://img.shields.io/badge/version-1.0.0-blue) ![platforms](https://img.shields.io/badge/platforms-macOS%20%C2%B7%20Windows%20%C2%B7%20Linux-6f6a5f)

## What it does

- **Reads anything** — pasted text, PDF, EPUB, DOCX, RTF, TXT, Markdown, an image,
  or a URL it extracts the article from.
- **Book view with a real word highlight** — EPUBs render their own chapters, PDFs
  page by page, and the highlight is driven by Kokoro's phoneme timings rather than
  a guess, so it tracks the voice.
- **OCR built in** — scanned pages and screenshots, on-device: Apple Vision on
  macOS, Tesseract on Windows and Linux.
- **On-device translation** — read a foreign book in your own language, or in the
  foreign voice with the translation shown under the player.
- **Manga and chapter films** — each beat of the book gets a locally generated
  still, animated and subtitled, or exported as a 1080p MP4.
- **Quick Read** — one hotkey reads whatever is selected in any app; with no
  selection it OCRs the clipboard image.
- 54 voices · 9 languages · 0.75–3× speed · 20 themes · your place remembered per
  document.

Everything stays on the machine: the voice model, the audio cache and the library
are local files. Nothing is uploaded.

## Install

**macOS** (Apple Silicon or Intel, needs `uv` and `ffmpeg`):

```bash
./run.sh              # venv, deps, ~340 MB model, then opens http://127.0.0.1:8765
./build_app.sh        # optional: a real .app bundle
```

**Windows 10/11** (preview; needs `uv` and `ffmpeg`, Python 3.12):

```powershell
.\run.ps1             # same first run; add -Desktop for a native WebView2 window
```

**Linux** (preview; needs `uv` and `ffmpeg`):

```bash
./run.sh              # browser mode; see AGENTS.md Section C for the desktop window
```

Step-by-step instructions, per-GPU notes (CUDA, DirectML, ROCm) and the
verification checklist live in **[AGENTS.md](AGENTS.md)**.

## Documentation

| Doc | Contents |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Keyboard map, Quick Read, translation, OCR knobs, remote access, where your data lives, troubleshooting |
| [docs/FILMS.md](docs/FILMS.md) | Manga view, chapter films, the Murmur engine sidecar |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, pipelines, API surface, security model, UML |
| [docs/PLATFORMS.md](docs/PLATFORMS.md) | Windows and Linux port, GPU backend plan, packaging |
| [AGENTS.md](AGENTS.md) | The install and build guide, written to be followed by an agent |
| [THIRD_PARTY.md](THIRD_PARTY.md) | Dependency, model and licence inventory |

## Platform status

| Platform | Read / TTS / highlight | Chapter films | Native bundle |
|---|---|---|---|
| macOS 13+ (Apple Silicon) | supported | supported (MPS + MLX) | `.app` |
| macOS Intel | supported | CPU only, opt-in | `.app` |
| Windows 10/11 x64 | preview, source install | NVIDIA CUDA; CPU opt-in | community PyInstaller build |
| Linux x64 | preview, source install | NVIDIA CUDA; CPU opt-in | not yet |

AMD DirectML/ROCm and Intel OpenVINO are planned, not wired into the film
pipeline yet — see [docs/PLATFORMS.md](docs/PLATFORMS.md).

## How this was built

Rhapsode is AI-assisted software with a human director. Most of the code was
written by AI models — **DeepSeek V4.1 Flash** and **Qwen3** — working in long
supervised sessions. Architecture, standards, and the decision about what ships
stayed with one person, who keeps that director's role in every program here.

That director reviewed the work as thoroughly as they could, and the repo is built
so the claims are checkable rather than taken on faith: 39 tests in `tests/`, the
end-to-end checklist in [AGENTS.md](AGENTS.md) §6, and an architecture document
that spells out the API surface and the security model.

None of that makes it authority. This is AI-written code with a human editor —
read it before you depend on it, and look hardest at the parts that handle other
people's files and the network: document extraction, OCR input, and the
remote-access token.

## Licence

Rhapsode's own code is MIT — see [LICENSE](LICENSE). Kokoro-82M weights are
Apache-2.0, so commercial use is fine. Two GPL components (`phonemizer` and eSpeak
NG) reach the app through the dependency chain, so redistributing a **built
bundle** carries GPL obligations even though the source stays MIT; the inventory
is in [THIRD_PARTY.md](THIRD_PARTY.md).
