"""First-run setup: download Kokoro-82M weights with progress output."""
from __future__ import annotations

import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import config


def _emit(text: str) -> None:
    """Write a line a legacy console encoding may not be able to encode.

    bootstrap tells a person what it is doing, and on a cp1252 console the tick
    mark raises UnicodeEncodeError - which used to abort the download itself.
    Degrade the text instead of failing the run.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding, "replace"))


def _write(text: str) -> None:
    """The same guard for the in-place progress line."""
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        sys.stdout.write(text.encode(encoding, "replace").decode(encoding, "replace"))
    sys.stdout.flush()


def _download(url: str, dest: Path, label: str, minimum: int) -> None:
    problem = config.weight_problem(dest, minimum)
    if problem is None:
        _emit(f"  ✓ {label} ({dest.stat().st_size / 1e6:.0f} MB)")
        return
    if dest.exists():
        # A stub left by an interrupted download would otherwise be kept forever.
        _emit(f"  ! {problem} - downloading it again")
    tmp = dest.with_suffix(dest.suffix + ".part")
    _emit(f"  ↓ {label}")
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
        _write(f"\r    [{bar}] {pct:5.1f}%  {speed:5.1f} MB/s ")

    urllib.request.urlretrieve(url, tmp, reporthook=hook)
    tmp.replace(dest)
    _emit(f"\r    ✓ {label} ({dest.stat().st_size / 1e6:.0f} MB)          ")


_STATE = {"running": False, "done": False, "error": None, "size": 0}
_STATE_LOCK = threading.Lock()


def state() -> dict:
    """What the on-demand download is doing, for /api/health and the Settings row."""
    return dict(_STATE)


def download_weights() -> None:
    """Fetch whichever weight file is missing or too small."""
    problem = config.voice_model_problem()
    if problem:
        _emit(f"  {problem}")
        _emit("  Downloading Kokoro-82M (one time, ~340 MB)\u2026")
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


def fetch_language_data(log: Callable[[str], None] | None = None) -> bool:
    """Fetch what the extra languages need beyond their wheels.

    `pyopenjtalk` downloads its Open JTalk dictionary (23 MB) the first time a Japanese
    sentence is spoken, which is a surprise at exactly the wrong moment and impossible
    offline. Doing it here means "the model is downloaded" also means "the voices work".

    Returns True when there was nothing to do or it succeeded; never raises - a missing
    dictionary is not a reason to fail a download.
    """
    try:
        import importlib.util

        if importlib.util.find_spec("pyopenjtalk") is None:
            return True
    except Exception:  # noqa: BLE001
        return True
    try:
        import pyopenjtalk

        data = pyopenjtalk.OPEN_JTALK_DICT_DIR
        if data and pathlib.Path(str(data)).exists() and any(pathlib.Path(str(data)).iterdir()):
            if log:
                log("Japanese dictionary: already present")
            return True
        if log:
            log("Japanese dictionary: downloading 23 MB (one time)")
        pyopenjtalk.download(quiet=True)
        return True
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        if log:
            log(f"Japanese dictionary: could not fetch it ({exc})")
        return False


def main() -> None:
    config.ensure_dirs()
    _emit("Rhapsode setup")
    _emit(f"  Home: {config.HOME}")
    # The same predicate the engine uses: a stub file must be repaired here, not
    # reported as "already downloaded".
    if config.voice_model_ready():
        _emit("  \u2713 Kokoro-82M already downloaded")
    else:
        download_weights()

    from . import store

    store.init()
    _emit("  ✓ Library ready")
    _emit("Done. Start Rhapsode.")


if __name__ == "__main__":
    main()
