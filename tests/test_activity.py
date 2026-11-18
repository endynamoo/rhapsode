"""The scene activity contract: one vocabulary for Cinema and Settings."""
from __future__ import annotations

import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from murmur import activity as activity_state
from murmur import config, film, imaging, server, store


@contextmanager
def temp_home():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        with mock.patch.multiple(
            config,
            HOME=base,
            MODELS_DIR=base / "models",
            CACHE_DIR=base / "cache",
            PAGES_DIR=base / "cache" / "pages",
            FILES_DIR=base / "files",
            EXPORT_DIR=base / "exports",
            FILM_DIR=base / "film",
            DB_PATH=base / "murmur.db",
        ):
            config.ensure_dirs()
            store.close()
            store.init()
            try:
                yield base
            finally:
                store.close()


def fresh() -> None:
    """The record is process-wide; every test starts from a clean one."""
    activity_state.activity.__init__()


def wait_until(check, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


class SceneRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        fresh()

    def test_a_still_starts_by_planning_not_by_painting(self) -> None:
        record = activity_state.activity
        record.begin("doc", 0, 3, model="flux2-klein-4b", backend="flux2-klein-diffusers")
        state = record.snapshot()
        self.assertEqual("planning", state["phase"])
        self.assertTrue(state["busy"])
        self.assertEqual(3, state["scene"])
        self.assertFalse(state["coldStart"])
        self.assertFalse(state["determinate"])

    def test_cold_start_is_a_flag_the_ui_can_explain(self) -> None:
        record = activity_state.activity
        record.begin("doc", 0, 0)
        record.phase("loading", "Loading the stills engine", coldStart=True)
        self.assertTrue(record.snapshot()["coldStart"])
        record.finish("ready", "Scene 1 ready")
        state = record.snapshot()
        self.assertEqual("ready", state["phase"])
        self.assertFalse(state["busy"])
        self.assertFalse(state["coldStart"])

    def test_a_failure_keeps_its_reason_and_drops_the_cold_start_flag(self) -> None:
        record = activity_state.activity
        record.begin("doc", 0, 1)
        record.phase("painting", "Painting scene 2", coldStart=True)
        record.finish("failed", "Scene 2 could not be painted", error="no GPU")
        state = record.snapshot()
        self.assertEqual("failed", state["phase"])
        self.assertEqual("no GPU", state["error"])
        self.assertFalse(state["coldStart"])

    def test_an_unknown_phase_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            activity_state.activity.phase("rendering")
        with self.assertRaises(ValueError):
            activity_state.activity.finish("painted")


class PaintReportingTests(unittest.TestCase):
    """What `imaging` reports while the backend is actually busy."""

    def setUp(self) -> None:
        fresh()
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "still.png"

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _backend(self, name: str):
        backend = mock.Mock()
        backend.name = name
        backend.generate.side_effect = lambda *a, **k: self.path.write_bytes(b"x" * 32)
        return backend

    def test_the_first_still_of_a_session_is_a_model_load(self) -> None:
        images = imaging.FilmImages()
        with mock.patch.object(images, "backend", return_value=self._backend("flux2-klein")):
            self.assertEqual("flux2-klein", images.generate("p", self.path, 1, model="flux2-klein"))
        state = activity_state.activity.snapshot()
        self.assertEqual("loading", state["phase"])
        self.assertTrue(state["coldStart"])

    def test_a_warm_painter_reports_painting(self) -> None:
        images = imaging.FilmImages()
        with mock.patch.object(images, "backend", return_value=self._backend("flux2-klein")):
            images.generate("p", self.path, 1, model="flux2-klein")
            self.path.unlink()
            images.generate("p", self.path, 2, model="flux2-klein", label="Painting scene 4")
        state = activity_state.activity.snapshot()
        self.assertEqual("painting", state["phase"])
        self.assertEqual("Painting scene 4", state["detail"])
        self.assertFalse(state["coldStart"])

    def test_no_engine_is_reported_as_a_placeholder(self) -> None:
        images = imaging.FilmImages()
        with mock.patch.object(images, "backend", return_value=self._backend("placeholder")):
            self.assertEqual("placeholder", images.generate("p", self.path, 1))
        state = activity_state.activity.snapshot()
        self.assertEqual("placeholder", state["phase"])
        self.assertIn("placeholder", state["detail"])
        self.assertTrue(self.path.with_suffix(self.path.suffix + ".placeholder").exists())


class SceneActivityPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        fresh()

    def setUp_doc(self) -> tuple[dict, list]:
        document = store.create_doc(
            "Activity", "\n\n".join(
                f"Sentence {i} has enough words to fill a beat of the narration."
                for i in range(12)
            ),
        )
        scenes = film.plan_chapter(document, 0)
        return document, scenes

    def test_another_documents_still_is_not_this_chapters_painting(self) -> None:
        with temp_home():
            document, scenes = self.setUp_doc()
            activity_state.activity.begin("someone-else", 0, 2)
            payload = film.scene_activity(document, 0, scenes)
            self.assertEqual("idle", payload["phase"])
            self.assertGreaterEqual(payload["total"], 1)
            self.assertEqual(document["id"], payload["doc"])

    def test_counts_come_from_the_painter_for_this_chapter(self) -> None:
        with temp_home():
            document, scenes = self.setUp_doc()
            activity_state.activity.begin(document["id"], 0, scenes[0].index)
            activity_state.activity.phase("painting", "Painting scene 1")
            payload = film.scene_activity(document, 0, scenes)
            self.assertEqual("painting", payload["phase"])
            self.assertEqual(len(scenes), payload["total"])
            self.assertTrue(payload["busy"])


class ScenePainterFailureTests(unittest.TestCase):
    """A swallowed exception left failures indistinguishable from slow successes."""

    def setUp(self) -> None:
        fresh()

    def test_a_failed_scene_keeps_its_reason(self) -> None:
        document = {"id": "fail-doc", "units": ["one"], "title": "Fail"}
        scenes = [film.Scene(index=0, units=[0], text="one", prompt="p", seed=1)]
        painter = film.ScenePainter()
        with mock.patch.object(film, "ensure_scene_image", side_effect=RuntimeError("boom")), \
                mock.patch.object(film, "film_model", return_value=("flux2-klein-4b", 64, 64)), \
                mock.patch.object(film, "still_ready", return_value=False):
            self.assertEqual(1, painter.enqueue(document, 0, scenes))
            self.assertTrue(wait_until(lambda: painter.status(document, 0, scenes)["failed"] == 1))
            status = painter.status(document, 0, scenes)
        self.assertEqual("boom", status["error"])
        state = activity_state.activity.snapshot()
        self.assertEqual("failed", state["phase"])
        self.assertEqual("boom", state["error"])


class SceneActivityRouteTests(unittest.TestCase):
    def test_the_activity_endpoint_answers_without_a_document(self) -> None:
        with temp_home():
            fresh()
            activity_state.activity.begin("doc", 0, 1)
            activity_state.activity.phase("painting", "Painting scene 2")
            body = TestClient(server.app).get("/api/film/activity").json()
            self.assertEqual("painting", body["phase"])
            self.assertFalse(body["determinate"])

    def test_a_pending_scene_payload_carries_the_activity(self) -> None:
        with temp_home():
            fresh()
            document = store.create_doc(
                "Activity", "\n\n".join(
                    f"Sentence {i} has enough words to fill a beat of the narration."
                    for i in range(12)
                ),
            )
            client = TestClient(server.app)
            with mock.patch.object(film.painter, "enqueue") as enqueue:
                response = client.post(
                    "/api/film/scene", json={"doc": document["id"], "chapter": 0, "index": 0}
                )
            self.assertEqual(200, response.status_code, response.text)
            body = response.json()
            self.assertFalse(body["ready"])
            self.assertIn("activity", body)
            self.assertIn("phase", body["activity"])
            enqueue.assert_called_once()


class PlaceholderDeliveryTests(unittest.TestCase):
    """A placeholder is a delivered result, not a still that never arrives."""

    def setUp(self) -> None:
        fresh()

    def _doc(self) -> dict:
        return store.create_doc(
            "Placeholders", "\n\n".join(
                f"Sentence {i} has enough words to fill a beat of the narration."
                for i in range(12)
            ),
        )

    def test_a_placeholder_scene_answers_ready_and_says_so(self) -> None:
        with temp_home():
            document = self._doc()
            client = TestClient(server.app)
            plan = client.get(
                f"/api/film/plan?doc={document['id']}&chapter=0"
            ).json()
            first = plan["scenes"][0]["index"]
            path = film.scene_image_path(document["id"], 0, first)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"prompt card")
            film.placeholder_path(path).write_text("placeholder\n", "utf-8")
            with mock.patch.object(film.painter, "enqueue") as enqueue:
                body = client.post(
                    "/api/film/scene",
                    json={"doc": document["id"], "chapter": 0, "index": first},
                ).json()
            self.assertTrue(body["ready"])
            self.assertTrue(body["placeholder"])
            enqueue.assert_not_called()
            image = client.get(body["url"].split("?")[0])
            self.assertEqual(200, image.status_code)

    def test_the_queue_counts_placeholders_as_done(self) -> None:
        with temp_home():
            document = self._doc()
            scenes = film.plan_chapter(document, 0)
            path = film.scene_image_path(document["id"], 0, scenes[0].index)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"prompt card")
            film.placeholder_path(path).write_text("placeholder\n", "utf-8")
            status = film.painter.status(document, 0, scenes)
            self.assertEqual(1, status["done"])


if __name__ == "__main__":
    unittest.main()
