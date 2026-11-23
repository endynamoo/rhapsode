"""A saved profile this build cannot honour must not survive (#100).

The 0.3.0 CPU button wrote {"profile": "cpu"} to engine.json; the button is gone, and the
file kept every scene a placeholder while the Settings copy blamed the hardware.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import profile


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        profile.RECONCILED.clear()

    def write(self, data: dict) -> None:
        (self.home / profile.PROFILE_FILE).write_text(json.dumps(data), "utf-8")

    def saved(self) -> dict:
        return json.loads((self.home / profile.PROFILE_FILE).read_text("utf-8"))

    def test_a_saved_cpu_profile_on_a_gpu_machine_becomes_the_recommendation(self):
        self.write({"profile": "cpu", "precision": "cpu", "text_device": "cpu",
                    "reserve_mib": 0, "quality": "fast", "polish": False})
        with mock.patch.object(profile, "_detect_vram", return_value=16.3):
            loaded = profile.load(self.home)
        self.assertEqual(loaded["profile"], "balanced")
        self.assertNotEqual(loaded["precision"], "cpu")
        # The file is rewritten, so the next start does not have to reconcile again.
        self.assertEqual(self.saved()["profile"], "balanced")
        self.assertEqual(profile.RECONCILED, {"was": "cpu", "now": "balanced"})

    def test_cpu_is_kept_when_this_machine_really_has_no_gpu(self):
        self.write({"profile": "cpu"})
        with mock.patch.object(profile, "_detect_vram", return_value=None):
            loaded = profile.load(self.home)
        self.assertEqual(loaded["profile"], "cpu")
        self.assertEqual(self.saved()["profile"], "cpu", "a correct saved profile was rewritten")
        self.assertEqual(profile.RECONCILED, {})

    def test_a_valid_saved_profile_is_left_alone(self):
        self.write({"profile": "max", "quality": "high"})
        before = (self.home / profile.PROFILE_FILE).read_bytes()
        with mock.patch.object(profile, "_detect_vram", return_value=16.3):
            loaded = profile.load(self.home)
        self.assertEqual(loaded["profile"], "max")
        self.assertEqual((self.home / profile.PROFILE_FILE).read_bytes(), before)
        self.assertEqual(profile.RECONCILED, {})

    def test_no_file_means_the_recommendation(self):
        with mock.patch.object(profile, "_detect_vram", return_value=16.3):
            self.assertEqual(profile.load(self.home)["profile"], "balanced")
        self.assertEqual(profile.RECONCILED, {})

    def test_the_reconciliation_is_reported_once(self):
        self.write({"profile": "cpu"})
        with mock.patch.object(profile, "_detect_vram", return_value=16.3):
            profile.load(self.home)
            self.assertTrue(profile.RECONCILED, "the first load did not report it")
            profile.load(self.home)
            self.assertEqual(profile.RECONCILED, {}, "it reported a fix that already happened")


if __name__ == "__main__":
    unittest.main()
