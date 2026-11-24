# Source for the copyleft components in a Rhapsode build

Rhapsode's own code is MIT (see [`../LICENSE`](../LICENSE)). Some components that a
**built** Rhapsode runs are under the GNU GPL-3.0 or LGPL, so a distributed binary
carries those licences for those components. This file is the written offer it
needs: every copyleft component, the exact version in the build, and where its
source is.

Nothing here applies to a source checkout — `run.sh` / `setup.ps1` install these
from PyPI into your own environment, and you hold the licences directly.

## Components under the GPL-3.0

| Component | Version | Source | Licence text |
| --- | --- | --- | --- |
| `phonemizer` (Python bindings to eSpeak NG) | 3.6.0 | <https://github.com/bootphon/phonemizer/tree/v3.6.0> · sdist: <https://pypi.org/project/phonemizer/3.6.0/#files> | [`GPL-3.0.txt`](GPL-3.0.txt) |
| eSpeak NG (`espeak-ng.dll` + `espeak-ng-data`) | 1.52.0 | <https://github.com/espeak-ng/espeak-ng/tree/1.52.0> | [`GPL-3.0.txt`](GPL-3.0.txt) |
| `espeakng-loader` (packages the eSpeak NG build above) | 0.2.4 | <https://github.com/thewh1teagle/espeakng-loader> · sdist: <https://pypi.org/project/espeakng-loader/0.2.4/#files> | MIT (loader only) |

`espeakng-loader` is MIT; the library and data it carries are the GPL-3.0 part.
The Windows installer and the macOS bundles include both, because
`kokoro-onnx`'s tokenizer phonemises through them.

## Components under the LGPL

These are separate, unmodified, dynamically loaded libraries, which is the
arrangement the LGPL is written for: you may replace them in your installation.

| Component | Version | Source | Licence text |
| --- | --- | --- | --- |
| `libsndfile` (`libsndfile_x64.dll`, inside the `soundfile` wheel) | 1.2.2 | <https://github.com/libsndfile/libsndfile/releases/tag/1.2.2> | [`LGPL-2.1.txt`](LGPL-2.1.txt) |
| `libheif` (`libheif-*.dll`, inside the `pillow-heif` wheel) | 1.23.6 | <https://github.com/strukturag/libheif/releases/tag/v1.23.6> | [`LGPL-3.0.txt`](LGPL-3.0.txt) (supplement to GPL-3.0, also in this folder) |
| FFmpeg inside the OpenCV wheel (`opencv_videoio_ffmpeg510_64.dll`) | FFmpeg 5.1 | <https://ffmpeg.org/releases/ffmpeg-5.1.tar.xz> | [`LGPL-2.1.txt`](LGPL-2.1.txt) |

The OpenCV wheel ships further binaries; its own complete notice is included as
[`opencv-LICENSE-3RD-PARTY.txt`](opencv-LICENSE-3RD-PARTY.txt). Rhapsode itself
calls OpenCV only for face detection and resizing — the bundled FFmpeg is unused
and is scheduled for removal (issue #84).

## The offer

If any of the above is missing from a download, or you would rather receive it on
physical media, ask: open an issue at <https://github.com/endynamoo/rhapsode/issues>
or write to the address on the repository's profile. The source will be provided
for no more than the cost of the media, and this offer is valid for three years
after the release it accompanies, or for as long as that release is supported,
whichever is longer.

Every Rhapsode release is built from a tag in
<https://github.com/endynamoo/rhapsode>, and the PyInstaller recipes that assemble
the bundles are in `packaging/` (`Rhapsode.spec`, `Rhapsode-win.spec`,
`Rhapsode-setup.iss`, `build_installer.ps1`, `make_dmg.sh`) — they are part of the
Corresponding Source.

## What this does not cover

- **Model weights** (Kokoro, FLUX.2 [klein], the detector ONNX files, the Qwen2.5
  translation weights) are Apache-2.0 and are downloaded at runtime, not bundled. The
  translation runtime (`llama-server`, MIT) is fetched the same way. See
  [`../THIRD_PARTY.md`](../THIRD_PARTY.md).
- **Per-MPL-2.0 packages** (`certifi`, `tqdm`) keep their own licences; they are
  unmodified and their notices travel with the wheel.
- **ffmpeg, Tesseract** and other system tools are invoked from `PATH`; they are
  the user's copies, not distributed here.
