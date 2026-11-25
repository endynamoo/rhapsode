"""The ffmpeg the app can install itself.

MP3 export silently falls back to WAV without it and chapter films cannot be cut at all, so
"install ffmpeg and restart" was a sentence every Windows reader had to act on - assuming,
again, that the machine was somebody's development box.

This downloads the pinned gyan.dev release build (ffmpeg 9.1, essentials zip) into the data
directory and points `config.find_tool` at it. That build is **GPL-3.0**; the licence text,
the NOTICE row and the written offer in `licenses/SOURCES.md` travel with it, and the archive's
own LICENSE is kept beside the binary.

Windows only: macOS and Linux take ffmpeg from their package manager, and macOS ships
`afconvert` for the audio case.
"""
from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path

from . import config, download

#: gyan.dev "release essentials" (113 MB zip). Essentials is enough: it carries libmp3lame,
#: libass and libx264 - the MP3 export, the subtitle burn-in and the H.264 encode.
URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
VERSION = "9.1"
SHA256 = "b9bbbb3acece000cbeec994f856649354e52c773807841e78908374b24d658fd"
MIN_BYTES = 100 * 1024 * 1024

_STATE = {"running": False, "done": False, "error": None, "progress": None}
# Reentrant: state() takes the lock too, and the early returns below call it while
# holding it - a plain Lock made the second call deadlock (found by tests, 2026-11-25).
_lock = threading.RLock()


def install_root() -> Path:
    return config.HOME / "tools" / "ffmpeg"


def marker_path() -> Path:
    return install_root() / "ffmpeg-ready.json"


def managed_path(name: str = "ffmpeg") -> Path | None:
    """The copy this app installed, if it finished and the binary is still there."""
    candidate = install_root() / "bin" / f"{name}.exe"
    return candidate if candidate.is_file() else None


def installed() -> bool:
    return managed_path("ffmpeg") is not None and marker_path().is_file()


def available() -> bool:
    return managed_path("ffmpeg") is not None or bool(config.find_tool("ffmpeg"))


def state() -> dict:
    with _lock:
        snapshot = dict(_STATE)
    snapshot["progress"] = dict(snapshot["progress"]) if snapshot["progress"] else None
    snapshot.update(
        installed=installed(),
        available=available(),
        version=VERSION,
        path=str(managed_path("ffmpeg") or config.find_tool("ffmpeg") or ""),
        licence="GPL-3.0",
    )
    return snapshot


def start_download() -> dict:
    """Fetch and unpack ffmpeg in the background. Idempotent while running or when present."""
    with _lock:
        if _STATE["running"]:
            return state()
        if installed():
            _STATE.update(running=False, done=True, error=None, progress=None)
            return state()
        _STATE.update(running=True, done=False, error=None, progress=None)

    def _run() -> None:
        try:
            _fetch()
            with _lock:
                _STATE.update(running=False, done=True, error=None, progress=None)
        except Exception as exc:  # noqa: BLE001 - reported to the window
            with _lock:
                _STATE.update(running=False, done=False, error=str(exc), progress=None)

    threading.Thread(target=_run, name="ffmpeg-download", daemon=True).start()
    return state()


def _fetch() -> None:
    archive = install_root() / "ffmpeg.zip"
    last = 0.0

    def progress(downloaded: int, total: int, speed: float) -> None:
        nonlocal last
        import time

        now = time.time()
        if now - last < 0.4 and (total <= 0 or downloaded < total):
            return
        last = now
        pct = (downloaded / total * 100) if total > 0 else 0.0
        with _lock:
            _STATE["progress"] = {
                "label": "ffmpeg", "downloaded": downloaded, "total": total,
                "percent": round(pct, 1), "mbPerSecond": round(speed, 1),
            }

    download.fetch(URL, archive, progress=progress, sha256=SHA256, timeout=60)
    if archive.stat().st_size < MIN_BYTES:
        archive.unlink(missing_ok=True)
        raise RuntimeError("the ffmpeg download was truncated; try again")
    _unpack(archive)
    archive.unlink(missing_ok=True)
    if managed_path("ffmpeg") is None:
        raise RuntimeError("the ffmpeg archive had no ffmpeg.exe in it")
    marker = {
        "version": VERSION,
        "sha256": SHA256,
        "licence": "GPL-3.0",
        "source": "https://www.gyan.dev/ffmpeg/builds/",
    }
    marker_path().write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")


def _unpack(archive: Path) -> None:
    """Keep the binaries and the licence, not the 100 MB of documentation."""
    wanted_bin = {"ffmpeg.exe", "ffprobe.exe"}
    root = install_root()
    (root / "bin").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = Path(info.filename).name
            lower = name.lower()
            if name in wanted_bin:
                target = root / "bin" / name
            elif lower in {"license", "license.txt", "copying.gplv3", "readme.txt"}:
                target = root / f"{lower.replace('.', '-')}"
            else:
                continue
            if info.is_dir():
                continue
            with bundle.open(info) as source, open(target, "wb") as handle:
                while True:
                    block = source.read(1 << 20)
                    if not block:
                        break
                    handle.write(block)
