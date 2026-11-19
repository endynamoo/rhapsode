"""A stub weight file must be reported, repaired and named - not handed to ONNX.

The report was a 4-byte `kokoro-v1.0.onnx` that passed every `exists()` check: the
engine threw a raw protobuf error into the reader, `python -m murmur.bootstrap`
printed "already downloaded", and `/api/health` only said the model was not ready.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import bootstrap, config


class VoiceModelPredicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        models = Path(self.home.name) / "models"
        models.mkdir()
        patcher = mock.patch.object(config, "MODEL_PATH", models / "kokoro-v1.0.onnx")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(config, "VOICES_PATH", models / "voices-v1.0.bin")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, path: Path, size: int) -> None:
        path.write_bytes(b"\0" * size)

    def test_a_stub_is_not_ready(self) -> None:
        self.write(config.MODEL_PATH, 4)
        self.write(config.VOICES_PATH, 2)
        self.assertFalse(config.voice_model_ready())
        problem = config.voice_model_problem()
        self.assertIn("kokoro-v1.0.onnx", problem)
        self.assertIn("4 bytes", problem)

    def test_a_missing_file_is_named(self) -> None:
        self.assertIn("kokoro-v1.0.onnx is missing", config.voice_model_problem())

    def test_the_voices_are_checked_too(self) -> None:
        self.write(config.MODEL_PATH, config.MODEL_MIN_BYTES + 1)
        self.write(config.VOICES_PATH, 10)
        self.assertIn("voices-v1.0.bin", config.voice_model_problem())

    def test_a_real_pair_is_ready(self) -> None:
        self.write(config.MODEL_PATH, config.MODEL_MIN_BYTES + 1)
        self.write(config.VOICES_PATH, config.VOICES_MIN_BYTES + 1)
        self.assertTrue(config.voice_model_ready())
        self.assertIsNone(config.voice_model_problem())


class BootstrappedStateTests(unittest.TestCase):
    def test_a_stub_is_downloaded_again(self) -> None:
        # `main()` used to skip the download whenever the files existed.
        with tempfile.TemporaryDirectory() as home:
            models = Path(home) / "models"
            models.mkdir()
            model = models / "kokoro-v1.0.onnx"
            voices = models / "voices-v1.0.bin"
            model.write_bytes(b"\0" * 4)
            voices.write_bytes(b"\0" * 2)
            def fake_download(url, dest, label, minimum):
                dest.write_bytes(b"\0" * (minimum + 1))

            with mock.patch.object(config, "MODEL_PATH", model), \
                    mock.patch.object(config, "VOICES_PATH", voices), \
                    mock.patch.object(config, "HOME", Path(home)), \
                    mock.patch.object(config, "ensure_dirs", lambda: None), \
                    mock.patch.object(bootstrap, "_download", side_effect=fake_download) as download, \
                    mock.patch("murmur.store.init", lambda: None):
                bootstrap.main()
                self.assertEqual(download.call_count, 2, "the stub was not re-downloaded")
                self.assertEqual(download.call_args_list[0].args[3], config.MODEL_MIN_BYTES)
                # Inside the patches: once they exit, config points at the real
                # paths again, so this would be asserting about a different file.
                self.assertTrue(config.voice_model_ready())


class RepairStateTests(unittest.TestCase):
    def test_start_download_refuses_to_run_twice(self) -> None:
        with mock.patch.object(config, "voice_model_ready", lambda: False), \
                mock.patch.object(bootstrap, "download_weights") as weights, \
                mock.patch.object(bootstrap.threading, "Thread") as thread:
            bootstrap._STATE.update(running=False, done=False, error=None)
            bootstrap.start_download()
            thread.return_value.start.assert_called_once()
            with self.assertRaises(RuntimeError):
                bootstrap.start_download()
            weights.assert_not_called()  # the thread owns the work

    def test_state_is_a_copy(self) -> None:
        state = bootstrap.state()
        state["running"] = "mutated"
        self.assertIsNot(bootstrap.state()["running"], "mutated")


if __name__ == "__main__":
    unittest.main()
