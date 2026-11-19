"""The per-face repair pass: crops, caps, and the guards that undo a bad repair.

None of these need a GPU: `imaging.paint_job` is replaced by a function that
paints the tile however the test wants, and the detector is stubbed where the
verification path is what is under test. The real painter is exercised by hand
against a real still, which is the only way to judge whether a face looks right.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

from murmur import polish


def make_still(path: Path, size=(600, 400), faces=((120, 90, 80, 80),)) -> None:
    """A grey page with a dark blob per face, big enough to be a plausible still."""
    image = Image.new("RGB", size, (238, 238, 232))
    draw = ImageDraw.Draw(image)
    for x, y, w, h in faces:
        draw.ellipse([x, y, x + w, y + h], outline=(20, 20, 24), width=3)
        draw.ellipse([x + w * 0.25, y + h * 0.28, x + w * 0.38, y + h * 0.42], fill=(20, 20, 24))
        draw.ellipse([x + w * 0.62, y + h * 0.28, x + w * 0.75, y + h * 0.42], fill=(20, 20, 24))
        draw.line([x + w * 0.35, y + h * 0.7, x + w * 0.65, y + h * 0.7], fill=(20, 20, 24), width=3)
    image.save(path, format="PNG")


def region_pixels(path: Path, region) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L").crop(region))


class FaceBoxTest(unittest.TestCase):
    def test_a_box_is_grown_and_squared(self):
        boxes = polish._face_boxes([(200.0, 100.0, 290.0, 170.0)], (600, 400), 2)
        self.assertEqual(len(boxes), 1)
        x1, y1, x2, y2 = boxes[0]
        # Square: the longest side of the face defines the crop.
        self.assertEqual(x2 - x1, y2 - y1)
        # Grown on every side, not just the face box.
        self.assertGreater(x2 - x1, 90)
        self.assertLess(x1, 200)
        self.assertLess(y1, 100)

    def test_a_box_at_the_edge_is_clamped_to_the_page(self):
        # A face against the top-left corner: the crop must not run off the page.
        boxes = polish._face_boxes([(10.0, 4.0, 100.0, 74.0)], (500, 300), 2)
        self.assertEqual(len(boxes), 1)
        x1, y1, x2, y2 = boxes[0]
        self.assertGreaterEqual(x1, 0)
        self.assertGreaterEqual(y1, 0)
        self.assertLessEqual(x2, 500)
        self.assertLessEqual(y2, 300)
        self.assertGreater(x2 - x1, 0)

    def test_the_biggest_faces_are_picked_and_the_cap_holds(self):
        faces = [(40.0, 40.0, 100.0, 100.0), (200.0, 40.0, 320.0, 160.0),
                 (400.0, 40.0, 430.0, 70.0), (40.0, 200.0, 135.0, 295.0)]
        boxes = polish._face_boxes(faces, (600, 400), 2)
        self.assertEqual(len(boxes), 2)
        self.assertGreater(boxes[0][2] - boxes[0][0], boxes[1][2] - boxes[1][0])

    def test_two_boxes_on_one_face_are_collapsed(self):
        # YuNet sometimes reports the same face twice; painting it twice would
        # undo the first repair with the second.
        faces = [(100.0, 100.0, 180.0, 180.0), (108.0, 104.0, 186.0, 182.0)]
        self.assertEqual(len(polish._face_boxes(faces, (600, 400), 4)), 1)

    def test_tiny_boxes_are_ignored(self):
        self.assertEqual(polish._face_boxes([(10.0, 10.0, 18.0, 18.0)], (600, 400), 2), [])


class FacesEnabledTest(unittest.TestCase):
    def test_off_by_default(self):
        with mock.patch.object(polish.store, "get_settings", return_value={}):
            self.assertFalse(polish.faces_enabled())

    def test_the_setting_turns_it_on(self):
        with mock.patch.object(polish.store, "get_settings", return_value={"polishFaces": True}):
            self.assertTrue(polish.faces_enabled())

    def test_the_environment_overrides_the_setting_both_ways(self):
        with mock.patch.object(polish.store, "get_settings", return_value={"polishFaces": True}):
            with mock.patch.dict(os.environ, {"MURMUR_POLISH_FACES": "0"}):
                self.assertFalse(polish.faces_enabled())
        with mock.patch.object(polish.store, "get_settings", return_value={"polishFaces": False}):
            with mock.patch.dict(os.environ, {"MURMUR_POLISH_FACES": "1"}):
                self.assertTrue(polish.faces_enabled())


class FaceRepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.still = self.root / "scene.png"
        make_still(self.still)
        self.region = (60, 30, 220, 190)

    def tearDown(self):
        self.tmp.cleanup()

    def _info(self, faces):
        return {"faces": faces, "people": [], "hands": [], "flags": []}

    def _paint_with(self, painter):
        """Let the repair run with a stand-in painter and the face check stubbed."""
        still = self.still
        return mock.patch.multiple(
            polish, imaging=mock.Mock(paint_job=painter, MIN_STILL_BYTES=1),
        ), still

    def test_a_good_repaint_changes_the_face_and_only_the_face(self):
        outside = (400, 300, 560, 380)
        before_face = region_pixels(self.still, self.region)
        before_outside = region_pixels(self.still, outside)

        def painter(job, **_kwargs):
            # "Repaint" = invert the tile: unmistakable inside the mask.
            with Image.open(job["reference"]) as reference:
                tile = reference.convert("RGB")
                Image.eval(tile, lambda value: 255 - value).save(job["out"], format="PNG")

        still = self.still
        patches = mock.patch.multiple(
            polish, imaging=mock.Mock(paint_job=painter, MIN_STILL_BYTES=1))
        with patches, mock.patch.object(polish.detectors, "faces",
                                        return_value=[(10.0, 10.0, 50.0, 50.0)]), \
                mock.patch.dict(os.environ, {"MURMUR_POLISH_FACES": "1"}):
            result = polish._repair_faces(still, self._info([(120.0, 90.0, 200.0, 170.0)]),
                                         600, 400, 1234)

        self.assertEqual(result["action"], "repaired", result)
        self.assertEqual(result["repaired"], 1)
        self.assertEqual(result["per_face"][0]["action"], "repaired")
        # The face region moved...
        self.assertFalse(np.array_equal(before_face, region_pixels(still, self.region)))
        # ...and the far corner did not.
        self.assertTrue(np.array_equal(before_outside, region_pixels(still, outside)))

    def test_a_repaint_with_no_face_left_is_thrown_away(self):
        before = region_pixels(self.still, self.region)

        def painter(job, **_kwargs):
            Image.new("RGB", (job["width"], job["height"]), (240, 240, 236)).save(
                job["out"], format="PNG")

        patches = mock.patch.multiple(
            polish, imaging=mock.Mock(paint_job=painter, MIN_STILL_BYTES=1))
        with patches, mock.patch.object(polish.detectors, "faces", return_value=[]), \
                mock.patch.dict(os.environ, {"MURMUR_POLISH_FACES": "1"}):
            result = polish._repair_faces(self.still, self._info([(120.0, 90.0, 200.0, 170.0)]),
                                         600, 400, 7)

        self.assertEqual(result["action"], "reverted", result)
        self.assertEqual(result["repaired"], 0)
        self.assertIn("no face", result["per_face"][0]["reason"])
        self.assertTrue(np.array_equal(before, region_pixels(self.still, self.region)))

    def test_a_repaint_that_loses_the_ink_is_thrown_away(self):
        before = region_pixels(self.still, self.region)

        def painter(job, **_kwargs):
            # Near-white tile: a face detector might still fire on a stub, so the
            # ink gate is what has to catch this one.
            Image.new("RGB", (job["width"], job["height"]), (252, 252, 250)).save(
                job["out"], format="PNG")

        patches = mock.patch.multiple(
            polish, imaging=mock.Mock(paint_job=painter, MIN_STILL_BYTES=1))
        with patches, mock.patch.object(polish.detectors, "faces",
                                        return_value=[(10.0, 10.0, 50.0, 50.0)]), \
                mock.patch.dict(os.environ, {"MURMUR_POLISH_FACES": "1"}):
            result = polish._repair_faces(self.still, self._info([(120.0, 90.0, 200.0, 170.0)]),
                                         600, 400, 11)

        self.assertEqual(result["action"], "reverted", result)
        self.assertIn("ink", result["per_face"][0]["reason"])
        self.assertTrue(np.array_equal(before, region_pixels(self.still, self.region)))

    def test_only_the_cap_is_painted(self):
        faces = [(60.0, 40.0, 130.0, 110.0), (200.0, 40.0, 270.0, 110.0),
                 (340.0, 40.0, 410.0, 110.0), (460.0, 40.0, 530.0, 110.0)]
        calls = []

        def painter(job, **_kwargs):
            calls.append(job)
            with Image.open(job["reference"]) as reference:
                reference.convert("RGB").save(job["out"], format="PNG")

        patches = mock.patch.multiple(
            polish, imaging=mock.Mock(paint_job=painter, MIN_STILL_BYTES=1))
        with patches, mock.patch.object(polish.detectors, "faces",
                                        return_value=[(10.0, 10.0, 50.0, 50.0)]), \
                mock.patch.dict(os.environ, {"MURMUR_POLISH_FACES": "1"}):
            result = polish._repair_faces(self.still, self._info(faces), 600, 400, 3)

        self.assertEqual(len(calls), polish.FACE_MAX_DEFAULT)
        self.assertEqual(result["faces"], polish.FACE_MAX_DEFAULT)
        # Every job asks for the enlarged tile, at the strength a repair needs.
        for job in calls:
            self.assertEqual((job["width"], job["height"]),
                             (polish.FACE_TILE, polish.FACE_TILE))
            self.assertEqual(job["strength"], polish.FACE_STRENGTH)
            self.assertIn("manga face", job["prompt"])

    def test_nothing_happens_when_the_pass_is_off(self):
        with mock.patch.dict(os.environ, {"MURMUR_POLISH_FACES": "0"}):
            result = polish._repair_faces(self.still, self._info([(120.0, 90.0, 200.0, 170.0)]),
                                          600, 400, 5)
        self.assertEqual(result["action"], "disabled")

    def test_temporary_tiles_are_cleaned_up(self):
        def painter(job, **_kwargs):
            with Image.open(job["reference"]) as reference:
                reference.convert("RGB").save(job["out"], format="PNG")

        patches = mock.patch.multiple(
            polish, imaging=mock.Mock(paint_job=painter, MIN_STILL_BYTES=1))
        with patches, mock.patch.object(polish.detectors, "faces",
                                        return_value=[(10.0, 10.0, 50.0, 50.0)]), \
                mock.patch.dict(os.environ, {"MURMUR_POLISH_FACES": "1"}):
            polish._repair_faces(self.still, self._info([(120.0, 90.0, 200.0, 170.0)]),
                                 600, 400, 9)
        leftovers = sorted(p.name for p in self.root.iterdir() if p.name != self.still.name)
        self.assertEqual(leftovers, [], leftovers)


class RecordTest(unittest.TestCase):
    def test_switching_the_pass_on_is_not_read_as_cached(self):
        # A still polished before the pass existed must be polished again once the
        # switch is on, or turning it on would do nothing until the next repaint.
        with tempfile.TemporaryDirectory() as tmp:
            still = Path(tmp) / "scene.png"
            make_still(still)
            digest = polish._digest(still)
            (Path(tmp) / "scene.png.polish.json").write_text(json.dumps({
                "version": polish.POLISH_VERSION, "action": "refined",
                "output_hash": digest,
            }), "utf-8")

            with mock.patch.object(polish.store, "get_settings", return_value={"polishStills": True}), \
                    mock.patch.object(polish.detectors, "available", return_value=True):
                base = polish.apply(still)
                self.assertEqual(base["action"], "cached")

                with mock.patch.object(polish, "faces_enabled", return_value=True):
                    # Not cached: the record predates the face variant, so it runs
                    # the rest of the pass -- which stops at "no image engine"
                    # here because the test has no stills interpreter.
                    result = polish.apply(still)
            self.assertNotEqual(result["action"], "cached")
