"""Auto Generate: the setting, the job flag, and stopping queued work.

The setting is off by default on purpose — a chapter film can hold the GPU for an
hour, so it is a choice a user makes, not something opening a book starts. These
tests pin the default, the queue ordering rule (one worker, chapter order) and
that "stop" drains queued auto jobs without touching the chapter painting or a
chapter the user queued by hand.
"""
from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from murmur import config, film, server, store


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


def document() -> dict:
    doc = store.create_doc(
        "Chapters", "\n\n".join(
            f"Sentence {i} has enough words to fill a beat of the narration."
            for i in range(60)
        ),
    )
    # Chapters are pages; pretend this one has three so ordering is meaningful.
    doc["pageCount"] = 3
    return doc


class AutoGenerateSettingTests(unittest.TestCase):
    def test_a_fresh_install_automates_scenes_only(self) -> None:
        with temp_home():
            settings = store.get_settings()
            # Scenes on: reading a book quietly paints ahead. Films off: one can
            # hold the GPU for an hour, so it stays a gesture.
            self.assertTrue(settings["autoScenes"])
            self.assertFalse(settings["autoFilms"])
            self.assertNotIn("autoGenerate", store.DEFAULT_SETTINGS)

    def test_the_choice_survives_a_restart(self) -> None:
        with temp_home():
            store.set_settings({"autoScenes": False, "autoFilms": True})
            store.close()
            store.init()
            settings = store.get_settings()
            self.assertFalse(settings["autoScenes"])
            self.assertTrue(settings["autoFilms"])

    def test_the_old_single_switch_carries_over_to_chapter_films(self) -> None:
        with temp_home():
            with store._db() as conn:
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES('autoGenerate','true')"
                )
            settings = store.get_settings()
            self.assertTrue(settings["autoFilms"])
            self.assertTrue(settings["autoScenes"])
            self.assertNotIn("autoGenerate", settings)

    def test_an_install_that_had_it_off_stays_off(self) -> None:
        with temp_home():
            with store._db() as conn:
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES('autoGenerate','false')"
                )
            self.assertFalse(store.get_settings()["autoFilms"])


class AutoJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = mock.Mock()
        film.JOBS.clear()
        # Never start a worker: these tests inspect the queue, they do not paint.
        self.patches = [
            mock.patch.object(film, "_FILM_QUEUE", self.queue),
            mock.patch.object(film, "_ensure_film_worker", lambda: None),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        film.JOBS.clear()

    def _start(self, doc: dict, chapter: int, auto: bool) -> dict:
        return film.start_job(doc, chapter, "af_heart", auto=auto)
    def test_a_job_records_whether_it_was_automatic(self) -> None:
        with temp_home():
            doc = document()
            auto = self._start(doc, 0, True)
            manual = self._start(doc, 1, False)
            self.assertTrue(auto["auto"])
            self.assertFalse(manual["auto"])
            self.assertEqual(auto["autoEpoch"], manual["autoEpoch"])

    def test_chapters_queue_in_the_order_they_were_asked_for(self) -> None:
        with temp_home():
            doc = document()
            for chapter in (0, 1):
                self._start(doc, chapter, True)
            chapters = [call.args[0][1] for call in self.queue.put.call_args_list]
            self.assertEqual([0, 1], chapters)

    def test_stop_drains_queued_auto_jobs_only(self) -> None:
        with temp_home():
            doc = document()
            first = self._start(doc, 0, True)
            second = self._start(doc, 1, True)
            manual = self._start(doc, 2, False)
            # The first one is the one painting; stop must leave it alone.
            first["state"] = "running"
            stopped = film.stop_auto()
            self.assertEqual(1, stopped)
            self.assertEqual("running", first["state"])
            self.assertFalse(first.get("cancelled", False))
            self.assertEqual("cancelled", second["state"])
            self.assertEqual("queued", manual["state"])

    def test_a_later_auto_job_is_not_drained_by_an_older_stop(self) -> None:
        with temp_home():
            doc = document()
            self._start(doc, 0, True)
            film.stop_auto()
            later = self._start(doc, 1, True)
            self.assertEqual("queued", later["state"])


class StopAutoRouteTests(unittest.TestCase):
    def test_the_route_reports_how_many_it_stopped(self) -> None:
        with temp_home(), \
                mock.patch.object(film, "_ensure_film_worker", lambda: None), \
                mock.patch.object(film, "_FILM_QUEUE", mock.Mock()), \
                mock.patch.object(film, "stop_auto", return_value=3) as stop:
            body = TestClient(server.app).post("/api/film/stop-auto").json()
            self.assertEqual({"stopped": 3}, body)
            stop.assert_called_once()

    def test_start_film_carries_the_auto_flag_from_the_body(self) -> None:
        with temp_home():
            doc = document()
            with mock.patch.object(film, "start_job", return_value={"state": "queued"}) as start:
                TestClient(server.app).post(
                    f"/api/docs/{doc['id']}/film", json={"chapter": 0, "auto": True}
                )
            self.assertTrue(start.call_args.kwargs["auto"])


if __name__ == "__main__":
    unittest.main()
