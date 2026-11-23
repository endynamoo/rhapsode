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


if __name__ == "__main__":
    unittest.main()
