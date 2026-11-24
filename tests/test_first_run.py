"""Tests for the first-run gate: the server half, and the window half in a real browser."""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from murmur import bootstrap, config, server


class FirstRunFlagTest(unittest.TestCase):
    def setUp(self):
        # The suite runs without weights, which is exactly the stranger's case.
        self.client = TestClient(server.app)

    def test_a_fresh_install_reports_that_it_needs_a_voice_and_will_fetch_it(self):
        with mock.patch.object(config, "voice_model_problem",
                               return_value="kokoro-v1.0.onnx is missing"):
            health = self.client.get("/api/health").json()
        self.assertEqual(health["firstRun"], {"needed": True, "auto": True})

    def test_the_killswitch_keeps_the_old_behaviour(self):
        with mock.patch.object(config, "voice_model_problem",
                               return_value="kokoro-v1.0.onnx is missing"), \
                mock.patch.dict("os.environ", {"MURMUR_FIRST_RUN": "0"}):
            health = self.client.get("/api/health").json()
        self.assertTrue(health["firstRun"]["needed"])
        self.assertFalse(health["firstRun"]["auto"])

    def test_a_ready_install_does_not_ask_for_anything(self):
        with mock.patch.object(config, "voice_model_problem", return_value=None):
            health = self.client.get("/api/health").json()
        self.assertFalse(health["firstRun"]["needed"])

    def test_the_status_route_answers_ready_and_state(self):
        with mock.patch.object(config, "voice_model_ready", return_value=True):
            status = self.client.get("/api/voice-model").json()
        self.assertIn("ready", status)
        self.assertIn("state", status)
        self.assertIn("progress", status["state"])


class ProgressTest(unittest.TestCase):
    def test_the_state_always_carries_a_progress_slot(self):
        # The window polls this once a second; a missing key is a broken bar.
        self.assertIn("progress", bootstrap.state())

    def test_a_download_reports_bytes_as_it_goes(self):
        seen = []

        class FakeResponse:
            # urlopen is read in chunks now (with a timeout), instead of urlretrieve.
            headers = {"content-length": "40"}

            def __init__(self) -> None:
                self.chunks = [b"x" * 10, b"x" * 10, b"x" * 10, b""]

            def read(self, size: int) -> bytes:
                chunk = self.chunks.pop(0)
                seen.append(bootstrap.state()["progress"])
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> bool:
                return False

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()), \
                mock.patch.object(config, "weight_problem", return_value="missing"), \
                mock.patch.object(config, "MODEL_MIN_BYTES", 1):
            bootstrap._download("https://example.invalid/x", __import__("pathlib").Path(
                self._tmpfile()), "Test", 1)
        self.assertTrue(seen)
        # The first read happens before any progress exists; the shape the UI reads is
        # whatever the download reported from the first chunk on.
        sample = next(s for s in seen if s)
        for key in ("label", "downloaded", "total", "percent", "mbPerSecond"):
            self.assertIn(key, sample)

    def _tmpfile(self) -> str:
        import tempfile

        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
        handle.close()
        return handle.name


if __name__ == "__main__":
    unittest.main()
