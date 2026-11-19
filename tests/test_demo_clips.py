"""The README's clips are real files, and every published clip is used by the README.

A broken image in a front page is silent: GitHub renders the alt text and nothing tells
anyone. These are cheap checks that the clips stay linked, small enough to load, and
actually animated rather than a single frame saved with a .gif name.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "docs" / "demo"
README = ROOT / "README.md"

# A front page that takes ten seconds to paint is worse than one with no clips.
MAX_BYTES = 4_000_000


class DemoClipTests(unittest.TestCase):
    def setUp(self):
        self.readme = README.read_text(encoding="utf-8")
        self.index = (DEMO / "README.md").read_text(encoding="utf-8")
        # A clip may be shown on the front page or listed in the folder's own index; the
        # name carries an extension, which keeps prose like "in docs/demo/" out of the set.
        names = re.findall(r"([A-Za-z0-9._-]+\.(?:gif|mp4|png))", self.readme + self.index)
        self.linked = {name for name in names if (DEMO / name).exists()}

    def test_every_linked_clip_exists(self):
        self.assertTrue(self.linked, "the README links no demo clip")
        for name in sorted(self.linked):
            with self.subTest(clip=name):
                self.assertTrue((DEMO / name).is_file(), f"docs/demo/{name} is missing")

    def test_every_published_clip_is_linked(self):
        published = {path.name for path in DEMO.iterdir()
                     if path.suffix in (".gif", ".mp4", ".png")}
        self.assertEqual(published - self.linked, set(),
                         "a clip ships without being named on the front page or in the index")

    def test_clips_are_gifs_small_enough_and_animated(self):
        for path in sorted(DEMO.glob("*.gif")):
            with self.subTest(clip=path.name):
                data = path.read_bytes()
                self.assertEqual(data[:6], b"GIF89a", "not a GIF")
                self.assertLess(path.stat().st_size, MAX_BYTES, "too heavy for a README")
                # Several frames means the clip moves; one frame is a screenshot in disguise.
                self.assertGreater(data.count(b"\x21\xf9\x04"), 20)

    def test_the_corpus_the_clips_used_is_published(self):
        names = {path.suffix for path in (DEMO / "corpus").iterdir()}
        self.assertEqual(names, {".epub", ".pdf", ".txt"})


if __name__ == "__main__":
    unittest.main()
