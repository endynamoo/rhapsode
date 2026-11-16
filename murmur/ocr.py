"""On-device OCR with pluggable platform backends.

macOS uses Apple's Vision framework (Neural Engine, no model download). Other
platforms can use Tesseract when it is installed. The rest of the app only sees
``available()`` / ``recognize_bytes()`` / ``lettering()``.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys

from . import config, proc

# Languages are auto-detected by Vision; set MURMUR_OCR_LANGS="en-US,fr-FR" to
# force a list (disables auto-detection).
_FORCED = [code.strip() for code in (config.OCR_LANGS or "").split(",") if code.strip()]


class OCRUnavailable(Exception):
    pass


class OcrBackend:
    name = "none"

    def available(self) -> bool:
        return False

    def recognize(self, data: bytes) -> str:
        raise OCRUnavailable("No OCR backend is available on this machine.")

    def lettering(self, data: bytes, min_height: float = 0.01, min_chars: int = 2) -> bool:
        return False


class VisionBackend(OcrBackend):
    """Apple Vision text recognition."""

    name = "vision"

    def available(self) -> bool:
        try:
            import Vision  # noqa: F401

            return True
        except Exception:
            return False

    def _request(self):
        import Vision

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        if _FORCED:
            try:
                request.setRecognitionLanguages_(_FORCED)
            except Exception:
                pass
        else:
            try:
                request.setAutomaticallyDetectsLanguage_(True)
            except Exception:
                pass
        return request

    def _observations(self, data: bytes):
        import Vision
        from Foundation import NSData

        if not data:
            return []
        payload = NSData.dataWithBytes_length_(data, len(data))
        request = self._request()
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(payload, None)
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise OCRUnavailable(f"Vision could not read the image: {error}")
        return request.results() or []

    def recognize(self, data: bytes) -> str:
        try:
            import Vision  # noqa: F401
            from Foundation import NSData  # noqa: F401
        except Exception as exc:  # pragma: no cover
            raise OCRUnavailable(
                "OCR needs PyObjC: uv pip install pyobjc-framework-Vision"
            ) from exc

        lines = []
        for observation in self._observations(data):
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            text = candidates[0].string()
            if not text or not text.strip():
                continue
            box = observation.boundingBox()
            # Vision's origin is bottom-left, so larger y means higher on the page.
            lines.append((round(box.origin.y, 2), box.origin.x, text.strip()))

        lines.sort(key=lambda line: (-line[0], line[1]))
        text = "\n".join(line[2] for line in lines)
        return re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    def lettering(self, data: bytes, min_height: float = 0.01, min_chars: int = 2) -> bool:
        if not data:
            return False
        try:
            observations = self._observations(data)
        except Exception:
            return False
        for observation in observations:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            text = candidates[0].string().strip()
            if len(text) < min_chars:
                continue
            box = observation.boundingBox()
            if box.size.height >= min_height and box.size.width >= 0.01:
                return True
        return False


class TesseractBackend(OcrBackend):
    """Tesseract CLI fallback for Windows and Linux (opt-in install)."""

    name = "tesseract"

    def _exe(self) -> str | None:
        return config.find_tool("tesseract")

    def available(self) -> bool:
        return bool(self._exe())

    def _run(self, data: bytes, *args: str) -> bytes:
        exe = self._exe()
        if not exe:
            raise OCRUnavailable("Tesseract is not installed.")
        try:
            result = proc.run(
                [exe, "stdin", "stdout", *args],
                input=data,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OCRUnavailable(f"Tesseract could not run: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise OCRUnavailable(f"Tesseract failed: {detail or 'unknown error'}")
        return result.stdout

    def recognize(self, data: bytes) -> str:
        raw = self._run(data, "--psm", "6")
        text = raw.decode("utf-8", "replace").strip()
        return re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    def lettering(self, data: bytes, min_height: float = 0.01, min_chars: int = 2) -> bool:
        if not data:
            return False
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                page_width, page_height = image.size
        except Exception:
            page_width = page_height = 0

        try:
            raw = self._run(data, "--psm", "6", "tsv")
        except OCRUnavailable:
            return False

        for row in raw.decode("utf-8", "replace").splitlines()[1:]:
            columns = row.split("\t")
            if len(columns) < 12:
                continue
            text = columns[11].strip()
            if len(text) < min_chars:
                continue
            try:
                width = float(columns[8])
                height = float(columns[9])
            except ValueError:
                continue
            if not page_height or not page_width:
                continue
            if height / page_height >= min_height and width / page_width >= 0.01:
                return True
        return False


_backend: OcrBackend | None = None


def backend() -> OcrBackend:
    """Pick the best OCR backend once per process."""
    global _backend
    if _backend is None:
        vision = VisionBackend()
        _backend = vision if vision.available() else TesseractBackend()
    return _backend


def backend_name() -> str:
    return backend().name


def available() -> bool:
    return backend().available()


def recognize_bytes(data: bytes) -> str:
    """OCR an encoded image (PNG, JPEG, TIFF, BMP, …) and return plain text."""
    return backend().recognize(data)


def lettering(data: bytes, min_height: float = 0.01, min_chars: int = 2) -> bool:
    """True when the image contains a probabilistically large text region."""
    return backend().lettering(data, min_height, min_chars)


def clipboard_image() -> bytes | None:
    """Return the image currently on the clipboard, if any."""
    if sys.platform == "darwin":
        try:
            from AppKit import NSPasteboard, NSPasteboardTypePNG, NSPasteboardTypeTIFF
        except Exception:
            pass
        else:
            pasteboard = NSPasteboard.generalPasteboard()
            for kind in (NSPasteboardTypePNG, NSPasteboardTypeTIFF):
                try:
                    data = pasteboard.dataForType_(kind)
                except Exception:
                    continue
                if data is not None and len(data) > 0:
                    return bytes(data)

    try:
        from PIL import ImageGrab

        image = ImageGrab.grabclipboard()
        if image is not None and hasattr(image, "save"):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:
        pass
    return None
