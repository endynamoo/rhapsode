"""First-run setup: download Kokoro-82M weights with progress output."""
from __future__ import annotations

import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import config


def _download(url: str, dest: Path, label: str, minimum: int) -> None:
    problem = config.weight_problem(dest, minimum)
    if problem is None:
        print(f"  ✓ {label} ({dest.stat().st_size / 1e6:.0f} MB)")
        return
    if dest.exists():
        # A stub left by an interrupted download would otherwise be kept forever.
        print(f"  ! {problem} - downloading it again")
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  ↓ {label}")
    start = time.time()
    last = 0.0

    def hook(blocks: int, block_size: int, total: int) -> None:
        nonlocal last
        downloaded = blocks * block_size
        now = time.time()
        if now - last < 0.4 and downloaded < total:
            return
        last = now
        pct = (downloaded / total * 100) if total > 0 else 0
        speed = downloaded / max(now - start, 0.001) / 1e6
        bar = "█" * int(pct / 4) + "░" * (25 - int(pct / 4))
        sys.stdout.write(f"\r    [{bar}] {pct:5.1f}%  {speed:5.1f} MB/s ")
        sys.stdout.flush()

    urllib.request.urlretrieve(url, tmp, reporthook=hook)
    tmp.replace(dest)
    print(f"\r    ✓ {label} ({dest.stat().st_size / 1e6:.0f} MB)          ")


_STATE = {"running": False, "done": False, "error": None, "size": 0}
_STATE_LOCK = threading.Lock()


def state() -> dict:
    """What the on-demand download is doing, for /api/health and the Settings row."""
    return dict(_STATE)


def download_weights() -> None:
    """Fetch whichever weight file is missing or too small."""
    problem = config.voice_model_problem()
    if problem:
        print(f"  {problem}")
        print("  Downloading Kokoro-82M (one time, ~340 MB)\u2026")
        _download(
            config.MODEL_URLS["kokoro-v1.0.onnx"], config.MODEL_PATH,
            "Kokoro-82M model", config.MODEL_MIN_BYTES,
        )
        _download(
            config.MODEL_URLS["voices-v1.0.bin"], config.VOICES_PATH,
            "Voice styles", config.VOICES_MIN_BYTES,
        )
        remaining = config.voice_model_problem()
        if remaining:
            raise RuntimeError(f"The download did not produce a usable model: {remaining}")
    _STATE["size"] = config.MODEL_PATH.stat().st_size if config.MODEL_PATH.exists() else 0


def start_download() -> dict:
    """Start the repair in the background; safe to call twice."""
    with _STATE_LOCK:
        if _STATE["running"]:
            raise RuntimeError("The voice model is already downloading.")
        if config.voice_model_ready():
            _STATE.update(running=False, done=True, error=None)
            return state()
        _STATE.update(running=True, done=False, error=None)

    def _run() -> None:
        try:
            download_weights()
            _STATE.update(running=False, done=True, error=None)
        except Exception as exc:  # pragma: no cover - network failures
            _STATE.update(running=False, done=False, error=str(exc))

    threading.Thread(target=_run, name="kokoro-download", daemon=True).start()
    return state()


def main() -> None:
    config.ensure_dirs()
    print("Murmur setup")
    print(f"  Home: {config.HOME}")
    # The same predicate the engine uses: a stub file must be repaired here, not
    # reported as "already downloaded".
    if config.voice_model_ready():
        print("  \u2713 Kokoro-82M already downloaded")
    else:
        download_weights()

    from . import store

    store.init()
    print("  ✓ Library ready")
    print("Done. Start Murmur with ./run.sh")


if __name__ == "__main__":
    main()
