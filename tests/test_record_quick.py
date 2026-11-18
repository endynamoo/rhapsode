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


class QuickDemoLanguageTest(unittest.TestCase):
    """The reading clip shows one sentence in three languages.

    The demo synthesises that sentence through `/api/tts` and splices the three
    takes together, so two things have to hold or the clip is wrong on screen:
    the sentence has to be the reader's *own* first speech unit (otherwise the
    highlighted text and the voice disagree), and the languages have to be three
    distinct voices (otherwise "translation" is one accent three times).
    """

    @classmethod
    def setUpClass(cls):
        cls.demo = load_script()

    def test_the_sentence_is_the_first_speech_unit(self):
        sentence = self.demo.first_sentence(self.demo.STORY)
        units = split_units(self.demo.STORY)
        self.assertEqual(
            units[0].strip(), sentence.strip(),
            "the demo speaks first_sentence(); the reader's first unit is "
            f"{units[0][:60]!r}, so the audio and the highlight would differ",
        )

    def test_three_languages_with_three_distinct_voices(self):
        voices = [voice or "af_heart" for _, voice in self.demo.LANGUAGES]
        self.assertEqual(len(self.demo.LANGUAGES), 3)
        self.assertEqual(len(set(voices)), 3, f"repeated voice in {voices}")

    def test_every_language_is_labelled(self):
        for label, _ in self.demo.LANGUAGES:
            self.assertTrue(label.strip())
            self.assertLessEqual(len(label), 60, "a label has to fit the sidebar")


if __name__ == "__main__":
    unittest.main()
