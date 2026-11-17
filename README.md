<img src="branding/banner.png" alt="Rhapsode — read-aloud and manga stills, entirely on your own machine" width="100%">

A read-aloud app that paints manga stills, entirely on your own machine. Drop in a
book, a PDF, a scanned page or a screenshot and it reads to you in a local voice —
no account, no cloud, no per-word billing.

![tts](https://img.shields.io/badge/TTS-Kokoro--82M-f5a26b) ![license](https://img.shields.io/badge/license-MIT-green) ![status](https://img.shields.io/badge/version-0.2.0--alpha-orange) ![platforms](https://img.shields.io/badge/platforms-macOS%20%C2%B7%20Windows%20%C2%B7%20Linux-6f6a5f) [![windows](https://github.com/endynamoo/rhapsode/actions/workflows/windows.yml/badge.svg)](https://github.com/endynamoo/rhapsode/actions/workflows/windows.yml)

## What it does

- **Reads anything** — pasted text, PDF, EPUB, DOCX, RTF, TXT, Markdown, an image,
  or a URL it extracts the article from.
- **Book view with a real word highlight** — EPUBs render their own chapters, PDFs
  page by page, and the highlight is driven by Kokoro's phoneme timings rather than
  a guess, so it tracks the voice.
- **OCR built in** — scanned pages and screenshots, on-device: Apple Vision on
  macOS, Windows.Media.Ocr on Windows, Tesseract on Linux.
- **On-device translation** — read a foreign book in your own language, or in the
  foreign voice with the translation shown under the player. Apple's framework on
  macOS; a local OpenAI-compatible server everywhere else, configured or
  discovered on the usual ports.
- **Polish pass** — each still is re-drawn once from itself so the ink settles and
  faces tighten; a face/person detector decides whether a frame needs it at all.
  Optional, and toggleable in Settings.
- **Manga stills** — the narration is planned into beats and each beat gets one
  locally generated still. Chapter view plays those stills against the voice with
  a slow drift and a dissolve, and a chapter can be assembled into a 1080p MP4 cut
  to the narration. Nothing is animated: the pictures stay still, and no video
  model is involved.
- **Quick Read** — one hotkey reads whatever is selected in any app; with no
  selection it OCRs the clipboard image.
- **Library search** — find a passage across every book you have imported, by
  words or by meaning, with the snippet marked and a jump straight to that
  sentence (`Ctrl+F`). Keyword search needs nothing extra; the 23 MB meaning
  model is optional, off by default, and downloads only when you ask.
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

**Windows 10/11** (preview). The installer is per-user — no admin prompt — and
can set up the stills engine during the wizard:

```powershell
.\scripts\build_installer.ps1   # needs Inno Setup 7; writes dist\Rhapsode-<version>-Setup.exe
```

From source (needs `uv` and `ffmpeg`, Python 3.12):

```powershell
.\scripts\setup.ps1        # reader + stills engine + profile, auto-detected from the card
.\run.ps1                  # add -Desktop for a native WebView2 window
```

`setup.ps1` reads the card, picks an engine profile, downloads the q4 transformer
and the detectors, and writes `%LOCALAPPDATA%\Rhapsode\engine.json`. Override it
whenever you like: `.\scripts\setup.ps1 -Profile lite` (or `balanced`, `max`, `cpu`).

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
| [docs/SEARCH.md](docs/SEARCH.md) | Cross-library search: index, ranking, recency, the optional model, API |
| [docs/STILLS.md](docs/STILLS.md) | Manga stills, chapter stills, the Murmur engine sidecar |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | VRAM and RAM guards, where the logs are, failure modes already fixed |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, pipelines, API surface, security model, UML |
| [docs/PLATFORMS.md](docs/PLATFORMS.md) | Windows and Linux port, GPU backends, packaging |
| [docs/CONCURRENCY.md](docs/CONCURRENCY.md) | Several agents, one repository: worktrees, pushes, cleanup |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branches, commits, pull requests and releases |
| [AGENTS.md](AGENTS.md) | The install and build guide, written to be followed by an agent |
| [THIRD_PARTY.md](THIRD_PARTY.md) | Dependency, model and license inventory |

## Platform status

| Platform | Read / TTS / highlight | Chapter stills → MP4 | Native bundle |
|---|---|---|---|
| macOS 13+ (Apple Silicon) | supported | supported (MPS + MLX) | `.app` |
| macOS Intel | supported | CPU only, opt-in | `.app` |
| Windows 10/11 x64 | preview, installer or source | NVIDIA CUDA or AMD ROCm; CPU opt-in | Inno Setup installer (preview) |
| Linux x64 | preview, source install | NVIDIA CUDA or AMD ROCm; CPU opt-in | not yet |

AMD ROCm is wired into the stills engine through diffusers — measured on an
RX 9070 XT — while DirectML and Intel OpenVINO are still planned. Details in
[docs/PLATFORMS.md](docs/PLATFORMS.md).

## How this was built

Rhapsode is AI-assisted software with a human director. Most of the code was
written by AI models — **DeepSeek V4.1 Flash** and **Qwen 3.8 Flash Next** — working in long
supervised sessions. Architecture, standards, and the decision about what ships
stayed with one person, who keeps that director's role in every program here.

That director reviewed the work as thoroughly as they could, and the repo is built
so the claims are checkable rather than taken on faith: 158 tests in `tests/`, the
end-to-end checklist in [AGENTS.md](AGENTS.md) §6, and an architecture document
that spells out the API surface and the security model.

None of that makes it authority. This is AI-written code with a human editor —
read it before you depend on it, and look hardest at the parts that handle other
people's files and the network: document extraction, OCR input, and the
remote-access token.

## License

Rhapsode's own code is MIT — see [LICENSE](LICENSE). Kokoro-82M weights are
Apache-2.0, so commercial use is fine. Two GPL components (`phonemizer` and eSpeak
NG) reach the app through the dependency chain, so redistributing a **built
bundle** carries GPL obligations even though the source stays MIT; the inventory
is in [THIRD_PARTY.md](THIRD_PARTY.md).

## Changelog

<!-- CHANGELOG:BEGIN -->

Newest first. The full history is in [CHANGELOG.md](CHANGELOG.md); this block is generated from it by `scripts/changelog_render.py`, so edit the changelog, not this copy.

### Unreleased

**Added**

- Search across your whole library with `Ctrl+F` / `⌘F` or the magnifier: results grouped by book, the matching passage marked, and a jump straight to that sentence, chapter or page. Keywords work with nothing extra installed; the optional 23 MB meaning model (Apache-2.0, CPU only) is offered as a download in the panel and can be switched off with `MURMUR_SEMANTIC=off`.
- "Read this week" and "Read this month" filters for search, based on when a book was actually opened or its position saved.
- Live progress for library indexing and the model download, plus an Index library button; an interrupted index resumes where it stopped instead of duplicating rows.
- Troubleshooting page in the app: the three guards, where the logs live, and what was already fixed.
- The RAM probe the painting guard calls, and the RAM it measured shown in Settings.

**Changed**

- The front page, the third-party notices and the app's copyright string say "license", not "licence".
- Opening or resuming a book now records reading activity, so recent-only search can tell a book you read last week from one you imported and never touched.
- The README front page states what actually ships: the per-user Windows installer, ROCm stills, and the real test count.
- The generated changelog block no longer repeats the "Changelog" heading above it.
- `AGENTS.md` is plain UTF-8 text again, so its arrows and dashes render as arrows and dashes.

*...and 26 more entries in [CHANGELOG.md](CHANGELOG.md).*

<!-- CHANGELOG:END -->
