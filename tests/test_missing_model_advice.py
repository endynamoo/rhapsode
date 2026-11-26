"""What a reader is told when the voice weights are missing.

The fresh-install harness found that a packaged build said "Run `python -m
murmur.bootstrap`" - advice with no interpreter behind it. The message has to name
something in the window the reader is looking at.
"""
from __future__ import annotations

import unittest
from unittest import mock

from murmur import config, engine


class MissingModelAdviceTest(unittest.TestCase):
    def test_a_packaged_build_points_at_the_button(self):
        with mock.patch.object(config, "FROZEN", True):
            advice = engine.missing_model_advice()
        self.assertIn("Settings", advice)
        self.assertIn("Download", advice)
        self.assertNotIn("python -m", advice)

    def test_a_source_checkout_keeps_the_command(self):
        with mock.patch.object(config, "FROZEN", False):
            advice = engine.missing_model_advice()
        self.assertIn("python -m murmur.bootstrap", advice)

    def test_the_error_text_carries_the_advice(self):
        # The text reaches the reader through health.modelError and the voice pill.
        with mock.patch.object(config, "FROZEN", True), \
                mock.patch.object(config, "voice_model_problem", return_value="kokoro-v1.0.onnx is missing"), \
                mock.patch.object(engine.tts_backends, "configure"):
            engine.engine._model = None
            with self.assertRaises(FileNotFoundError) as caught:
                engine.engine.load()
        message = str(caught.exception)
        self.assertIn("kokoro-v1.0.onnx is missing", message)
        self.assertIn("Download", message)

    def test_a_lean_environment_still_gets_the_model_advice(self):
        """The model check must come before the TTS import.

        CI installs requirements-test.txt, which deliberately has no kokoro-onnx; on a
        fresh install the reader must still be told the actionable thing rather than
        getting a ModuleNotFoundError from an import they cannot act on.
        """
        import builtins

        real_import = builtins.__import__

        def lean(name, *args, **kwargs):
            if name == "kokoro_onnx":
                raise ModuleNotFoundError("No module named 'kokoro_onnx'")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(config, "FROZEN", True), \
                mock.patch.object(config, "voice_model_problem", return_value="kokoro-v1.0.onnx is missing"), \
                mock.patch.object(engine.tts_backends, "configure"), \
                mock.patch.object(builtins, "__import__", side_effect=lean):
            engine.engine._model = None
            with self.assertRaises(FileNotFoundError) as caught:
                engine.engine.load()
        self.assertIn("Download", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
