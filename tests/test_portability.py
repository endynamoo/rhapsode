"""Portability seams: data dirs, OCR backends, TTS providers, image conversion."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from murmur import config, ocr, store
from murmur.backends import detect, tts


class DataHomeTests(unittest.TestCase):
    def test_macos_keeps_legacy_home(self) -> None:
        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(config._default_home(), Path.home() / ".murmur")

    def test_windows_uses_local_app_data(self) -> None:
        with mock.patch("sys.platform", "win32"), mock.patch.dict(
            os.environ, {"LOCALAPPDATA": "/tmp/LocalAppData"}
        ):
            self.assertEqual(config._default_home(), Path("/tmp/LocalAppData/Rhapsode"))

    def test_linux_uses_xdg_data_home(self) -> None:
        with mock.patch("sys.platform", "linux"), mock.patch.dict(
            os.environ, {"XDG_DATA_HOME": "/tmp/xdg"}
        ):
            self.assertEqual(config._default_home(), Path("/tmp/xdg/rhapsode"))

    def test_linux_falls_back_to_local_share(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != "XDG_DATA_HOME"}
        with mock.patch("sys.platform", "linux"), mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                config._default_home(), Path.home() / ".local" / "share" / "rhapsode"
            )


class OcrBackendTests(unittest.TestCase):
    def test_vision_is_preferred(self) -> None:
        with mock.patch.object(ocr, "_backend", None), mock.patch.object(
            ocr.VisionBackend, "available", return_value=True
        ), mock.patch.object(
            ocr.WindowsOcrBackend, "available", return_value=True
        ), mock.patch.object(ocr.TesseractBackend, "available", return_value=True):
            self.assertEqual(ocr.backend_name(), "vision")

    def test_windows_is_preferred_over_tesseract(self) -> None:
        with mock.patch.object(ocr, "_backend", None), mock.patch.object(
            ocr.VisionBackend, "available", return_value=False
        ), mock.patch.object(
            ocr.WindowsOcrBackend, "available", return_value=True
        ), mock.patch.object(ocr.TesseractBackend, "available", return_value=True):
            self.assertEqual(ocr.backend_name(), "windows")

    def test_tesseract_is_the_last_fallback(self) -> None:
        with mock.patch.object(ocr, "_backend", None), mock.patch.object(
            ocr.VisionBackend, "available", return_value=False
        ), mock.patch.object(
            ocr.WindowsOcrBackend, "available", return_value=False
        ), mock.patch.object(ocr.TesseractBackend, "available", return_value=True):
            self.assertEqual(ocr.backend_name(), "tesseract")

    def test_no_backend_is_unavailable(self) -> None:
        with mock.patch.object(ocr, "_backend", None), mock.patch.object(
            ocr.VisionBackend, "available", return_value=False
        ), mock.patch.object(
            ocr.WindowsOcrBackend, "available", return_value=False
        ), mock.patch.object(ocr.TesseractBackend, "available", return_value=False):
            self.assertFalse(ocr.available())

    def test_windows_backend_reads_rendered_text(self) -> None:
        """The real engine, when this machine has one: exact text, in order."""
        backend = ocr.WindowsOcrBackend()
        if not backend.available():
            self.skipTest("Windows OCR is not available on this machine")

        import io

        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (900, 260), "white")
        draw = ImageDraw.Draw(image)
        font = None
        for name in ("georgia.ttf", "arial.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(name, 34)
                break
            except OSError:
                continue
        if font is None:
            self.skipTest("no TrueType font to render with")
        draw.text((40, 60), "The quick brown fox jumps.", font=font, fill="black")
        draw.text((40, 140), "Scanned pages read locally.", font=font, fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        text = backend.recognize(buffer.getvalue())
        self.assertIn("quick brown fox", text)
        self.assertIn("Scanned pages", text)
        self.assertTrue(backend.lettering(buffer.getvalue()))

    def test_windows_backend_works_inside_a_running_event_loop(self) -> None:
        """Uploads run on the server's loop; OCR must not need asyncio.run()."""
        backend = ocr.WindowsOcrBackend()
        if not backend.available():
            self.skipTest("Windows OCR is not available on this machine")

        import asyncio
        import io

        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (600, 160), "white")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("georgia.ttf", 32)
        except OSError:
            self.skipTest("no TrueType font to render with")
        draw.text((30, 60), "Loop safe text.", font=font, fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()

        async def inside_the_loop() -> str:
            return backend.recognize(data)

        text = asyncio.run(inside_the_loop())
        self.assertIn("Loop safe", text)


class TtsProviderTests(unittest.TestCase):
    def test_cpu_when_no_accelerated_runtime(self) -> None:
        with mock.patch.dict(os.environ, {"ONNX_PROVIDER": ""}), mock.patch.object(
            tts, "accelerated_installed", return_value=False
        ):
            self.assertEqual(tts.active_provider(), "CPUExecutionProvider")

    def test_reports_available_cuda_provider(self) -> None:
        with mock.patch.dict(os.environ, {"ONNX_PROVIDER": ""}), mock.patch.object(
            tts, "accelerated_installed", return_value=True
        ), mock.patch.object(
            tts,
            "available_providers",
            return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
        ):
            self.assertEqual(tts.active_provider(), "CUDAExecutionProvider")

    def test_env_override_wins(self) -> None:
        with mock.patch.dict(os.environ, {"ONNX_PROVIDER": "DmlExecutionProvider"}):
            self.assertEqual(tts.active_provider(), "DmlExecutionProvider")


class HardwareProbeTests(unittest.TestCase):
    def tearDown(self) -> None:
        detect.reset()

    def test_vendor_names(self) -> None:
        self.assertEqual(detect._vendor("NVIDIA GeForce RTX 4070"), "nvidia")
        self.assertEqual(detect._vendor("AMD Radeon RX 7800 XT"), "amd")
        self.assertEqual(detect._vendor("Intel Arc A770"), "intel")
        self.assertEqual(detect._vendor("Apple M3 Pro"), "apple")
        self.assertEqual(detect._vendor("Virtual Display"), "unknown")

    def test_probe_shape_is_json_ready(self) -> None:
        detect.reset()
        with mock.patch.object(
            detect, "_gpu", return_value=("NVIDIA RTX 4070", "nvidia", 12.0)
        ), mock.patch.object(
            detect.tts, "available_providers", return_value=["CUDAExecutionProvider"]
        ), mock.patch.object(
            detect.tts, "active_provider", return_value="CUDAExecutionProvider"
        ), mock.patch.object(
            detect.tts, "accelerated_installed", return_value=True
        ):
            data = detect.probe()
        self.assertEqual(data["gpuVendor"], "nvidia")
        self.assertEqual(data["vramGb"], 12.0)
        self.assertIn("providers", data)
        self.assertIn("os", data)


class ImageConversionTests(unittest.TestCase):
    def test_bmp_converts_to_png(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "page.bmp"
            target = Path(folder) / "page.png"
            Image.new("RGB", (12, 9), (220, 30, 30)).save(source)
            self.assertTrue(store._convert_to_png(source, target))
            with Image.open(target) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (12, 9))

    def test_png_keeps_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "page.png"
            target = Path(folder) / "out.png"
            Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(source)
            self.assertTrue(store._convert_to_png(source, target))
            with Image.open(target) as image:
                self.assertEqual(image.mode, "RGBA")


class UploadTempFileTests(unittest.TestCase):
    """Windows will not open a temp file that another handle still holds open."""

    def test_pdf_upload_reaches_pdfium(self) -> None:
        import pypdfium2 as pdfium

        from murmur import extract

        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "blank.pdf"
            document = pdfium.PdfDocument.new()
            document.new_page(200, 200)
            document.save(str(sample))
            data = sample.read_bytes()

        # A blank page carries no text, so extraction has to fail cleanly *after*
        # the file opened. A PdfiumError here means the upload was unreadable.
        with self.assertRaises((extract.ExtractError, ocr.OCRUnavailable)):
            extract.extract_upload("blank.pdf", data)

    def test_upload_leaves_no_temp_file_behind(self) -> None:
        from murmur import extract

        before = set(Path(tempfile.gettempdir()).glob("rhapsode-upload-*"))
        with self.assertRaises(extract.ExtractError):
            extract.extract_upload("notes.xyz", b"unsupported")
        after = set(Path(tempfile.gettempdir()).glob("rhapsode-upload-*"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
