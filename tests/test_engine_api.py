"""The engine API: read the profile, switch it, restart the painter."""
from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from murmur import config, server, store


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


class EngineApiTests(unittest.TestCase):
    def test_reading_the_engine_lists_profiles_and_painter(self) -> None:
        with temp_home():
            client = TestClient(server.app)
            body = client.get("/api/engine").json()
            self.assertIn(body["profile"], {"lite", "balanced", "max", "cpu"})
            self.assertTrue(body["summary"])
            names = {item["name"] for item in body["profiles"]}
            self.assertEqual({"lite", "balanced", "max", "cpu"}, names)
            self.assertIn("painter", body)

    def test_switching_profile_updates_settings_and_restarts(self) -> None:
        with temp_home(), mock.patch("murmur.imaging.restart_painter") as restart:
            client = TestClient(server.app)
            response = client.post("/api/engine/profile", json={"profile": "lite"})
            self.assertEqual(200, response.status_code, response.text)
            restart.assert_called_once()
            body = response.json()
            self.assertEqual("lite", body["profile"])
            settings = store.get_settings()
            self.assertEqual("fast", settings["filmQuality"])
            self.assertFalse(settings["polishStills"])

    def test_unknown_profile_is_rejected(self) -> None:
        with temp_home():
            client = TestClient(server.app)
            response = client.post("/api/engine/profile", json={"profile": "quantum"})
            self.assertEqual(400, response.status_code)

    def test_restart_endpoint_returns_painter_state(self) -> None:
        with temp_home():
            client = TestClient(server.app)
            response = client.post("/api/engine/restart")
            self.assertEqual(200, response.status_code)
            self.assertIn("running", response.json()["painter"])

    def test_overrides_beat_the_preset(self) -> None:
        with temp_home(), mock.patch("murmur.imaging.restart_painter"):
            client = TestClient(server.app)
            body = client.post(
                "/api/engine/profile",
                json={"profile": "balanced", "reserve_mib": 4000},
            ).json()
            self.assertEqual(4000, body["settings"]["reserve_mib"])


if __name__ == "__main__":
    unittest.main()
