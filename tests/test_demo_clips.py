"""The demo folder is honest: every published file is named, and every named file exists.

A broken image or a dead audio link in a front page is silent - GitHub renders alt text and
nothing fails. These checks are cheap and they run in the normal suite.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "docs" / "demo"
README = ROOT / "README.md"
INDEX = DEMO / "README.md"

MEDIA = (".gif", ".mp4", ".png", ".mp3")

# A front page that takes ten seconds to paint is worse than one with no clips.
MAX_GIF_BYTES = 4_000_000
MAX_AUDIO_BYTES = 2_000_000


class DemoClipTests(unittest.TestCase):
    def setUp(self):
        self.readme = README.read_text(encoding="utf-8")
        self.index = INDEX.read_text(encoding="utf-8")
        self.prose = self.readme + self.index
        self.media = sorted(
            path for path in DEMO.rglob("*") if path.suffix in MEDIA and path.is_file()
        )

    def test_the_folder_publishes_something(self):
        self.assertTrue(self.media, "docs/demo holds no media")

    def test_every_published_file_is_named_somewhere(self):
        for path in self.media:
            with self.subTest(file=str(path.relative_to(DEMO))):
                if path.name not in self.prose:
                    self.fail(f"{path.relative_to(DEMO)} is published but never named, "
                              "so nothing links it")

    def test_every_link_into_the_folder_resolves(self):
        # Front page: absolute paths like docs/demo/reading.gif, in <img src> and links.
        for target in set(re.findall(r"docs/demo/([A-Za-z0-9._/-]+\.(?:gif|mp4|png|mp3))",
                                     self.readme)):
            with self.subTest(target=target):
                self.assertTrue((DEMO / target).is_file(), f"docs/demo/{target} is missing")

        # The folder index links relatively: audio/x.mp3, reading.gif, corpus/y.epub.
        for target in set(re.findall(r"\]\(([A-Za-z0-9._/-]+\.(?:gif|mp4|png|mp3|epub|pdf|txt))\)",
                                     self.index)):
            if target.startswith("http"):
                continue
            with self.subTest(target=target):
                self.assertTrue((DEMO / target).is_file(), f"docs/demo/{target} is missing")

    def test_gifs_are_animations_and_fit_a_readme(self):
        for path in self.media:
            if path.suffix != ".gif":
                continue
            with self.subTest(clip=path.name):
                data = path.read_bytes()
                self.assertEqual(data[:6], b"GIF89a", "not a GIF")
                self.assertLess(path.stat().st_size, MAX_GIF_BYTES, "too heavy for a README")
                # Many frames means the clip moves; a single frame hides as "clip.gif".
                if path.name != "three-languages.gif":
                    self.assertGreater(data.count(b"\x21\xf9\x04"), 20)

    def test_audio_is_real_mpeg_and_modest(self):
        audio = [path for path in self.media if path.suffix == ".mp3"]
        self.assertTrue(audio, "no audio is published")
        for path in audio:
            with self.subTest(file=path.name):
                data = path.read_bytes()[:4]
                starts_with_id3 = data[:3] == b"ID3"
                frame_sync = data[0] == 0xFF and (data[1] & 0xE0) == 0xE0
                self.assertTrue(starts_with_id3 or frame_sync, "not an MPEG audio file")
                self.assertLess(path.stat().st_size, MAX_AUDIO_BYTES, "too heavy to download")

    def test_the_corpus_the_clips_used_is_published(self):
        names = {path.suffix for path in (DEMO / "corpus").iterdir()}
        self.assertEqual(names, {".epub", ".pdf", ".txt"})


def demo_is_absent(name: str) -> bool:
    """True when a mention is a filename the app would produce, not a demo asset."""
    allowed = {"NOTICE.txt", "SOURCES.md", "GPL-3.0.txt", "LICENSE"}
    return name in allowed or name.startswith(("Rhapsode-", "kokoro-", "voices-"))


if __name__ == "__main__":
    unittest.main()
