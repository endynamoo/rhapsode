"""Engine profiles: the recommendation, the override, and the hand-off to the painter."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import profile


class RecommendTests(unittest.TestCase):
    def test_no_card_is_cpu(self) -> None:
        self.assertEqual("cpu", profile.recommend(None))
        self.assertEqual("cpu", profile.recommend(0))

    def test_eight_gigabyte_card_is_lite(self) -> None:
        self.assertEqual("lite", profile.recommend(8.0))
        self.assertEqual("lite", profile.recommend(6.0))

    def test_sixteen_gigabyte_card_is_balanced(self) -> None:
        self.assertEqual("balanced", profile.recommend(15.9))
        self.assertEqual("balanced", profile.recommend(12.0))

    def test_big_card_is_max(self) -> None:
        self.assertEqual("max", profile.recommend(24.0))


class RoundTripTests(unittest.TestCase):
    def test_saving_and_loading_a_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            profile.save(home, "lite")
            loaded = profile.load(home)
            self.assertEqual("lite", loaded["profile"])
            self.assertEqual("q4", loaded["precision"])
            self.assertEqual("cpu", loaded["text_device"])
            self.assertFalse(loaded["polish"])

    def test_explicit_values_beat_the_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            profile.save(home, "balanced", reserve_mib=4000, polish=False)
            loaded = profile.load(home)
            self.assertEqual(4000, loaded["reserve_mib"])
            self.assertFalse(loaded["polish"])
            self.assertEqual("gpu", loaded["text_device"])

    def test_a_corrupt_file_falls_back_to_a_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            profile.path(home).write_text("{not json", "utf-8")
            loaded = profile.load(home)
            self.assertIn(loaded["profile"], profile.PROFILES)

    def test_describe_names_the_encoder_side(self) -> None:
        lite = dict(profile.PROFILES["lite"], profile="lite")
        text = profile.describe(lite)
        self.assertIn("text encoder on the CPU", text)
        self.assertIn("polish off", text)


class EnvHandoffTests(unittest.TestCase):
    def test_apply_env_sets_the_variables_the_painter_reads(self) -> None:
        preset = dict(profile.PROFILES["lite"], profile="lite")
        with mock.patch.dict(os.environ, {}, clear=True):
            profile.apply_env(preset)
            self.assertEqual("q4", os.environ["MURMUR_FLUX_PRECISION"])
            self.assertEqual("cpu", os.environ["MURMUR_FLUX_TEXT_DEVICE"])
            self.assertEqual("2000", os.environ["MURMUR_FLUX_RESERVE_MB"])

    def test_gpu_encoder_clears_the_cpu_override(self) -> None:
        preset = dict(profile.PROFILES["balanced"], profile="balanced")
        with mock.patch.dict(os.environ, {"MURMUR_FLUX_TEXT_DEVICE": "cpu"}, clear=True):
            profile.apply_env(preset)
            self.assertNotIn("MURMUR_FLUX_TEXT_DEVICE", os.environ)


class VramDetectionTests(unittest.TestCase):
    def test_linux_reads_the_card_instead_of_falling_back_to_cpu(self) -> None:
        # Only Windows has the display-adapter registry key; a Linux machine with a
        # CUDA/ROCm card used to look CPU-only, which put the painter on the `cpu`
        # profile it refuses to run.
        with mock.patch.object(profile.sys, "platform", "linux"), \
                mock.patch("murmur.backends.detect._linux_gpu",
                           return_value=("AMD Radeon", "amd", 15.9)):
            self.assertEqual(15.9, profile._detect_vram())
        self.assertEqual("balanced", profile.recommend(15.9))

    def test_a_linux_machine_with_no_card_stays_none(self) -> None:
        with mock.patch.object(profile.sys, "platform", "linux"), \
                mock.patch("murmur.backends.detect._linux_gpu",
                           return_value=("", "", None)):
            self.assertIsNone(profile._detect_vram())


if __name__ == "__main__":
    unittest.main()
