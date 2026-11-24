"""The on-device translator: what it fetches, where it puts it, and when it refuses.

No network and no weights here: the download paths are mocked, and the point of these
tests is the parts that decide *whether* the app thinks translation is ready - the size
and checksum pins, the off switch, and the shape the UI reads.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import config, localmt, store


class PlatformTests(unittest.TestCase):
    def test_every_supported_platform_has_a_runtime_asset(self) -> None:
        for key in ("win32-x86_64", "darwin-arm64", "darwin-x86_64",
                    "linux-x86_64", "linux-aarch64"):
            with self.subTest(key=key):
                url, floor, sha = localmt.ASSETS[key]
                self.assertTrue(url.startswith("https://github.com/ggml-org/llama.cpp/"))
                self.assertIn(localmt.TAG, url)
                self.assertGreater(floor, 1_000_000)
                if key == "win32-x86_64":
                    self.assertEqual(64, len(sha), "the archive this box fetched is pinned")
                else:
                    self.assertEqual("", sha, "a checksum we could not compute is not invented")

    def test_this_machine_maps_to_a_known_key(self) -> None:
        self.assertIn(localmt.platform_key(), localmt.ASSETS)


class VariantTests(unittest.TestCase):
    def test_every_variant_pins_a_size_and_a_checksum(self) -> None:
        for name, spec in localmt.VARIANTS.items():
            with self.subTest(variant=name):
                self.assertTrue(spec["label"])
                self.assertGreater(len(spec["files"]), 0)
                for file_name, size, sha in spec["files"]:
                    self.assertTrue(file_name.endswith(".gguf"))
                    self.assertGreater(size, 100_000_000)
                    self.assertEqual(64, len(sha), "weights are pinned by sha256")
                self.assertEqual(sum(s for _n, s, _h in spec["files"]), localmt.size_of(name))

    def test_the_default_is_the_measured_one(self) -> None:
        # 7B is the smallest Apache-2.0 model that translated French and Japanese
        # correctly when this was measured on 2026-11-24. 3B is better but under the
        # Qwen Research Licence, which this project cannot ship.
        self.assertEqual("7b", localmt.DEFAULT_VARIANT)
        self.assertIn("1.5b", localmt.VARIANTS)


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patcher = mock.patch.object(config, "MODELS_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        localmt.stop()

    def _binary(self) -> Path:
        binary = self.root / "localmt" / "llama-b12676" / "llama-server"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"binary")
        return binary

    def test_nothing_is_installed_in_an_empty_data_directory(self) -> None:
        self.assertFalse(localmt.installed("7b"))
        status = localmt.status()
        self.assertFalse(status["installed"])
        self.assertFalse(status["runtime"])
        self.assertEqual("7b", status["variant"])
        for key in ("download", "server", "variants", "enabled", "reason"):
            self.assertIn(key, status)

    def test_a_model_file_of_the_wrong_size_does_not_count(self) -> None:
        binary = self._binary()
        path = localmt.model_paths("1.5b")[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1024)
        with mock.patch.object(localmt, "binary_path", return_value=binary):
            self.assertFalse(localmt.installed("1.5b"))

    def test_a_full_set_at_the_pinned_sizes_counts_as_installed(self) -> None:
        binary = self._binary()
        spec = {"label": "T", "repo": "x/y", "note": "",
                "files": [("tiny.gguf", 4, "0" * 64)]}
        path = self.root / "localmt" / "models" / "tiny" / "tiny.gguf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"abcd")
        with mock.patch.dict(localmt.VARIANTS, {"tiny": spec}), \
                mock.patch.object(localmt, "binary_path", return_value=binary):
            self.assertTrue(localmt.installed("tiny"))
            path.write_bytes(b"abc")
            self.assertFalse(localmt.installed("tiny"))

    def test_the_off_switch_disables_it_and_says_why(self) -> None:
        with mock.patch.dict("os.environ", {"MURMUR_LOCAL_MT": "off"}):
            self.assertFalse(localmt.enabled())
            status = localmt.status()
        self.assertFalse(status["enabled"])
        self.assertIn("switched off", status["reason"])

    def test_starting_without_the_files_refuses_with_a_sentence(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            localmt.ensure_server(timeout=0.1)
        self.assertIn("not installed", str(caught.exception))

    def test_an_unknown_variant_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            localmt.start_download("99b")

    def test_the_chosen_variant_comes_from_settings(self) -> None:
        with mock.patch.object(store, "get_settings", return_value={"translateVariant": "1.5b"}):
            self.assertEqual("1.5b", localmt.variant())
        with mock.patch.object(store, "get_settings", return_value={"translateVariant": "bogus"}):
            self.assertEqual("7b", localmt.variant())

    def test_a_partial_download_is_a_part_file_not_a_model(self) -> None:
        path = localmt.model_paths("7b")[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".gguf.part").write_bytes(b"half")
        self.assertFalse(localmt.installed("7b"))


if __name__ == "__main__":
    unittest.main()
