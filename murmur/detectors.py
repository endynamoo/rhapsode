"""Find faces, hands and bodies in a painted still.

Two detectors, both small and both permissively licensed:

* **YuNet** (via OpenCV, MIT weights) for faces — precise boxes, ~10 ms.
* **RTMDet** (Apache-2.0) for people — a box per person, which the anatomy
  check and the pose model both hang off.

The models are fetched once into ``<data>/models/polish`` and cached. Detection
is CPU work: the boxes cost milliseconds, the GPU is for painting.
"""
from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

import numpy as np

from . import config

FACES_MODEL = "yunet.onnx"
PERSON_MODEL = "rtmdet-n-person.onnx"
POSE_MODEL = "rtmpose-m.onnx"

MODEL_URLS = {
    FACES_MODEL: "https://huggingface.co/bukuroo/YuNet-ONNX/resolve/main/yunet.onnx",
    PERSON_MODEL: "https://huggingface.co/bukuroo/RTMDet-ONNX/resolve/main/rtmdet-n-person.onnx",
    POSE_MODEL: "https://huggingface.co/bukuroo/RTMPose-ONNX/resolve/main/rtmpose-m.onnx",
}

# mmdet's preprocessing: BGR, ImageNet means in BGR order, no rescale.
_MEAN = np.array([103.53, 116.28, 123.675], dtype=np.float32)
_STD = np.array([57.375, 57.12, 58.395], dtype=np.float32)

_lock = threading.Lock()
_sessions: dict[str, object] = {}
_download_failed = False


class DetectorUnavailable(Exception):
    pass


def models_dir() -> Path:
    return config.HOME / "models" / "polish"


def model_path(name: str) -> Path:
    return models_dir() / name


def _fetch(name: str) -> Path:
    """Download a detector once; offline machines get a clear error."""
    global _download_failed
    path = model_path(name)
    if path.exists() and path.stat().st_size > 1024:
        return path
    url = MODEL_URLS.get(name)
    if not url:
        raise DetectorUnavailable(f"no download URL for {name}")
    if _download_failed:
        raise DetectorUnavailable(
            f"{name} is missing and could not be downloaded — polish needs it."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001 - surfaced as an app-level error
        _download_failed = True
        tmp.unlink(missing_ok=True)
        raise DetectorUnavailable(
            f"could not download {name} ({exc}); connect once so polish can fetch it"
        ) from exc
    return path


def _session(name: str):
    import onnxruntime as ort

    with _lock:
        if name in _sessions:
            return _sessions[name]
        session = ort.InferenceSession(
            str(_fetch(name)), providers=["CPUExecutionProvider"]
        )
        _sessions[name] = session
        return session


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def _letterbox(array_bgr: np.ndarray, size: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    height, width = array_bgr.shape[:2]
    scale = min(size / width, size / height)
    new = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    resized = cv2_resize(array_bgr, new)
    canvas = np.zeros((size, size, 3), dtype=np.float32)
    pad = ((size - new[0]) // 2, (size - new[1]) // 2)
    canvas[pad[1]:pad[1] + new[1], pad[0]:pad[0] + new[0]] = resized.astype(np.float32)
    normalised = (canvas - _MEAN) / _STD
    return normalised.transpose(2, 0, 1)[None], scale, pad


def cv2_resize(array: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    import cv2

    return cv2.resize(array, size, interpolation=cv2.INTER_LINEAR)


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def faces(image_bgr: np.ndarray, threshold: float = 0.6) -> list[tuple[float, float, float, float]]:
    """Face boxes in pixel coordinates."""
    import cv2

    session_path = _fetch(FACES_MODEL)
    height, width = image_bgr.shape[:2]
    detector = cv2.FaceDetectorYN_create(str(session_path), "", (width, height), threshold, 0.3, 5000)
    _count, found = detector.detect(image_bgr)
    if found is None:
        return []
    boxes = []
    for face in found:
        x, y, w, h = (float(v) for v in face[:4])
        boxes.append((x, y, x + w, y + h))
    return boxes


def people(image_bgr: np.ndarray, threshold: float = 0.35) -> list[tuple[float, float, float, float]]:
    """Person boxes in pixel coordinates (RTMDet-n, NMS already applied)."""
    session = _session(PERSON_MODEL)
    tensor, scale, (pad_x, pad_y) = _letterbox(image_bgr, 320)
    dets, _labels = session.run(None, {"input": tensor})
    height, width = image_bgr.shape[:2]
    boxes = []
    for det in dets[0]:
        score = float(det[4])
        if score < threshold:
            continue
        box = (
            (float(det[0]) - pad_x) / scale,
            (float(det[1]) - pad_y) / scale,
            (float(det[2]) - pad_x) / scale,
            (float(det[3]) - pad_y) / scale,
        )
        if (box[2] - box[0]) < 16 or (box[3] - box[1]) < 16:
            continue
        if box[0] > width or box[1] > height or box[2] < 0 or box[3] < 0:
            continue
        boxes.append(box)
    # The export leaves near-duplicates behind; keep the best of each cluster.
    boxes.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    kept: list[tuple[float, float, float, float]] = []
    for box in boxes:
        if all(_iou(box, other) < 0.6 for other in kept):
            kept.append(box)
    return kept


def inspect(image_bgr: np.ndarray) -> dict:
    """Everything the polisher needs to decide whether a still is worth repair."""
    result = {"faces": [], "people": [], "error": None}
    try:
        result["faces"] = faces(image_bgr)
    except DetectorUnavailable as exc:
        result["error"] = str(exc)
    try:
        result["people"] = people(image_bgr)
    except DetectorUnavailable as exc:
        result["error"] = result["error"] or str(exc)
    return result
