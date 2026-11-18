"""What a placeholder says about itself (issue #40)."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from murmur import imaging


class PlaceholderReasonTests(unittest.TestCase):
    def setUp(self) -> None:
        imaging._PLACEHOLDER_WHY = None
        self.addCleanup(setattr, imaging, "_PLACEHOLDER_WHY", None)

    def test_no_engine_names_the_missing_interpreter_and_the_fix(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "image-venv" / "Scripts" / "python.exe"
            with mock.patch.object(imaging.config, "IMAGE_PYTHON", missing):
                reason = imaging.placeholder_reason()
        self.assertIn(str(missing), reason)
        self.assertIn("setup_image.ps1", reason)
        self.assertIn("placeholder", reason)
        # The old wording blamed the machine; that is the bug this fixes.
        self.assertNotIn("no supported GPU", reason)
        self.assertNotIn("Install the film requirements", reason)

    def test_engine_that_cannot_import_torch_asks_for_a_rebuild(self) -> None:
        with TemporaryDirectory() as tmp:
            python = Path(tmp) / "python.exe"
            python.write_text("", "utf-8")
            with mock.patch.object(imaging.config, "IMAGE_PYTHON", python), mock.patch.object(
                imaging.FluxPythonBackend, "available", return_value=False
            ):
                reason = imaging.placeholder_reason()
        self.assertIn(str(python), reason)
        self.assertIn("torch", reason)
        self.assertIn("setup_image.ps1", reason)

    def test_a_mac_build_without_the_helper_says_so(self) -> None:
        with TemporaryDirectory() as tmp:
            python = Path(tmp) / "python"
            python.write_text("", "utf-8")
            with mock.patch.object(imaging.config, "IMAGE_PYTHON", python), mock.patch.object(
                imaging.FluxPythonBackend, "available", return_value=True
            ), mock.patch.object(imaging.sys, "platform", "darwin"), mock.patch.object(
                imaging.config, "FLUX_GENERATE", Path(tmp) / "mflux-generate-flux2"
            ):
                reason = imaging.placeholder_reason()
        self.assertIn("build_app.sh", reason)
        self.assertIn("Mac", reason)

    def test_the_fallback_does_not_blame_hardware(self) -> None:
        with TemporaryDirectory() as tmp:
            python = Path(tmp) / "python"
            python.write_text("", "utf-8")
            with mock.patch.object(imaging.config, "IMAGE_PYTHON", python), mock.patch.object(
                imaging.FluxPythonBackend, "available", return_value=True
            ), mock.patch.object(imaging.sys, "platform", "linux"):
                reason = imaging.placeholder_reason()
        self.assertIn("docs/STILLS.md", reason)

    def test_the_answer_is_computed_once(self) -> None:
        with mock.patch.object(
            imaging, "_compute_placeholder_reason", return_value="once"
        ) as compute:
            self.assertEqual("once", imaging.placeholder_reason())
            self.assertEqual("once", imaging.placeholder_reason())
        self.assertEqual(1, compute.call_count)


class PlaceholderDeliveryTests(unittest.TestCase):
    """The reason reaches the activity record, which is what the UI reads."""

    def test_a_placeholder_scene_reports_the_reason(self) -> None:
        from murmur import activity as activity_module

        with mock.patch.object(imaging, "_PLACEHOLDER_WHY", "no engine here"):
            activity_module.activity.finish("placeholder", imaging.placeholder_reason())
            state = activity_module.activity.snapshot()
        self.assertEqual("placeholder", state["phase"])
        self.assertEqual("no engine here", state["detail"])


if __name__ == "__main__":
    unittest.main()
