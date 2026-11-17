"""Library search: anchors, incremental indexing, ranking, fallback and the API.

The embedding model is mocked everywhere here: CI installs `requirements-test.txt`
and has neither the weights nor onnxruntime, and a test that downloads 23 MB
would be neither fast nor offline. The lexical half is exercised for real
against SQLite's FTS5.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

from murmur import config, search, semantic, server, store

UNITS = [
    "The lighthouse keeper climbed the stairs every evening.",
    "He carried a small oil can and a rag in his coat pocket.",
    "The lens had to be polished before the sun went down, or the beam would be dull.",
    "One night a storm tore the shutter loose and rain came in sideways.",
    "He held the shutter with his shoulder until the wind dropped.",
    "The committee voted to postpone the budget review until the autumn session.",
]


@contextmanager
def temp_home():
    """Point every data path at a throwaway directory for the test."""
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
            store.init()
            try:
                yield base
            finally:
                search.stop()
                store.close()


def make_doc(title: str, units: list[str], pages: list[int] | None = None, toc: list[dict] | None = None) -> dict:
    return store.create_doc(
        title,
        " ".join(units),
        units=units,
        unit_pages=pages or [0] * len(units),
        page_count=max(pages or [0]) + 1 if pages else 0,
        toc=toc or [],
    )


def wait_for_index(timeout: float = 15.0) -> dict:
    """Wait until the background worker has nothing left to do."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = search.status()
        if not status["indexing"]["running"] and status["pending"] == 0:
            return status
        time.sleep(0.05)
    raise AssertionError(f"index did not finish: {search.status()}")


def chunk_rows(doc_id: str) -> list[sqlite3.Row]:
    with search._db() as conn:
        return conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY unit, ord", (doc_id,)
        ).fetchall()


def fake_embedder():
    """A deterministic stand-in for MiniLM: 3 axes, one per topic."""
    def embed(texts):
        matrix = np.zeros((len(texts), 3), dtype=np.float32)
        for index, value in enumerate(texts):
            low = value.lower()
            if "automobile" in low or "car" in low:
                matrix[index] = (1.0, 0.0, 0.0)
            elif "storm" in low or "rain" in low or "weather" in low:
                matrix[index] = (0.0, 1.0, 0.0)
            else:
                matrix[index] = (0.0, 0.0, 1.0)
        return matrix

    return embed


class ChunkAnchorTests(unittest.TestCase):
    def test_a_chunk_carries_the_anchor_the_reader_needs(self) -> None:
        with temp_home():
            doc = make_doc(
                "The Lighthouse",
                UNITS,
                pages=[0, 0, 1, 1, 2, 2],
                toc=[
                    {"title": "Chapter One", "level": 1, "unit": 0},
                    {"title": "Chapter Two", "level": 1, "unit": 3},
                ],
            )
            search.index_all()

            result = search.search("polished lens")
            self.assertEqual("lexical", result["mode"])
            self.assertEqual(1, result["total"])
            hit = result["hits"][0]
            self.assertEqual(doc["id"], hit["docId"])
            self.assertEqual(2, hit["unit"])
            self.assertEqual(1, hit["page"])
            self.assertEqual(0, hit["chapter"])
            self.assertEqual("Chapter One", hit["chapterTitle"])
            self.assertEqual(6, hit["unitCount"])
            self.assertEqual(
                {
                    "docId": doc["id"],
                    "unit": 2,
                    "page": 1,
                    "chunkId": hit["chunkId"],
                    "terms": ["polished", "lens"],
                },
                hit["jump"],
            )
            # The jump target really is the sentence the reader would land on.
            self.assertIn("polished", store.get_doc(doc["id"])["units"][hit["jump"]["unit"]])

            later = search.search("storm shutter")["hits"][0]
            self.assertEqual(3, later["unit"])
            self.assertEqual(1, later["chapter"])
            self.assertEqual("Chapter Two", later["chapterTitle"])

    def test_long_units_are_split_on_a_word_boundary(self) -> None:
        with temp_home():
            words = [f"word{index:03d}" for index in range(120)]
            long_unit = " ".join(words)
            doc = make_doc("Long", [long_unit, "A short second sentence."])
            search.index_all()

            rows = [row for row in chunk_rows(doc["id"]) if row["unit"] == 0]
            self.assertGreater(len(rows), 1)
            for row in rows:
                self.assertLessEqual(len(row["text"]), search.CHUNK_CHARS)
                # Offsets index the original unit and never cut a word apart.
                self.assertEqual(row["text"], long_unit[row["start"] : row["stop"]])
            self.assertEqual(list(range(len(rows))), [row["ord"] for row in rows])
            self.assertEqual(sorted({row["page"] for row in rows}), [0])

            result = search.search("word119")
            self.assertEqual(1, result["total"])
            self.assertIn("word119", result["hits"][0]["snippet"]["text"])
            self.assertEqual(0, result["hits"][0]["unit"])

    def test_snippet_highlights_point_at_the_query_words(self) -> None:
        with temp_home():
            make_doc("The Lighthouse", UNITS)
            search.index_all()
            hit = search.search("polished lens")["hits"][0]
            snippet = hit["snippet"]
            for start, stop in snippet["highlights"]:
                self.assertIn(snippet["text"][start:stop].lower(), {"polished", "lens"})


class IncrementalTests(unittest.TestCase):
    def test_an_unchanged_book_is_not_touched_again(self) -> None:
        with temp_home():
            make_doc("The Lighthouse", UNITS)
            first = search.index_all()
            self.assertEqual(1, first["documents"])
            before = [(row["id"], row["text"]) for row in chunk_rows(
                store.list_docs()[0]["id"]
            )]

            second = search.index_all()
            self.assertEqual(0, second["documents"])
            self.assertEqual(0, second["skipped"])
            self.assertEqual(before, [(row["id"], row["text"]) for row in chunk_rows(
                store.list_docs()[0]["id"]
            )])

    def test_a_changed_book_is_replaced_not_appended(self) -> None:
        with temp_home():
            doc = make_doc("The Lighthouse", UNITS)
            search.index_all()
            self.assertGreater(search.search("lighthouse")["total"], 0)

            new_units = ["A completely different page about bicycles.", "Hills are steep."]
            store.update_doc(
                doc["id"],
                body=" ".join(new_units),
                units=new_units,
                words=len(" ".join(new_units).split()),
                chars=len(" ".join(new_units)),
            )
            summary = search.index_all()
            self.assertEqual(1, summary["documents"])
            self.assertEqual(2, len(chunk_rows(doc["id"])))
            self.assertEqual(0, search.search("lighthouse")["total"])
            self.assertEqual(1, search.search("bicycles")["total"])

    def test_a_rename_keeps_the_chunks_and_only_refreshes_metadata(self) -> None:
        with temp_home():
            doc = make_doc("The Lighthouse", UNITS)
            search.index_all()
            ids = [row["id"] for row in chunk_rows(doc["id"])]

            store.update_doc(doc["id"], title="The Lighthouse Keeper")
            summary = search.index_all()
            self.assertEqual(1, summary["documents"])
            self.assertEqual("unchanged", summary["results"][0]["state"])
            self.assertEqual(ids, [row["id"] for row in chunk_rows(doc["id"])])
            self.assertEqual(
                "The Lighthouse Keeper", search.search("lighthouse")["hits"][0]["title"]
            )

    def test_reading_a_book_does_not_requeue_it(self) -> None:
        with temp_home():
            doc = make_doc("The Lighthouse", UNITS)
            search.index_all()
            self.assertEqual(0, search.status()["pending"])

            store.update_doc(doc["id"], position=3)
            store.touch(doc["id"])
            self.assertEqual(0, search.status()["pending"])
            self.assertEqual(0, search.index_all()["documents"])

    def test_a_library_from_before_this_feature_still_skips_unchanged_books(self) -> None:
        with temp_home():
            doc = make_doc("The Lighthouse", UNITS)
            with store._db() as conn:
                # What an older murmur.db looks like after the ALTER TABLE.
                conn.execute("UPDATE docs SET content_updated = 0 WHERE id = ?", (doc["id"],))
            store.init()  # the migration the app runs at startup
            self.assertGreater(store.get_doc(doc["id"])["contentUpdated"], 0)

            search.index_all()
            self.assertEqual(0, search.status()["pending"])
            self.assertEqual(0, search.index_all()["documents"])

    def test_an_interrupted_index_resumes_without_duplicates(self) -> None:
        with temp_home():
            doc = make_doc("The Lighthouse", UNITS)
            partial = search.index_all(stop_after_units=2)
            self.assertEqual("partial", partial["results"][0]["state"])
            self.assertEqual(2, len(chunk_rows(doc["id"])))
            self.assertEqual(1, search.status()["pending"])

            resumed = search.index_all()
            self.assertEqual("indexed", resumed["results"][0]["state"])
            rows = chunk_rows(doc["id"])
            self.assertEqual(len(UNITS), len(rows))
            self.assertEqual(UNITS, [row["text"] for row in rows])
            self.assertEqual(len({row["unit"] for row in rows}), len(rows))
            self.assertEqual(0, search.status()["pending"])

    def test_deleting_a_document_removes_it_from_the_index(self) -> None:
        with temp_home():
            doc = make_doc("The Lighthouse", UNITS)
            search.index_all()
            store.delete_doc(doc["id"])
            search.index_all()
            status = search.status()
            self.assertEqual(0, status["books"])
            self.assertEqual(0, status["chunks"])
            self.assertEqual(0, search.search("lighthouse")["total"])


class RankingTests(unittest.TestCase):
    def test_ranking_is_deterministic(self) -> None:
        with temp_home():
            make_doc("A", ["The albatross circled the mast.", "The sea was grey."])
            make_doc("B", ["The sailor watched the sea.", "A storm gathered."])
            search.index_all()

            runs = [[hit["chunkId"] for hit in search.search("sea storm", limit=10)["hits"]]
                    for _ in range(5)]
            self.assertEqual(1, len({tuple(run) for run in runs}))

    def test_the_rare_word_wins_over_the_common_one(self) -> None:
        with temp_home():
            make_doc("A", ["The albatross circled the mast.", "The sea was grey."])
            make_doc("B", ["The sailor watched the sea.", "A storm gathered."])
            search.index_all()

            hits = search.search("albatross sea")["hits"]
            self.assertTrue(hits)
            self.assertIn("albatross", hits[0]["snippet"]["text"].lower())

    def test_reading_recently_breaks_a_tie_in_favour_of_the_last_book(self) -> None:
        with temp_home():
            first = make_doc("Old", ["A lantern burned at the gate."])
            second = make_doc("Recent", ["A lantern burned at the gate."])
            search.index_all()

            before = [hit["docId"] for hit in search.search("lantern")["hits"]]
            self.assertEqual({first["id"], second["id"]}, set(before))

            search.record_read(second["id"], 0)
            after = search.search("lantern")["hits"]
            self.assertEqual(second["id"], after[0]["docId"])
            self.assertEqual(1.0, after[0]["recency"])
            self.assertEqual(0.0, [h for h in after if h["docId"] == first["id"]][0]["recency"])


class FallbackTests(unittest.TestCase):
    def test_search_is_lexical_only_when_the_model_is_absent(self) -> None:
        with temp_home(), mock.patch.object(semantic, "available", return_value=False):
            make_doc("The Lighthouse", UNITS)
            search.index_all()
            result = search.search("storm")
            self.assertEqual("lexical", result["mode"])
            self.assertFalse(result["semantic"]["available"])
            self.assertTrue(result["hits"])
            self.assertTrue(all(hit["semanticRank"] is None for hit in result["hits"]))

    def test_a_semantic_failure_downgrades_instead_of_failing(self) -> None:
        with temp_home(), mock.patch.object(semantic, "available", return_value=True), \
                mock.patch.object(semantic, "embed", side_effect=RuntimeError("model exploded")):
            make_doc("The Lighthouse", UNITS)
            search.index_all()
            result = search.search("storm")
            self.assertEqual("lexical", result["mode"])
            self.assertIn("model exploded", result["semantic"]["error"])
            self.assertTrue(result["hits"])


class HybridTests(unittest.TestCase):
    def test_meaning_finds_a_passage_with_no_shared_words(self) -> None:
        with temp_home(), mock.patch.object(semantic, "available", return_value=True), \
                mock.patch.object(semantic, "embed", side_effect=fake_embedder()):
            make_doc("Roads", ["The automobile was parked beside the hedge."])
            make_doc("Weather", ["A storm broke over the harbour."])
            summary = search.index_all()
            self.assertGreater(summary["embedded"], 0)
            self.assertEqual(2, search.status()["embedded"])

            result = search.search("car")
            self.assertEqual("hybrid", result["mode"])
            self.assertTrue(result["semantic"]["available"])
            self.assertGreaterEqual(result["total"], 1)
            car_hit = result["hits"][0]
            self.assertIn("automobile", car_hit["snippet"]["text"].lower())
            self.assertIsNone(car_hit["lexicalRank"])
            self.assertIsNotNone(car_hit["semanticRank"])
            # Unrelated sentences stay out: a small library must not answer
            # every query with every sentence it owns.
            self.assertNotIn("storm", " ".join(hit["snippet"]["text"] for hit in result["hits"]))

    def test_books_indexed_before_the_model_are_backfilled(self) -> None:
        with temp_home():
            make_doc("Roads", ["The automobile was parked beside the hedge."])
            search.index_all()
            self.assertEqual(0, search.status()["embedded"])

            with mock.patch.object(semantic, "available", return_value=True), \
                    mock.patch.object(semantic, "embed", side_effect=fake_embedder()):
                added = search.backfill_embeddings()
                self.assertEqual(1, added)
                self.assertEqual(1, search.status()["embedded"])


class ApiTests(unittest.TestCase):
    def _client(self) -> TestClient:
        return TestClient(server.app)

    def test_a_missing_query_is_a_bad_request(self) -> None:
        with temp_home():
            client = self._client()
            for query in ("", "   "):
                response = client.get("/api/search", params={"q": query})
                self.assertEqual(400, response.status_code, response.text)
                self.assertIn("detail", response.json())

    def test_a_bad_recent_filter_is_a_bad_request(self) -> None:
        with temp_home():
            client = self._client()
            response = client.get("/api/search", params={"q": "storm", "recent": "last week"})
            self.assertEqual(400, response.status_code)
            self.assertIn("recent", response.json()["detail"])

    def test_the_response_contract(self) -> None:
        with temp_home():
            doc = make_doc("The Lighthouse", UNITS, pages=[0, 0, 1, 1, 2, 2])
            search.index_all()
            client = self._client()
            payload = client.get("/api/search", params={"q": "storm", "limit": 5}).json()

            for key in ("query", "mode", "limit", "offset", "total", "tookMs", "hits", "semantic", "terms"):
                self.assertIn(key, payload)
            self.assertEqual("storm", payload["query"])
            self.assertEqual("lexical", payload["mode"])
            self.assertEqual(5, payload["limit"])
            self.assertGreaterEqual(payload["total"], 1)

            hit = payload["hits"][0]
            for key in ("chunkId", "docId", "title", "unit", "page", "chapter", "chapterTitle",
                        "score", "lexicalRank", "semanticRank", "snippet", "jump", "location"):
                self.assertIn(key, hit)
            self.assertEqual(doc["id"], hit["docId"])
            self.assertEqual(doc["title"], hit["title"])
            self.assertEqual(3, hit["unit"])
            self.assertEqual(1, hit["page"])
            snippet = hit["snippet"]
            self.assertIn("text", snippet)
            self.assertIn("highlights", snippet)
            self.assertTrue(snippet["highlights"])
            for start, stop in snippet["highlights"]:
                self.assertEqual("storm", snippet["text"][start:stop].lower())
            self.assertEqual(doc["id"], hit["jump"]["docId"])
            self.assertEqual(3, hit["jump"]["unit"])

    def test_limit_and_offset_page_through_the_same_ranking(self) -> None:
        with temp_home():
            make_doc("A", ["The lantern burned at the gate.", "The lantern went out."])
            make_doc("B", ["Another lantern hung by the door."])
            search.index_all()
            client = self._client()
            page_one = client.get("/api/search", params={"q": "lantern", "limit": 1}).json()
            page_two = client.get("/api/search", params={"q": "lantern", "limit": 1, "offset": 1}).json()
            self.assertEqual(3, page_one["total"])
            self.assertEqual(1, len(page_one["hits"]))
            self.assertEqual(1, len(page_two["hits"]))
            self.assertNotEqual(page_one["hits"][0]["chunkId"], page_two["hits"][0]["chunkId"])

    def test_recent_filters_on_reading_activity(self) -> None:
        with temp_home():
            first = make_doc("Read", ["A lantern burned at the gate."])
            second = make_doc("Unread", ["A lantern hung by the door."])
            search.index_all()
            client = self._client()

            every = client.get("/api/search", params={"q": "lantern"}).json()
            self.assertEqual({first["id"], second["id"]}, {hit["docId"] for hit in every["hits"]})

            client.get(f"/api/docs/{first['id']}")  # opening a book is activity
            recent = client.get("/api/search", params={"q": "lantern", "recent": "7d"}).json()
            self.assertEqual(1, recent["total"])
            self.assertEqual(first["id"], recent["hits"][0]["docId"])

    def test_the_status_endpoint_reports_coverage_and_semantics(self) -> None:
        with temp_home():
            make_doc("The Lighthouse", UNITS)
            search.index_all()
            body = self._client().get("/api/search/status").json()
            self.assertEqual(1, body["books"])
            self.assertEqual(len(UNITS), body["chunks"])
            self.assertEqual(0, body["pending"])
            self.assertIn("indexing", body)
            self.assertIn("semantic", body)
            self.assertEqual("lexical", body["mode"])
            self.assertIn(body["semantic"]["state"], {"missing", "off", "ready"})
            self.assertGreater(body["sizeBytes"], 0)

    def test_the_index_endpoint_runs_in_the_background(self) -> None:
        with temp_home():
            client = self._client()
            created = client.post("/api/docs", json={"title": "Fresh", "text": "A sentence about a lantern."}).json()
            response = client.post("/api/search/index", json={})
            self.assertEqual(202, response.status_code)
            self.assertTrue(response.json()["started"])
            status = wait_for_index()
            self.assertEqual(1, status["books"])
            self.assertEqual(1, search.search("lantern")["total"])

            forced = client.post("/api/search/index", json={"force": True})
            self.assertEqual(202, forced.status_code)
            wait_for_index()
            self.assertEqual(1, search.status()["books"])
            self.assertEqual(created["id"], search.search("lantern")["hits"][0]["docId"])

    def test_indexing_an_unknown_document_is_a_404(self) -> None:
        with temp_home():
            response = self._client().post("/api/search/index", json={"doc": "nope"})
            self.assertEqual(404, response.status_code)

    def test_the_model_endpoint_refuses_when_semantic_search_is_off(self) -> None:
        with temp_home(), mock.patch.dict(os.environ, {"MURMUR_SEMANTIC": "off"}):
            response = self._client().post("/api/search/model", json={"download": True})
            self.assertEqual(400, response.status_code)
            self.assertIn("MURMUR_SEMANTIC", response.json()["detail"])

    def test_deleting_a_document_through_the_api_forgets_its_index(self) -> None:
        with temp_home():
            doc = make_doc("The Lighthouse", UNITS)
            search.index_all()
            client = self._client()
            self.assertEqual(200, client.delete(f"/api/docs/{doc['id']}").status_code)
            self.assertEqual(0, search.status().get("books", 0))
            self.assertEqual(0, client.get("/api/search", params={"q": "lighthouse"}).json()["total"])

    def test_fts_operators_in_the_query_are_not_executed(self) -> None:
        with temp_home():
            make_doc("The Lighthouse", UNITS)
            search.index_all()
            client = self._client()
            for query in ('NEAR("a" "b")', "storm OR (", "*", '"'):
                response = client.get("/api/search", params={"q": query})
                self.assertIn(response.status_code, (200, 400), response.text)
                if response.status_code == 200:
                    self.assertIn("hits", response.json())

    def test_search_does_not_need_an_index_to_answer(self) -> None:
        with temp_home():
            make_doc("The Lighthouse", ["A sentence about a lantern."])
            body = self._client().get("/api/search", params={"q": "lantern"}).json()
            self.assertEqual(0, body["total"])  # not indexed yet, and no crash
            self.assertEqual("lexical", body["mode"])


if __name__ == "__main__":
    unittest.main()
