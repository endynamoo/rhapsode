"""The demo's story has to produce the scenes the demo shows.

`scripts/record_quick.py` paints every planned scene before it records, and the
manga clip exists to show "a few scenes": the review grid has to come up with
three painted tiles and no "Not painted" placeholder. That is a property of the
story's length - `film.plan_chapter` splits a chapter on estimated reading time,
75 seconds per scene - and the cheapest way to break the demo is to edit the
story and not notice it dropped to two scenes.

The painter is not needed for any of this: planning is pure arithmetic over the
unit texts.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from murmur import film  # noqa: E402
from murmur.text import split_units  # noqa: E402

SCRIPT = ROOT / "scripts" / "record_quick.py"


def load_script():
    spec = importlib.util.spec_from_file_location("record_quick", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class QuickDemoStoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = load_script()
        cls.units = split_units(cls.demo.STORY)
        cls.doc = {
            "id": "demo-story",
            "units": cls.units,
            "unitPages": [0] * len(cls.units),
            "pageCount": 1,
        }
        cls.scenes = film.plan_chapter(cls.doc, 0)

    def test_the_story_plans_three_scenes(self):
        self.assertEqual(
            len(self.scenes), 3,
            f"the story is {len(self.demo.STORY.split())} words and plans "
            f"{len(self.scenes)} scenes; the demo shows three",
        )

    def test_every_scene_has_a_slice_of_the_story(self):
        covered = [unit for scene in self.scenes for unit in scene.units]
        self.assertEqual(covered, list(range(len(self.units))))

    def test_the_scenes_are_numbered_for_the_grid(self):
        self.assertEqual([scene.index for scene in self.scenes], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
