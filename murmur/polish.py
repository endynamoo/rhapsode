"""One cleanup pass over a painted still.

The pass is a low-strength re-draw from the still itself: FLUX.2 looks at its own
output and paints over it at 0.32 strength, which sharpens mush, settles stray
linework, and repairs hands and faces without changing the composition — nothing
is masked, so nothing can leave a seam.

Detection decides whether it is worth the GPU time: a still with no people in it
(landscapes, props) is left alone. Every still records what happened in a
`.polish.json` sidecar, keyed to the output hash, so a repaint polishes again and
an unchanged still never does.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np

from . import config, detectors, imaging, proc, store

POLISH_VERSION = "polish_v1"
STRENGTH = 0.32
STEPS = 4

CLEAN_SUFFIX = (
    " Re-draw the same composition cleanly: consistent faces, correct hands with "
    "five fingers, coherent anatomy, crisp ink linework, no artifacts, no text, "
    "no speech bubbles."
)


def sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".polish.json")


def backup(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".pre-polish.png")


def enabled() -> bool:
    try:
        return bool(store.get_settings().get("polishStills", True))
    except Exception:
        return True


def _digest(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bgr(path: Path) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB"))[:, :, ::-1]


def _read_record(path: Path) -> dict:
    try:
        return json.loads(sidecar(path).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write_record(path: Path, record: dict) -> None:
    try:
        sidecar(path).write_text(json.dumps(record, indent=2), "utf-8")
    except OSError:
        pass


def _script() -> Path | None:
    for candidate in (config.ASSET_ROOT / "fluxgen.py", Path(__file__).with_name("fluxgen.py")):
        if candidate.exists():
            return candidate
    return None


def apply(path: Path, prompt: str = "", force: bool = False) -> dict:
    """Polish one still in place. Never raises: painting must not depend on it."""
    if not enabled():
        return {"action": "disabled"}
    if not path.exists():
        return {"action": "missing"}
    if not detectors.available():
        return {"action": "skipped", "reason": "detectors unavailable"}

    digest = _digest(path)
    record = _read_record(path)
    if not force and record.get("version") == POLISH_VERSION and record.get("output_hash") == digest:
        return {"action": "cached"}

    info = detectors.inspect(_bgr(path))
    if info.get("error"):
        _write_record(path, {"version": POLISH_VERSION, "action": "failed",
                             "error": info["error"], "output_hash": digest})
        return {"action": "failed", "error": info["error"]}

    if not info["faces"] and not info["people"]:
        _write_record(path, {"version": POLISH_VERSION, "action": "skipped",
                             "reason": "no people", "output_hash": digest})
        return {"action": "skipped", "reason": "no people"}

    script = _script()
    if script is None or not config.IMAGE_PYTHON.exists():
        return {"action": "skipped", "reason": "no image engine"}

    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
    except OSError as exc:
        return {"action": "failed", "error": str(exc)}

    keep = backup(path)
    if not keep.exists():
        try:
            keep.write_bytes(path.read_bytes())
        except OSError:
            pass

    out = path.with_suffix(".polish.png")
    out.unlink(missing_ok=True)
    env = dict(__import__("os").environ)
    env.setdefault("HF_HOME", str(config.HOME / "models" / "hf"))
    command = [
        str(config.IMAGE_PYTHON), str(script),
        "--prompt", (prompt or "monochrome manga illustration").rstrip() + CLEAN_SUFFIX,
        "--reference", str(path),
        "--strength", str(STRENGTH),
        "--steps", str(STEPS),
        "--width", str(width), "--height", str(height),
        "--seed", str(int(digest[:8], 16)),
        "--out", str(out),
    ]
    started = time.time()
    try:
        result = proc.run(command, capture_output=True, text=True, timeout=900,
                          cwd=str(config.APP_ROOT), env=env, errors="replace")
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        _write_record(path, {"version": POLISH_VERSION, "action": "failed",
                             "error": str(exc), "output_hash": digest})
        return {"action": "failed", "error": str(exc)}

    if result.returncode != 0 or not out.exists() or out.stat().st_size < imaging.MIN_STILL_BYTES:
        tail = (result.stderr or result.stdout or "no output")[-400:]
        _write_record(path, {"version": POLISH_VERSION, "action": "failed",
                             "error": tail, "output_hash": digest})
        out.unlink(missing_ok=True)
        return {"action": "failed", "error": tail}

    try:
        out.replace(path)
    except OSError as exc:
        out.unlink(missing_ok=True)
        return {"action": "failed", "error": str(exc)}

    after = detectors.inspect(_bgr(path))
    record = {
        "version": POLISH_VERSION,
        "action": "refined",
        "strength": STRENGTH,
        "seconds": round(time.time() - started, 1),
        "input_hash": digest,
        "output_hash": _digest(path),
        "faces_before": len(info["faces"]),
        "faces_after": len(after.get("faces") or []),
        "people_before": len(info["people"]),
        "people_after": len(after.get("people") or []),
    }
    _write_record(path, record)
    return record
