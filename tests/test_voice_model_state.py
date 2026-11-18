"""A missing voice model must not leave the UI loading forever.

A first run has no Kokoro weights: the warmup fails and the reason is reported
on `/api/health`. Bootstrap can finish while the app is still open, so the
engine has to notice the files and re-arm itself instead of keeping a stale
"files are missing" forever — which is exactly what an endless
"Loading voice model…" pill was made of.
"""
from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from murmur import config, engine as engine_module, server, store, system_api
from murmur.engine import TTSEngine

MISSING = "Kokoro model files are missing. Run `python -m murmur.bootstrap`."


@contextmanager
def temp_home():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        with mock.patch.multiple(
            config,
            HOME=base,
            MODELS_DIR=base / "models",
            MODEL_PATH=base / "models" / "kokoro-v1.0.onnx",
            VOICES_PATH=base / "models" / "voices-v1.0.bin",
            CACHE_DIR=base / "cache",
            PAGES_DIR=base / "cache" / "pages",
            FILES_DIR=base / "files",
            EXPORT_DIR=base / "exports",
            FILM_DIR=base / "film",
            DB_PATH=base / "murmur.db",
        ):
            config.ensure_dirs()
            store.close()
            store.init()
            try:
                yield base
            finally:
                store.close()


class RecheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = TTSEngine()

    def test_a_stale_missing_error_survives_while_the_files_are_absent(self) -> None:
        with temp_home():
            self.engine.last_error = MISSING
            with mock.patch.object(self.engine, "warm_async") as warm:
                self.engine.recheck()
            warm.assert_not_called()
            self.assertEqual(MISSING, self.engine.last_error)

    def test_the_files_landing_re_arms_the_load(self) -> None:
        with temp_home():
            self.engine.last_error = MISSING
            config.MODEL_PATH.write_bytes(b"onnx")
            config.VOICES_PATH.write_bytes(b"{}")
            with mock.patch.object(self.engine, "warm_async") as warm:
                self.engine.recheck()
            warm.assert_called_once()
            self.assertIsNone(self.engine.last_error)

    def test_a_loaded_engine_is_left_alone(self) -> None:
        with temp_home():
            self.engine._model = object()
            self.engine.last_error = MISSING
            config.MODEL_PATH.write_bytes(b"onnx")
            config.VOICES_PATH.write_bytes(b"{}")
            with mock.patch.object(self.engine, "warm_async") as warm:
                self.engine.recheck()
            warm.assert_not_called()

    def test_a_successful_load_clears_the_reason(self) -> None:
        with temp_home():
            self.engine.last_error = MISSING
            config.MODEL_PATH.write_bytes(b"onnx")
            config.VOICES_PATH.write_bytes(b"{}")
            fake = mock.Mock()
            with mock.patch.dict("sys.modules", {"kokoro_onnx": mock.Mock(Kokoro=mock.Mock(return_value=fake))}), \
                    mock.patch("murmur.engine.tts_backends.configure"):
                self.engine.load()
            self.assertIsNone(self.engine.last_error)
            self.assertTrue(self.engine.ready)


class HealthTests(unittest.TestCase):
    def test_health_reports_the_reason_and_then_gets_past_it(self) -> None:
        with temp_home():
            fake = TTSEngine()
            fake.last_error = MISSING
            with mock.patch.object(system_api, "engine", fake), \
                    mock.patch.object(fake, "warm_async") as warm:
                body = TestClient(server.app).get("/api/health").json()
                self.assertFalse(body["modelReady"])
                self.assertEqual(MISSING, body["modelError"])
                warm.assert_not_called()

                # Bootstrap finishes while the app is open.
                config.MODEL_PATH.write_bytes(b"onnx")
                config.VOICES_PATH.write_bytes(b"{}")
                body = TestClient(server.app).get("/api/health").json()
            self.assertIsNone(body["modelError"])
            warm.assert_called_once()

    def test_engine_sidecar_health_does_the_same(self) -> None:
        from murmur import engine_api

        fake = TTSEngine()
        fake.last_error = MISSING
        with temp_home(), mock.patch.object(engine_api, "engine", fake), \
                mock.patch.object(fake, "warm_async") as warm:
            config.MODEL_PATH.write_bytes(b"onnx")
            config.VOICES_PATH.write_bytes(b"{}")
            body = TestClient(engine_api.app).get("/api/engine/health").json()
        self.assertIsNone(body["modelError"])
        warm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
