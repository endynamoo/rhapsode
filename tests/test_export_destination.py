"""An export must be saved where the reader chose, and leave no second copy."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from murmur import server


class ExportNameTests(unittest.TestCase):
    def test_the_name_comes_from_the_title(self) -> None:
        self.assertEqual(
            server.export_name({"title": "The Time Machine", "ext": "mp3"}),
            "The Time Machine.mp3",
        )

    def test_illegal_characters_are_dropped(self) -> None:
        name = server.export_name({"title": 'Dune / Messiah: "book"', "ext": "wav"})
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)
        self.assertTrue(name.endswith(".wav"))

    def test_an_empty_title_still_names_a_file(self) -> None:
        self.assertEqual(server.export_name({"title": "///", "ext": "mp3"}), "murmur.mp3")


class ConsumeTests(unittest.TestCase):
    """`consume=1` deletes the server-side copy once the reader has the bytes."""

    def setUp(self) -> None:
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "job.mp3"
        self.path.write_bytes(b"audio")
        server.JOBS["job1"] = {
            "id": "job1", "state": "done", "file": str(self.path),
            "mime": "audio/mpeg", "ext": "mp3", "title": "A book",
        }
        self.addCleanup(server.JOBS.pop, "job1", None)

    def test_the_file_survives_a_plain_fetch(self) -> None:
        server.export_file("job1")
        self.assertTrue(self.path.exists(), "a client that has not saved it yet needs it")

    def test_consume_removes_it_after_the_response(self) -> None:
        response = server.export_file("job1", consume=True)
        self.assertIsNotNone(response.background)
        response.background.func()
        self.assertFalse(self.path.exists(), "the second copy outlived the download")
        self.assertTrue(server.JOBS["job1"]["consumed"])

    def test_an_unready_job_is_refused(self) -> None:
        from fastapi import HTTPException

        server.JOBS["job1"]["state"] = "running"
        with self.assertRaises(HTTPException):
            server.export_file("job1", consume=True)


if __name__ == "__main__":
    unittest.main()
