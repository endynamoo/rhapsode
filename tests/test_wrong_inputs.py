"""Wrong input gets a sentence, not a stack trace or a setting it cannot use.

The upload endpoint used to answer a damaged PDF or EPUB with a bare 500, because
PDFium and zipfile raise their own exception types and only `ExtractError` was
translated. The settings endpoint stored whatever it was handed, including a
playback rate of `"fast"`, which the browser then refuses to apply - a stored
value can break playback for that install until someone edits the database.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import config, extract, store


class DamagedFileTests(unittest.TestCase):
    def test_a_truncated_pdf_is_refused_with_a_sentence(self) -> None:
        with self.assertRaises(extract.ExtractError) as caught:
            extract.extract_upload("book.pdf", b"%PDF-1.7\nthis is not a pdf at all")
        message = str(caught.exception)
        self.assertIn("PDF could not be opened", message)
        self.assertIn("damaged", message)

    def test_an_empty_pdf_is_refused_too(self) -> None:
        with self.assertRaises(extract.ExtractError) as caught:
            extract.extract_upload("empty.pdf", b"")
        self.assertIn("PDF could not be opened", str(caught.exception))

    def test_a_file_that_is_not_an_epub_is_refused(self) -> None:
        with self.assertRaises(extract.ExtractError) as caught:
            extract.extract_upload("book.epub", b"never zipped")
        self.assertIn("EPUB could not be opened", str(caught.exception))

    def test_a_damaged_file_still_reports_what_went_wrong(self) -> None:
        with self.assertRaises(extract.ExtractError) as caught:
            extract.extract_upload("book.pdf", b"%PDF-1.7\nnope")
        self.assertIn("PdfiumError", str(caught.exception), "the cause must not vanish")


class SettingsTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="rhapsode-settings-"))
        self.base = base
        patcher = mock.patch.multiple(
            config,
            HOME=base,
            MODELS_DIR=base / "models",
            MODEL_PATH=base / "models" / "kokoro-v1.0.onnx",
            VOICES_PATH=base / "models" / "voices-v1.0.bin",
            CACHE_DIR=base / "cache",
            PAGES_DIR=base / "cache" / "pages",
            FILES_DIR=base / "files",
            EXPORT_DIR=base / "exports",
            FILM_DIR=base / "film",
            DB_PATH=base / "murmur.db",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        store.init()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        # The store keeps one connection open per database path; release it
        # before the temporary folder goes, or Windows refuses to delete it.
        connection = getattr(store, "_shared", None)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
            store._shared = None
            store._shared_path = None
        if getattr(self, "base", None):
            shutil.rmtree(self.base, ignore_errors=True)

    def test_a_playback_rate_that_is_not_a_number_is_dropped(self) -> None:
        settings = store.set_settings({"speed": "fast"})
        self.assertEqual(1.0, settings["speed"], "the stored rate must stay usable")

    def test_a_number_is_kept_and_stored_as_the_right_type(self) -> None:
        self.assertEqual(1.5, store.set_settings({"speed": 1.5})["speed"])
        self.assertEqual(22, store.set_settings({"fontSize": 22.0})["fontSize"])
        self.assertIsInstance(store.set_settings({"fontSize": 22.7})["fontSize"], int)

    def test_a_switch_that_is_not_a_boolean_is_dropped(self) -> None:
        before = store.set_settings({})["autoFilms"]
        self.assertEqual(before, store.set_settings({"autoFilms": "yes"})["autoFilms"])
        self.assertTrue(store.set_settings({"autoFilms": True})["autoFilms"])

    def test_a_text_setting_that_is_not_text_is_dropped(self) -> None:
        store.set_settings({"theme": "paper"})
        self.assertEqual("paper", store.set_settings({"theme": 7})["theme"])

    def test_an_out_of_range_number_is_clamped(self) -> None:
        self.assertEqual(4.0, store.set_settings({"speed": 99})["speed"])
        self.assertEqual(0.25, store.set_settings({"speed": 0})["speed"])
        self.assertEqual(72, store.set_settings({"fontSize": 5000})["fontSize"])

    def test_an_unknown_key_is_still_ignored(self) -> None:
        self.assertNotIn("nope", store.set_settings({"nope": 1}))


if __name__ == "__main__":
    unittest.main()
