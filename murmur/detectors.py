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

# RTMPose wants RGB at 192x256 with the same means in RGB order.
_POSE_MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
_POSE_STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

KEYPOINTS = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)

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
    try:
        import onnxruntime as ort
    except Exception as exc:  # noqa: BLE001 - any import failure means unavailable
        raise DetectorUnavailable(f"onnxruntime is not installed ({exc})") from exc

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

        _quiet_cv2()
        return True
    except Exception:
        return False


def _cv2():
    """OpenCV, or a reason it cannot be used - never a bare ImportError.

    `inspect()` reports what stopped it in `result["error"]`, which is what the
    still's sidecar and Settings show. A raw ModuleNotFoundError escaping from
    here would instead abort the whole polish pass on a source install that has
    the detectors' wheels but not opencv.
    """
    try:
        import cv2
    except Exception as exc:  # noqa: BLE001 - any import failure means unavailable
        raise DetectorUnavailable(f"opencv is not installed ({exc})") from exc
    _quiet_cv2()
    return cv2


def _quiet_cv2() -> None:
    """OpenCV logs a DNN-backend warning on every FaceDetectorYN; drop it."""
    try:
        import cv2

        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass


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
    cv2 = _cv2()

    return cv2.resize(array, size, interpolation=cv2.INTER_LINEAR)


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def faces(image_bgr: np.ndarray, threshold: float = 0.6) -> list[tuple[float, float, float, float]]:
    """Face boxes in pixel coordinates."""
    cv2 = _cv2()

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


def pose(image_bgr: np.ndarray, box: tuple[float, float, float, float]) -> list[dict]:
    """17 COCO keypoints for one person, in image pixels.

    RTMPose outputs SimCC: separate x/y logits per keypoint over 384/512 bins.
    Per-bin probability is meaningless (a good pose still scores ~0.004 because
    the mass is spread over hundreds of bins), so the geometry is what counts.
    """
    cv2 = _cv2()

    session = _session(POSE_MODEL)
    x1, y1, x2, y2 = box
    height, width = image_bgr.shape[:2]
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(width), x2), min(float(height), y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return []

    crop = image_bgr[int(y1):int(y2), int(x1):int(x2)]
    rgb = crop[:, :, ::-1]
    resized = cv2.resize(rgb, (192, 256), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    tensor = ((resized - _POSE_MEAN) / _POSE_STD).transpose(2, 0, 1)[None]
    simcc_x, simcc_y = session.run(None, {"input": tensor})

    xs = simcc_x[0].argmax(axis=1) / 2.0
    ys = simcc_y[0].argmax(axis=1) / 2.0
    crop_w, crop_h = x2 - x1, y2 - y1
    points = []
    for index, name in enumerate(KEYPOINTS):
        points.append({
            "name": name,
            "x": float(x1 + xs[index] / 192.0 * crop_w),
            "y": float(y1 + ys[index] / 256.0 * crop_h),
        })
    return points


def hands(image_bgr: np.ndarray) -> list[tuple[float, float, float, float]]:
    """Hand boxes derived from wrist keypoints, one per visible hand."""
    height, width = image_bgr.shape[:2]
    found: list[tuple[float, float, float, float]] = []
    for person in people(image_bgr):
        points = pose(image_bgr, person)
        if not points:
            continue
        by_name = {point["name"]: point for point in points}
        body = max(48.0, (person[2] - person[0]) * 0.18)
        for side in ("left", "right"):
            wrist = by_name.get(f"{side}_wrist")
            elbow = by_name.get(f"{side}_elbow")
            if not wrist:
                continue
            # A wrist outside the person's box is a decode failure, not a hand.
            margin = (person[2] - person[0]) * 0.08
            if not (person[0] - margin <= wrist["x"] <= person[2] + margin
                    and person[1] - margin <= wrist["y"] <= person[3] + margin):
                continue
            size = body
            if elbow:
                dx, dy = wrist["x"] - elbow["x"], wrist["y"] - elbow["y"]
                length = max(1.0, (dx * dx + dy * dy) ** 0.5)
                # Push the box past the wrist, in the direction the arm travels.
                size = min(body * 1.4, max(body, length * 0.6))
            found.append((
                wrist["x"] - size / 2,
                wrist["y"] - size / 2,
                wrist["x"] + size / 2,
                wrist["y"] + size / 2,
            ))
    kept: list[tuple[float, float, float, float]] = []
    for box in found:
        clipped = (max(0.0, box[0]), max(0.0, box[1]),
                   min(float(width), box[2]), min(float(height), box[3]))
        if clipped[2] - clipped[0] < 12 or clipped[3] - clipped[1] < 12:
            continue
        if all(_iou(clipped, other) < 0.5 for other in kept):
            kept.append(clipped)
    return kept


def anatomy_flags(image_bgr: np.ndarray) -> list[str]:
    """Cheap sanity checks on each detected skeleton. Empty list means plausible."""
    flags: list[str] = []
    for index, person in enumerate(people(image_bgr)):
        points = {point["name"]: point for point in pose(image_bgr, person)}
        if not points:
            continue
        height = max(1.0, person[3] - person[1])
        width = max(1.0, person[2] - person[0])
        for side in ("left", "right"):
            shoulder = points.get(f"{side}_shoulder")
            elbow = points.get(f"{side}_elbow")
            wrist = points.get(f"{side}_wrist")
            if not (shoulder and elbow and wrist):
                continue
            upper = _distance(shoulder, elbow)
            lower = _distance(elbow, wrist)
            if upper < 1 or lower < 1:
                continue
            ratio = upper / lower
            if ratio > 2.6 or ratio < 0.35:
                flags.append(f"person {index}: {side} arm segments out of proportion ({ratio:.2f})")
            if _distance(shoulder, wrist) > height * 1.1:
                flags.append(f"person {index}: {side} arm longer than the body")
        if points.get("left_wrist") and points.get("right_wrist"):
            if _distance(points["left_wrist"], points["right_wrist"]) < width * 0.04:
                flags.append(f"person {index}: both wrists in the same place")
    return flags


def _distance(a: dict, b: dict) -> float:
    return ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5


def inspect(image_bgr: np.ndarray, face_threshold: float = 0.45) -> dict:
    """Everything the polisher needs to decide whether a still is worth repair.

    The face threshold sits below YuNet's default: clean ink linework scores lower
    than a photograph, so 0.6 misses faces that are plainly there.
    """
    result = {"faces": [], "people": [], "hands": [], "flags": [], "error": None}
    try:
        result["faces"] = faces(image_bgr, threshold=face_threshold)
    except DetectorUnavailable as exc:
        result["error"] = str(exc)
    try:
        result["people"] = people(image_bgr)
        if result["people"]:
            result["hands"] = hands(image_bgr)
            result["flags"] = anatomy_flags(image_bgr)
    except DetectorUnavailable as exc:
        result["error"] = result["error"] or str(exc)
    return result
