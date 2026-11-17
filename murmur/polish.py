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

from . import config, detectors, imaging, store

POLISH_VERSION = "polish_v1"
# One step for the global pass: the 1/2/4-step renders are indistinguishable on
# our stills, and warm that is ~10 s against 26 s and 101 s. Flux2KleinPipeline
# takes no `strength`, so the step count is the only per-pass lever; the resident
# painter removes the model load that used to dominate.
STEPS = 1

# The hand repair is different: it has to actually change pixels inside a mask, so
# it takes a real strength and two steps.
HAND_STRENGTH = 0.5
# Off by default. Measured on the lab still: at 0.75 the repair replaced the whole
# forearm with a white blot, and at 0.5 with a tighter mask it still redrew the desk
# and duplicated the hand. The mechanism is kept for tuning; a repair that damages
# a frame is worse than the blobby hand it was meant to fix.
HANDS_ENABLED = False
HAND_STEPS = 2

CLEAN_SUFFIX = (
    " Re-draw the same composition cleanly: consistent faces, correct hands with "
    "five fingers, coherent anatomy, crisp ink linework, no artifacts, no text, "
    "no speech bubbles."
)


_last_error: str | None = None
_last_action: str | None = None


def status() -> dict:
    """What the pass last did, for /api/health and support."""
    return {
        "version": POLISH_VERSION,
        "enabled": enabled(),
        "detectors": detectors.available(),
        "last_action": _last_action,
        "last_error": _last_error,
    }


def note_failure(error: object) -> None:
    """Remember a failure the painting path swallowed, so health can report it."""
    global _last_error, _last_action
    _last_action = "failed"
    _last_error = str(error)[-300:]


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



def _hand_mask(boxes: list[tuple[float, float, float, float]], size: tuple[int, int]) -> "object":
    """A soft mask over the hand boxes; feathering is what stops a seam."""
    from PIL import Image, ImageDraw, ImageFilter

    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for x1, y1, x2, y2 in boxes:
        pad = max(4.0, min(x2 - x1, y2 - y1) * 0.15)
        draw.ellipse([x1 - pad, y1 - pad, x2 + pad, y2 + pad], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(4, int(min(size) * 0.012))))


def _blend_in(base: Path, painted: Path, mask: "object") -> None:
    """Composite the repaired region back through the feathered mask."""
    from PIL import Image, ImageFilter

    with Image.open(base) as original, Image.open(painted) as result:
        alpha = mask.convert("L").resize(original.size)
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=max(3, int(min(original.size) * 0.008))))
        merged = Image.composite(result.convert("RGB"), original.convert("RGB"), alpha)
        merged.save(base, format="PNG")


def _repair_hands(path: Path, info: dict, width: int, height: int, seed: int) -> dict:
    """Mask the hands and re-draw just them, at a strength that actually repairs."""
    if not HANDS_ENABLED:
        return {"action": "disabled", "reason": "hand repair is experimental"}
    boxes = info.get("hands") or []
    if not boxes:
        return {"action": "none"}
    mask = _hand_mask(boxes, (width, height))
    mask_path = path.with_suffix(".hands-mask.png")
    painted = path.with_suffix(".hands.png")
    mask.save(mask_path, format="PNG")
    painted.unlink(missing_ok=True)
    job = {
        "prompt": (
            "monochrome manga panel, black ink linework and screentone shading, "
            "a pair of hands drawn correctly, five fingers on each, natural grip, "
            "no text, no speech bubbles"
        ),
        "out": str(painted),
        "width": width,
        "height": height,
        "seed": seed + 17,
        "steps": HAND_STEPS,
        "strength": HAND_STRENGTH,
        "reference": str(path),
        "mask": str(mask_path),
    }
    started = time.time()
    try:
        imaging.paint_job(job, timeout=900)
        if not painted.exists() or painted.stat().st_size < imaging.MIN_STILL_BYTES:
            raise RuntimeError("the hand repair produced no usable image")
        _blend_in(path, painted, mask)
        return {"action": "repaired", "hands": len(boxes),
                "seconds": round(time.time() - started, 1)}
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        return {"action": "failed", "hands": len(boxes), "error": str(exc)[-300:]}
    finally:
        painted.unlink(missing_ok=True)
        mask_path.unlink(missing_ok=True)


def apply(path: Path, prompt: str = "", force: bool = False) -> dict:
    """Polish one still in place. Never raises: painting must not depend on it."""
    global _last_action, _last_error
    if not enabled():
        _last_action = "disabled"
        return {"action": "disabled"}
    if not path.exists():
        return {"action": "missing"}
    if not detectors.available():
        _last_action = "no detectors"
        _last_error = "opencv or onnxruntime is not importable in this build"
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

    if not config.IMAGE_PYTHON.exists():
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
    job = {
        "prompt": (prompt or "monochrome manga illustration").rstrip() + CLEAN_SUFFIX,
        "out": str(out),
        "width": width,
        "height": height,
        "seed": int(digest[:8], 16),
        "steps": STEPS,
        "reference": str(path),
    }
    started = time.time()
    try:
        imaging.paint_job(job, timeout=900)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        _write_record(path, {"version": POLISH_VERSION, "action": "failed",
                             "error": str(exc), "output_hash": digest})
        return {"action": "failed", "error": str(exc)}

    if not out.exists() or out.stat().st_size < imaging.MIN_STILL_BYTES:
        tail = "the painter produced no usable image"
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
    repair = _repair_hands(path, info, width, height, int(digest[:8], 16))
    if repair.get("action") == "repaired":
        after = detectors.inspect(_bgr(path))
    record = {
        "version": POLISH_VERSION,
        "action": "refined",
        "steps": STEPS,
        "seconds": round(time.time() - started, 1),
        "input_hash": digest,
        "output_hash": _digest(path),
        "faces_before": len(info["faces"]),
        "faces_after": len(after.get("faces") or []),
        "people_before": len(info["people"]),
        "people_after": len(after.get("people") or []),
        "hands_before": len(info.get("hands") or []),
        "hands_after": len(after.get("hands") or []),
        "hand_repair": repair,
        "flags": info.get("flags") or [],
    }
    _write_record(path, record)
    _last_action = "refined"
    _last_error = None
    return record
