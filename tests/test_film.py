"""Stills/film tests. These do not require FLUX or a network connection."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

from murmur import film


def setUpModule() -> None:
    # Tests bypass the server startup that normally initializes the store;
    # create the schema here so a fresh profile behaves like a running app.
    from murmur import store
    store.init()


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        film._PLANS.clear()

    def test_sentences_group_into_few_key_images(self) -> None:
        units = [
            f"Sentence {i} carries the scene forward with enough descriptive words to "
            "fill part of a minute of narration."
            for i in range(63)
        ]
        doc = {
            "id": "plan", "title": "Plan",
            "units": units,
            "unitPages": [0] * len(units), "pageCount": 1,
        }
        scenes = film.plan_chapter(doc, 0)
        self.assertGreaterEqual(len(scenes), 4)
        self.assertLessEqual(len(scenes), 8)
        seen = [u for s in scenes for u in s.units]
        self.assertEqual(seen, list(range(len(units))))
        for scene in scenes:
            self.assertTrue(scene.text.strip())
            self.assertLessEqual(len(scene.units), film.SCENE_MAX_UNITS)

    def test_shot_rotation_is_varied(self) -> None:
        scenes = [
            film.Scene(index=i, units=[i], text=f"Sentence {i}.", prompt="manga", seed=i)
            for i in range(10)
        ]
        shots = {film.shot_for_scene(scene) for scene in scenes}
        self.assertGreaterEqual(len(shots), 4)

    def test_high_quality_is_flux(self) -> None:
        # store defaults to high in a fresh profile; this only checks the mapping.
        from murmur import store
        old = store.get_settings().get("filmQuality")
        store.set_settings({"filmQuality": "high"})
        try:
            self.assertEqual(film.film_model()[0], "flux2-klein-4b")
        finally:
            store.set_settings({"filmQuality": old or "high"})


class CacheTests(unittest.TestCase):
    def _doc(self) -> dict:
        return {
            "id": "cache", "title": "Cache",
            "units": ["A single sentence for the cache test."],
            "unitPages": [0], "pageCount": 1,
        }

    def _scene(self) -> film.Scene:
        return film.Scene(
            index=0, units=[0], text="A single sentence for the cache test.",
            prompt="manga", seed=1,
        )

    def test_existing_scene_image_is_not_regenerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(film.config, "FILM_DIR", Path(tmp)):
                doc = self._doc()
                path = film.scene_image_path(doc["id"], 0, 0)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * 2048)
                with mock.patch.object(film.imaging.image_gen, "generate") as generate:
                    generate.side_effect = AssertionError("should not regenerate")
                    result = film.ensure_scene_image(doc, 0, self._scene())
                self.assertEqual(result, path)

    def test_render_key_tracks_images_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(film.config, "FILM_DIR", root):
                doc = self._doc()
                scenes = [self._scene()]
                path = film.scene_image_path(doc["id"], 0, 0)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * 2048)

                first = film._render_key(doc, 0, scenes, "af_heart", False)
                manifest = film._manifest_path(doc["id"], 0)
                film._write_manifest(manifest, first)
                self.assertEqual(film._read_manifest(manifest), first)

                os.utime(path, (1, 1))
                second = film._render_key(doc, 0, scenes, "af_heart", False)
                self.assertNotEqual(first["images"], second["images"])
                self.assertNotEqual(film._read_manifest(manifest), second)


class CastTests(unittest.TestCase):
    def test_cast_lines_are_parsed_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(film.config, "FILM_DIR", Path(tmp)):
                doc = {
                    "id": "cast", "title": "Cast", "text": "A narrator and Amelia.",
                    "units": ["A narrator and Amelia."], "unitPages": [0], "pageCount": 1,
                }
                raw = (
                    "Narrator | observer | lean man, torn coat\n"
                    "Amelia Lopez | ally | dark hair, heavy coat"
                )
                with mock.patch.object(film.prompts.writer, "cast", return_value=raw):
                    cast = film.doc_cast(doc, 0)
                self.assertEqual([m["name"] for m in cast], ["Narrator", "Amelia Lopez"])
                self.assertEqual(cast[0]["role"], "protagonist")
                self.assertTrue(film.cast_path(doc["id"]).exists())
                with mock.patch.object(film.prompts.writer, "cast") as writer:
                    again = film.doc_cast(doc, 0)
                writer.assert_not_called()
                self.assertEqual(again, cast)


class GutterTests(unittest.TestCase):
    def test_split_spread_is_detected_and_cropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spread.png"
            rng = np.random.default_rng(7)
            data = np.full((700, 1200), 255, dtype=np.uint8)
            data[20:680, 20:580] = rng.integers(20, 220, (660, 560), dtype=np.uint8)
            data[20:680, 620:1180] = rng.integers(20, 220, (660, 560), dtype=np.uint8)
            Image.fromarray(data).save(path)

            self.assertIsNotNone(film._find_gutter(np.asarray(Image.open(path).convert("L"))))
            film._crop_gutter(path)
            result = Image.open(path)
            self.assertEqual(result.mode, "L")
            self.assertGreater(result.width, 1700)
            self.assertGreater(result.height, 1000)
            self.assertIsNone(film._find_gutter(np.asarray(result)))


class ClipTests(unittest.TestCase):
    def test_scene_clip_is_steady_1080p(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "still.png"
            out = root / "clip.mp4"
            Image.new("RGB", (1920, 1080), (30, 30, 30)).save(image)
            film._scene_clip(image, 1.0, out, 0)
            self.assertTrue(out.exists())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=width,height,r_frame_rate", "-of", "json", str(out)],
                capture_output=True, text=True,
            )
            data = json.loads(probe.stdout)
            stream = data["streams"][0]
            self.assertEqual((stream["width"], stream["height"]), (1920, 1080))
            self.assertEqual(stream["r_frame_rate"], "30/1")

    def test_finish_still_is_monochrome_and_trims_border(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "still.png"
            image = Image.new("RGB", (400, 300), (255, 255, 255))
            for x in range(30, 370):
                for y in range(30, 270):
                    image.putpixel((x, y), (180, 140, 90))
            image.save(path)
            film._finish_still(path)
            with Image.open(path) as result:
                mode, width, height = result.mode, result.width, result.height
            self.assertEqual(mode, "L")
            self.assertLess(width, 400)
            self.assertLess(height, 300)


if __name__ == "__main__":
    unittest.main()
