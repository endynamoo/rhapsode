"""Assets must be revalidated, so a reload cannot run a stale module."""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from murmur import server


class AssetCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def test_assets_ask_the_browser_to_revalidate(self) -> None:
        response = self.client.get("/assets/app.js")
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-cache", response.headers.get("cache-control"))

    def test_a_revalidation_still_gets_304(self) -> None:
        first = self.client.get("/assets/app.js")
        tag = first.headers.get("etag")
        self.assertTrue(tag, "StaticFiles should still send an ETag")
        again = self.client.get("/assets/app.js", headers={"If-None-Match": tag})
        self.assertEqual(304, again.status_code)
        self.assertEqual("no-cache", again.headers.get("cache-control"))

    def test_the_index_is_not_cached_either(self) -> None:
        response = self.client.get("/")
        self.assertEqual("no-cache", response.headers.get("cache-control"))


if __name__ == "__main__":
    unittest.main()
