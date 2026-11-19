"""A scanned page has no text layer: its word boxes have to come from OCR.

The page image is what the reader looks at, so the mark has to be drawn on the
page - which needs per-word positions, and a scanned page only has pixels. These
tests cover the boxes themselves, the pixel-to-fraction conversion, and the
decision to ask OCR only when the text layer is empty.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import config, library_api


class NormaliseBoxesTests(unittest.TestCase):
    """Pixels in, fractions of the page out - the reader draws with fractions."""

    def test_pixels_become_fractions(self) -> None:
        words = [{"text": "lantern", "x": 50, "y": 100, "w": 25, "h": 10}]
        boxes = library_api._normalise_boxes(200, 400, words)
        self.assertEqual(
            [{"text": "lantern", "x": 0.25, "y": 0.25, "w": 0.125, "h": 0.025}],
            boxes,
        )

    def test_blank_text_and_zero_sized_pages_are_dropped(self) -> None:
        self.assertEqual([], library_api._normalise_boxes(200, 400, [{"text": "  ", "x": 1, "y": 1, "w": 4, "h": 4}]))
        self.assertEqual([], library_api._normalise_boxes(0, 400, [{"text": "x", "x": 1, "y": 1, "w": 4, "h": 4}]))

    def test_a_negative_box_is_clamped_not_shipped(self) -> None:
        boxes = library_api._normalise_boxes(100, 100, [{"text": "x", "x": 0, "y": 0, "w": -5, "h": -2}])
        self.assertEqual(0.0, boxes[0]["w"])
        self.assertEqual(0.0, boxes[0]["h"])


class OcrFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pages = Path(self.tmp.name)
        patcher = mock.patch.object(config, "PAGES_DIR", self.pages)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.doc_id = "scanned00001"
        self.source = self.pages / f"{self.doc_id}.pdf"
        self.source.write_bytes(b"%PDF-1.4 not really a pdf")
        self.doc = {"id": self.doc_id, "viewer": "pdf", "pageCount": 1}

    def _patch_library(self, *, words, ocr_boxes, calls=None):
        def reader(_doc_id, page, _dpi):
            target = self.pages / f"{self.doc_id}_{page}_140.png"
            target.write_bytes(b"png bytes")
            return target

        def boxes(_data):
            if calls is not None:
                calls.append(1)
            return ocr_boxes

        return [
            mock.patch.object(library_api.store, "get_doc", return_value=self.doc),
            mock.patch.object(library_api.store, "doc_file", return_value=self.source),
            mock.patch.object(library_api, "_render_page_png", side_effect=reader),
            mock.patch.object(library_api.ocr, "available", return_value=True),
            mock.patch.object(library_api.ocr, "word_boxes_bytes", side_effect=boxes),
            # The text layer of a scanned page is nothing at all; a text page has
            # its own boxes in PDF points, which the route normalises.
            mock.patch.object(
                library_api,
                "_text_layer_boxes",
                return_value=(160.0, 320.0, "", words),
            ),
        ]

    def _call(self, *, text_words, ocr_boxes, calls=None):
        stack = self._patch_library(words=text_words, ocr_boxes=ocr_boxes, calls=calls)
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)
        return library_api.doc_page_words(self.doc_id, 0)

    def test_a_page_without_a_text_layer_gets_ocr_boxes(self) -> None:
        payload = self._call(
            text_words=[],
            ocr_boxes={"width": 100, "height": 200,
                       "words": [{"text": "EXPERIMENTAL", "x": 10, "y": 20, "w": 40, "h": 6}]},
        )
        self.assertEqual("ocr", payload["origin"])
        self.assertEqual(
            [{"text": "EXPERIMENTAL", "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.03}],
            payload["words"],
        )

    def test_a_page_with_a_text_layer_never_calls_ocr(self) -> None:
        calls: list[int] = []
        payload = self._call(
            # points on a 160 x 320 page, already flipped to the reader's origin
            text_words=[{"text": "lantern", "x": 32.0, "y": 96.0, "w": 16.0, "h": 6.4}],
            ocr_boxes=None,
            calls=calls,
        )
        self.assertEqual([], calls, "OCR must stay a fallback, not a first choice")
        self.assertEqual("text", payload["origin"])
        self.assertEqual(
            [{"text": "lantern", "x": 0.2, "y": 0.3, "w": 0.1, "h": 0.02}],
            payload["words"],
        )

    def test_no_ocr_backend_leaves_the_page_without_words(self) -> None:
        payload = self._call(text_words=[], ocr_boxes=None)
        self.assertEqual([], payload["words"])

    def test_the_answer_is_cached_so_ocr_runs_once_per_page(self) -> None:
        calls: list[int] = []
        payload = self._call(
            text_words=[],
            ocr_boxes={"width": 100, "height": 200,
                       "words": [{"text": "one", "x": 1, "y": 1, "w": 5, "h": 5}]},
            calls=calls,
        )
        self.assertEqual("ocr", payload["origin"])
        cache_file = self.pages / f"{self.doc_id}_0_words.json"
        self.assertTrue(cache_file.exists())
        written = json.loads(cache_file.read_text("utf-8"))
        self.assertEqual("ocr", written["origin"])
        # A second visit answers from the file: the same words, one OCR call total.
        self.assertEqual(1, len(calls))

    def test_a_cache_file_written_before_the_fallback_is_not_trusted(self) -> None:
        cache_file = self.pages / f"{self.doc_id}_0_words.json"
        fingerprint = {"size": self.source.stat().st_size,
                       "mtime": int(self.source.stat().st_mtime)}
        cache_file.write_text(
            json.dumps({"page": 0, "words": [], "source": fingerprint}), "utf-8"
        )
        payload = self._call(
            text_words=[],
            ocr_boxes={"width": 100, "height": 200,
                       "words": [{"text": "later", "x": 1, "y": 1, "w": 5, "h": 5}]},
        )
        self.assertEqual("ocr", payload["origin"])
        self.assertEqual("later", payload["words"][0]["text"])


if __name__ == "__main__":
    unittest.main()
