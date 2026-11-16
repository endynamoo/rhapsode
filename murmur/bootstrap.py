"""First-run setup: download Kokoro-82M weights with progress output."""
from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

from . import config


def _download(url: str, dest: Path, label: str) -> None:
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"  ✓ {label} ({dest.stat().st_size / 1e6:.0f} MB)")
        return
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


def main() -> None:
    config.ensure_dirs()
    print("Murmur setup")
    print(f"  Home: {config.HOME}")
    if not config.MODEL_PATH.exists() or not config.VOICES_PATH.exists():
        print("  Downloading Kokoro-82M (one time, ~340 MB)…")
        _download(config.MODEL_URLS["kokoro-v1.0.onnx"], config.MODEL_PATH, "Kokoro-82M model")
        _download(config.MODEL_URLS["voices-v1.0.bin"], config.VOICES_PATH, "Voice styles")
    else:
        print("  ✓ Kokoro-82M already downloaded")

    from . import store

    store.init()
    print("  ✓ Library ready")
    print("Done. Start Murmur with ./run.sh")


if __name__ == "__main__":
    main()
