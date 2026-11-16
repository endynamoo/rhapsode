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
        ), mock.patch.object(ocr.TesseractBackend, "available", return_value=True):
            self.assertEqual(ocr.backend_name(), "vision")

    def test_tesseract_is_the_fallback(self) -> None:
        with mock.patch.object(ocr, "_backend", None), mock.patch.object(
            ocr.VisionBackend, "available", return_value=False
        ), mock.patch.object(ocr.TesseractBackend, "available", return_value=True):
            self.assertEqual(ocr.backend_name(), "tesseract")

    def test_no_backend_is_unavailable(self) -> None:
        with mock.patch.object(ocr, "_backend", None), mock.patch.object(
            ocr.VisionBackend, "available", return_value=False
        ), mock.patch.object(ocr.TesseractBackend, "available", return_value=False):
            self.assertFalse(ocr.available())


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


if __name__ == "__main__":
    unittest.main()
