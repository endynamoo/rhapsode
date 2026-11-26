"""The ffmpeg the app installs for itself: the pin, the unpack, and where it is found.

No download here. The point of these tests is that a reader's machine ends up with a
binary the app can find, and that the archive one bad day cannot turn into a 100 MB pile of
documentation or a missing licence.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from murmur import config, ffmpeg


class PinTests(unittest.TestCase):
    def test_the_build_is_pinned_and_permissibly_described(self) -> None:
        # A versioned artifact, not `/ffmpeg-release-essentials.zip`, which moves with each
        # upstream release and would fail the pinned checksum on the next one.
        self.assertTrue(ffmpeg.URL.startswith("https://www.gyan.dev/ffmpeg/builds/"))
        self.assertIn("9.1", ffmpeg.URL)
        self.assertTrue(ffmpeg.URL.endswith(".zip"), "a zip, so no 7-Zip is needed")
        self.assertEqual(64, len(ffmpeg.SHA256))
        self.assertGreaterEqual(ffmpeg.MIN_BYTES, 100 * 1024 * 1024)
        self.assertEqual("9.1", ffmpeg.VERSION)

    def test_the_licence_is_the_one_the_notice_names(self) -> None:
        # gyan.dev's builds are GPL-3.0; the notice and SOURCES.md say so.
        self.assertEqual("GPL-3.0", ffmpeg.state()["licence"])


class UnpackTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        patcher = mock.patch.object(config, "HOME", self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _archive(self, root: Path) -> Path:
        archive = root / "ffmpeg.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("ffmpeg-9.1-essentials_build/bin/ffmpeg.exe", b"MZ" + b"x" * 64)
            bundle.writestr("ffmpeg-9.1-essentials_build/bin/ffprobe.exe", b"MZ" + b"y" * 64)
            bundle.writestr("ffmpeg-9.1-essentials_build/bin/ffplay.exe", b"MZ" + b"z" * 64)
            bundle.writestr("ffmpeg-9.1-essentials_build/LICENSE", "GNU GENERAL PUBLIC LICENSE\n")
            bundle.writestr("ffmpeg-9.1-essentials_build/README.txt", "docs")
            bundle.writestr("ffmpeg-9.1-essentials_build/doc/long.txt", "not needed")
        return archive

    def test_only_the_binaries_and_the_licence_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(Path(tmp))
            ffmpeg._unpack(archive)
        root = ffmpeg.install_root()
        self.assertTrue((root / "bin" / "ffmpeg.exe").is_file())
        self.assertTrue((root / "bin" / "ffprobe.exe").is_file())
        self.assertFalse((root / "bin" / "ffplay.exe").exists(), "playback is not used")
        self.assertTrue((root / "license").is_file())
        self.assertIn("GENERAL PUBLIC LICENSE", (root / "license").read_text(encoding="utf-8"))
        self.assertFalse((root / "doc").exists())

    def test_a_marker_plus_the_binary_count_as_installed(self) -> None:
        (ffmpeg.install_root() / "bin").mkdir(parents=True)
        (ffmpeg.install_root() / "bin" / "ffmpeg.exe").write_bytes(b"MZ")
        self.assertFalse(ffmpeg.installed(), "no marker, no claim")
        ffmpeg.marker_path().write_text(json.dumps({"version": "9.1"}), encoding="utf-8")
        self.assertTrue(ffmpeg.installed())
        self.assertIsNotNone(ffmpeg.managed_path())

    def test_find_tool_prefers_the_copy_the_app_installed(self) -> None:
        (ffmpeg.install_root() / "bin").mkdir(parents=True)
        managed = ffmpeg.install_root() / "bin" / "ffmpeg.exe"
        managed.write_bytes(b"MZ")
        self.assertEqual(str(managed), config.find_tool("ffmpeg"))


class StartTests(unittest.TestCase):
    def setUp(self) -> None:
        ffmpeg._STATE.update(running=False, done=False, error=None, progress=None)

    def test_starting_when_installed_is_a_no_op(self) -> None:
        with mock.patch.object(ffmpeg, "installed", return_value=True):
            state = ffmpeg.start_download()
        self.assertFalse(state["running"])
        self.assertIsNone(state["error"])

    def test_a_failed_fetch_is_reported_not_raised(self) -> None:
        with mock.patch.object(ffmpeg, "installed", return_value=False), \
                mock.patch.object(ffmpeg, "_fetch", side_effect=RuntimeError("no network")):
            ffmpeg.start_download()
            for _ in range(100):
                if not ffmpeg._STATE["running"]:
                    break
                import time

                time.sleep(0.02)
        self.assertFalse(ffmpeg._STATE["done"])
        self.assertEqual("no network", ffmpeg._STATE["error"])


if __name__ == "__main__":
    unittest.main()
