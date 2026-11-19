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

    def word_boxes(self, data: bytes) -> dict | None:
        """Per-word boxes in image pixels, or None when the backend cannot say.

        A scanned page has no text layer, so the only way to draw a mark on the
        page itself is to ask the recogniser where each word sat. Backends that
        report whole lines only return None rather than a guess.
        """
        return None


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


class WindowsOcrBackend(OcrBackend):
    """Windows.Media.Ocr — the engine behind Snipping Tool's text actions.

    On-device, no install and no model download: the language packs Windows
    already has for text-to-speech and the touch keyboard are what it reads
    with. Larger images are scaled down to the engine's limit first.
    """

    name = "windows"

    def _engine(self):
        from winsdk.windows.media.ocr import OcrEngine

        if _FORCED:
            from winsdk.windows.globalization import Language

            for code in _FORCED:
                engine = OcrEngine.try_create_from_language(Language(code))
                if engine is not None:
                    return engine
            raise OCRUnavailable(
                "Windows has no OCR language pack for: " + ", ".join(_FORCED)
            )
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise OCRUnavailable(
                "Windows has no OCR language pack installed "
                "(Settings → Time & language → Language & region)."
            )
        return engine

    def available(self) -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            self._engine()
            return True
        except Exception:
            return False

    def _prepare(self, data: bytes) -> bytes:
        """Scale down past the engine's limit; OCR ignores detail we do not need."""
        from winsdk.windows.media.ocr import OcrEngine

        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                limit = OcrEngine.max_image_dimension
                if max(image.size) <= limit:
                    return data
                ratio = limit / max(image.size)
                size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
                buffer = io.BytesIO()
                image.convert("RGB").resize(size, Image.LANCZOS).save(buffer, format="PNG")
                return buffer.getvalue()
        except Exception:
            return data

    def _lines(self, data: bytes):
        import asyncio
        import threading

        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        engine = self._engine()

        async def recognise():
            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream)
            writer.write_bytes(self._prepare(data))
            await writer.store_async()
            writer.detach_stream()
            stream.seek(0)
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            result = await engine.recognize_async(bitmap)
            return (
                [
                    (line.text, [(word.text, word.bounding_rect) for word in line.words])
                    for line in result.lines
                ],
                int(decoder.pixel_width),
                int(decoder.pixel_height),
            )

        # Uploads arrive on the server's event loop, where asyncio.run() refuses to
        # start; a private loop on its own thread works from either side.
        outcome: dict = {}

        def runner() -> None:
            loop = asyncio.new_event_loop()
            try:
                outcome["value"] = loop.run_until_complete(recognise())
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
                outcome["error"] = exc
            finally:
                loop.close()

        thread = threading.Thread(target=runner, name="windows-ocr", daemon=True)
        thread.start()
        thread.join()
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value", [])

    def _read(self, data: bytes):
        if not data:
            return [], 0, 0
        try:
            return self._lines(data)
        except OCRUnavailable:
            raise
        except Exception as exc:
            raise OCRUnavailable(f"Windows OCR could not read the image: {exc}") from exc

    def recognize(self, data: bytes) -> str:
        lines, _width, _height = self._read(data)
        text = "\n".join(line.strip() for line, _words in lines if line.strip())
        return re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    def word_boxes(self, data: bytes) -> dict | None:
        lines, width, height = self._read(data)
        if not width or not height:
            return None
        words = [
            {
                "text": str(text),
                "x": float(rect.x),
                "y": float(rect.y),
                "w": float(rect.width),
                "h": float(rect.height),
            }
            for _line, pairs in lines
            for text, rect in pairs
            if str(text).strip()
        ]
        if not words:
            return None
        return {"width": width, "height": height, "words": words}

    def lettering(self, data: bytes, min_height: float = 0.01, min_chars: int = 2) -> bool:
        if not data:
            return False
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                page_width, page_height = image.size
        except Exception:
            return False
        if not page_width or not page_height:
            return False
        try:
            lines, _width, _height = self._lines(data)
        except Exception:
            return False
        for _line, words in lines:
            for word, rect in words:
                if len(word.strip()) < min_chars:
                    continue
                if rect.height / page_height >= min_height and rect.width / page_width >= 0.01:
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

    def word_boxes(self, data: bytes) -> dict | None:
        if not data:
            return None
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                page_width, page_height = image.size
        except Exception:
            return None
        try:
            raw = self._run(data, "--psm", "6", "tsv")
        except OCRUnavailable:
            return None
        words: list[dict] = []
        for row in raw.decode("utf-8", "replace").splitlines()[1:]:
            columns = row.split("\t")
            if len(columns) < 12 or columns[0] != "5":
                continue
            text = columns[11].strip()
            if not text:
                continue
            try:
                words.append(
                    {
                        "text": text,
                        "x": float(columns[6]),
                        "y": float(columns[7]),
                        "w": float(columns[8]),
                        "h": float(columns[9]),
                    }
                )
            except ValueError:
                continue
        if not words or not page_width or not page_height:
            return None
        return {"width": page_width, "height": page_height, "words": words}

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
        for candidate in (VisionBackend(), WindowsOcrBackend(), TesseractBackend()):
            if candidate.available():
                _backend = candidate
                break
        else:
            _backend = TesseractBackend()
    return _backend


def backend_name() -> str:
    return backend().name


def available() -> bool:
    return backend().available()


def recognize_bytes(data: bytes) -> str:
    """OCR an encoded image (PNG, JPEG, TIFF, BMP, …) and return plain text."""
    return backend().recognize(data)


def word_boxes_bytes(data: bytes) -> dict | None:
    """Where each word sat in an image, in pixels, or None when unsupported."""
    try:
        return backend().word_boxes(data)
    except OCRUnavailable:
        return None


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
