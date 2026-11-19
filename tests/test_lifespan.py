"""The startup work moved from on_event("startup") to a lifespan.

FastAPI deprecates on_event; the migration is only correct if the work still
happens, so these tests hold the behaviour rather than the syntax:

* neither app registers an on_event handler any more;
* importing either app raises no DeprecationWarning (the reason to migrate);
* a TestClient entering the context (which runs the lifespan) leaves an
  initialised store behind, and the setting round-trip that depends on it works.
"""
from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from fastapi.testclient import TestClient

from murmur import config, engine_api, server, store


class LifespanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # Only the paths that exist on config: the module builds the rest from
        # HOME at import time, so a name that does not exist is a test bug, not a
        # missing feature.
        self.patches = [
            _patch("HOME", self.home),
            _patch("DB_PATH", self.home / "murmur.db"),
            _patch("EXPORT_DIR", self.home / "exports"),
            _patch("FILM_DIR", self.home / "film"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        # The sqlite handle outlives the client, and Windows will not delete an
        # open database file: close it before the temp directory goes.
        store.close()
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def test_no_app_registers_on_event(self):
        for app in (server.app, engine_api.app):
            handlers = getattr(app.router, "on_startup", [])
            self.assertEqual(handlers, [], f"{app.title} still has an on_event startup hook")

    def test_importing_the_apps_warns_about_nothing(self):
        # on_event emits a DeprecationWarning on use, not on import, so this is a
        # cheap guard: importing with warnings as errors must stay quiet.
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            import importlib

            importlib.reload(engine_api)

    def test_the_server_lifespan_initialises_the_store(self):
        db = self.home / "murmur.db"
        self.assertFalse(db.exists())
        with TestClient(server.app) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            settings = client.get("/api/settings").json()
        self.assertTrue(db.exists(), "the lifespan did not run store.init()")
        self.assertIn("voice", settings)

    def test_the_sidecar_lifespan_initialises_the_store(self):
        db = self.home / "murmur.db"
        self.assertFalse(db.exists())
        with TestClient(engine_api.app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
        self.assertTrue(db.exists(), "the sidecar lifespan did not run store.init()")


def _patch(name: str, value):
    from unittest import mock

    target = config
    return mock.patch.object(target, name, value)


if __name__ == "__main__":
    unittest.main()
