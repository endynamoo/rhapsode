"""bootstrap must survive a console that cannot encode what it prints.

The Windows CI job failed here, not in the product logic: `print("  ✓ Library
ready")` raised UnicodeEncodeError on a cp1252 stdout and aborted the first-run
download. This asserts the behaviour on such a console, so it cannot come back.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import bootstrap, config


class ConsoleEncodingTests(unittest.TestCase):
    def test_a_console_without_the_tick_still_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            models = Path(home) / "models"
            models.mkdir()
            model = models / "kokoro-v1.0.onnx"
            voices = models / "voices-v1.0.bin"
            model.write_bytes(b"\0" * 2048)
            voices.write_bytes(b"\0" * 2048)

            raw = io.BytesIO()
            stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")

            with mock.patch.object(config, "MODEL_PATH", model), \
                    mock.patch.object(config, "VOICES_PATH", voices), \
                    mock.patch.object(config, "HOME", Path(home)), \
                    mock.patch.object(config, "MODEL_MIN_BYTES", 1024), \
                    mock.patch.object(config, "VOICES_MIN_BYTES", 1024), \
                    mock.patch.object(config, "ensure_dirs", lambda: None), \
                    mock.patch("murmur.store.init", lambda: None), \
                    mock.patch("sys.stdout", stream):
                # Before the guard this raised UnicodeEncodeError on the tick mark.
                bootstrap.main()
                stream.flush()

            written = raw.getvalue().decode("cp1252", "replace")
            self.assertIn("Library ready", written)

    def test_a_progress_line_survives_the_same_console(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with mock.patch("sys.stdout", stream):
            bootstrap._write("\r    [███░░░░] 12.0%  3.4 MB/s ")
            stream.flush()
        # The bar degrades to question marks, but the run keeps going.
        self.assertIn("12.0%", raw.getvalue().decode("cp1252", "replace"))


if __name__ == "__main__":
    unittest.main()
