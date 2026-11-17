"""The in-memory caches: bounded, correct, and actually skipping the disk."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from murmur import cache, config, store


class LRUTests(unittest.TestCase):
    def test_evicts_the_least_recently_used(self) -> None:
        lru = cache.LRUCache(maxsize=2, name="t")
        lru.put("a", 1)
        lru.put("b", 2)
        lru.get("a")          # a is now the most recent
        lru.put("c", 3)       # b goes
        self.assertEqual(1, lru.get("a"))
        self.assertIsNone(lru.get("b"))
        self.assertEqual(3, lru.get("c"))

    def test_counts_hits_and_misses(self) -> None:
        lru = cache.LRUCache(maxsize=4)
        lru.put("k", "v")
        lru.get("k")
        lru.get("missing")
        stats = lru.stats()
        self.assertEqual(1, stats["hits"])
        self.assertEqual(1, stats["misses"])
        self.assertEqual(0.5, stats["hit_rate"])

    def test_get_or_load_calls_the_loader_once(self) -> None:
        lru = cache.LRUCache(maxsize=4)
        loader = mock.MagicMock(return_value="value")
        self.assertEqual("value", lru.get_or_load("k", loader))
        self.assertEqual("value", lru.get_or_load("k", loader))
        loader.assert_called_once()

    def test_none_is_not_cached(self) -> None:
        lru = cache.LRUCache(maxsize=4)
        loader = mock.MagicMock(return_value=None)
        lru.get_or_load("k", loader)
        lru.get_or_load("k", loader)
        self.assertEqual(2, loader.call_count)


class TranslationCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        base = Path(self.folder.name)
        patcher = mock.patch.multiple(
            config,
            HOME=base,
            MODELS_DIR=base / "models",
            CACHE_DIR=base / "cache",
            PAGES_DIR=base / "cache" / "pages",
            FILES_DIR=base / "files",
            EXPORT_DIR=base / "exports",
            FILM_DIR=base / "film",
            DB_PATH=base / "murmur.db",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.folder.cleanup)
        store.close()
        config.ensure_dirs()
        store.init()

    def tearDown(self) -> None:
        store.close()

    def test_second_read_never_queries_the_database(self) -> None:
        store.translation_put("k1", "en", "es", "hello", "hola")
        with mock.patch.object(store, "_db") as db:
            self.assertEqual("hola", store.translation_get("k1"))
            self.assertEqual("hola", store.translation_get("k1"))
        db.assert_not_called()

    def test_a_miss_still_reads_through_and_populates(self) -> None:
        store.translation_put("k2", "en", "fr", "hello", "bonjour")
        store._translations.clear()
        self.assertEqual("bonjour", store.translation_get("k2"))
        self.assertEqual("bonjour", store._translations.get("k2"))

    def test_close_clears_the_cache(self) -> None:
        store.translation_put("k3", "en", "it", "hello", "ciao")
        store.close()
        self.assertEqual(0, store._translations.stats()["entries"])


class LookupCostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        base = Path(self.folder.name)
        patcher = mock.patch.multiple(
            config,
            HOME=base,
            MODELS_DIR=base / "models",
            CACHE_DIR=base / "cache",
            PAGES_DIR=base / "cache" / "pages",
            FILES_DIR=base / "files",
            EXPORT_DIR=base / "exports",
            FILM_DIR=base / "film",
            DB_PATH=base / "murmur.db",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.folder.cleanup)
        store.close()
        config.ensure_dirs()
        store.init()

    def tearDown(self) -> None:
        store.close()

    def test_cached_lookup_is_much_cheaper_than_a_query(self) -> None:
        store.translation_put("hot", "en", "es", "a sentence", "una frase")
        rounds = 300

        start = time.perf_counter()
        for _ in range(rounds):
            store.close()
            store.init()
            store._translations.clear()
            store.translation_get("hot")
        cold = time.perf_counter() - start

        start = time.perf_counter()
        for _ in range(rounds):
            store.translation_get("hot")
        hot = time.perf_counter() - start

        self.assertLess(hot, cold / 4, f"cached {hot:.4f}s vs uncached {cold:.4f}s")


if __name__ == "__main__":
    unittest.main()
