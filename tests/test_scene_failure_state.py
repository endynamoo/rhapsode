"""A failure must stop being reported once the scene has a still.

Cinema's Retry repaints a scene in the request thread, not through the queue
worker, so the worker's record of the earlier failure was never cleared: a scene
that had just painted came back as `phase: "ready"` *and* the old
"refusing to paint" error, which the panel below the still renders as a failure.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from murmur import film


class StaleFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.painter = film.ScenePainter()
        self.doc = {"id": "doc1"}
        self.scenes = [SimpleNamespace(index=0)]
        self.key = ("doc1", 0, 0)

    def _status(self, *, delivered: bool) -> dict:
        with mock.patch.object(film, "film_model", return_value=("m", None, None)), \
             mock.patch.object(film, "scene_image_path", return_value=Path("still.png")), \
             mock.patch.object(film, "still_delivered", return_value=delivered):
            return self.painter.status(self.doc, 0, self.scenes)

    def _fail_once(self) -> None:
        with self.painter._lock:
            self.painter._failed.add(self.key)
            self.painter._errors[self.key] = "refusing to paint: no memory"

    def test_a_failure_is_reported_while_the_scene_has_no_still(self) -> None:
        self._fail_once()
        status = self._status(delivered=False)
        self.assertEqual(1, status["failed"])
        self.assertEqual("refusing to paint: no memory", status["error"])

    def test_a_failure_is_dropped_once_the_scene_has_a_still(self) -> None:
        self._fail_once()
        status = self._status(delivered=True)
        self.assertEqual(0, status["failed"])
        self.assertIsNone(status["error"])
        self.assertEqual(1, status["done"])

    def test_the_dropped_failure_does_not_come_back(self) -> None:
        self._fail_once()
        self._status(delivered=True)
        status = self._status(delivered=False)
        self.assertEqual(0, status["failed"])
        self.assertIsNone(status["error"])

    def test_a_failure_in_another_chapter_is_left_alone(self) -> None:
        other = ("doc1", 3, 7)
        with self.painter._lock:
            self.painter._failed.add(other)
            self.painter._errors[other] = "other chapter"
        self._fail_once()
        self._status(delivered=True)
        with self.painter._lock:
            self.assertIn(other, self.painter._failed)
            self.assertIn(other, self.painter._errors)


@unittest.skipUnless(hasattr(film, "scene_activity"), "needs the activity merge")
class MergedActivityTests(unittest.TestCase):
    """The bug read through the same call the API makes."""

    def test_a_ready_scene_carries_no_error(self) -> None:
        painter = film.ScenePainter()
        doc = {"id": "doc2"}
        scenes = [SimpleNamespace(index=0, units=[0], text="x", seed=1)]
        with painter._lock:
            painter._failed.add(("doc2", 0, 0))
            painter._errors[("doc2", 0, 0)] = "refusing to paint: no memory"
        with mock.patch.object(film, "film_model", return_value=("m", None, None)), \
             mock.patch.object(film, "scene_image_path", return_value=Path("still.png")), \
             mock.patch.object(film, "still_delivered", return_value=True), \
             mock.patch.object(film, "painter", painter), \
             mock.patch.object(film.activity, "snapshot",
                               return_value={"phase": "ready", "detail": "Scene 1 ready",
                                             "doc": "doc2", "chapter": 0, "scene": 0,
                                             "error": None}):
            state = film.scene_activity(doc, 0, scenes)
        self.assertEqual("ready", state["phase"])
        self.assertIsNone(state["error"])


if __name__ == "__main__":
    unittest.main()
