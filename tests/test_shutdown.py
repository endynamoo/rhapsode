"""Quitting has to give the models back.

Two things hold memory across a session and neither is freed by garbage collection
alone: the voice model inside this process, and the stills painter, a child holding
~13 GiB of VRAM. The child was the only long-lived one outside the kill-on-close job
object, so a hard quit left the card pinned; and browser mode had no quit at all.

These tests hold the wiring, not the gigabytes: that the resident painter is spawned
adopted, that one call releases everything, that the lifespan and the quit route both
reach it, and that a device on the network cannot stop the app.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from murmur import config, engine, imaging, lifecycle, proc, server, store


def _patch_config(name: str, value):
    return mock.patch.object(config, name, value)


class AdoptedPainterTest(unittest.TestCase):
    """The resident painter must be in the job object, like the one-shot run."""

    def test_the_resident_painter_is_spawned_through_proc_spawn(self):
        worker = imaging.FluxWorker(Path("python"), Path("fluxgen.py"))
        boom = RuntimeError("spawn was used")

        with mock.patch.object(proc, "spawn", side_effect=boom) as spawned, \
                mock.patch.object(proc, "popen", side_effect=AssertionError("not adopted")):
            with self.assertRaises(RuntimeError) as caught:
                worker._ensure()

        self.assertIs(caught.exception, boom)
        spawned.assert_called_once()
        self.assertIsNone(worker._proc, "a failed spawn must not be remembered as a worker")

    def test_one_shot_runs_are_still_adopted(self):
        # The fallback path is a fresh interpreter that paints one tile and exits;
        # leaving one of those behind pins the card just as effectively.
        source = Path(imaging.__file__).read_text(encoding="utf-8")
        one_shot = source[source.index("def _run_once"):source.index("def worker(")]
        self.assertIn("proc.spawn(", one_shot)
        self.assertNotIn("proc.popen(", one_shot)


@unittest.skipUnless(os.name == "nt", "job objects are a Windows mechanism")
class JobObjectTest(unittest.TestCase):
    def test_the_job_object_is_created(self):
        # The whole reaping story rests on this handle existing. It did not.
        self.assertTrue(proc._job(), "no kill-on-close job object was created")

    def test_the_extended_limit_struct_matches_the_api(self):
        # SetInformationJobObject rejects the wrong size with ERROR_BAD_LENGTH (24),
        # which is exactly how the job silently failed before: 64 bytes, then 112.
        self.assertEqual(ctypes.sizeof(proc._ExtendedLimit), 144)

    def test_a_spawned_child_is_in_the_job(self):
        child = proc.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not proc.in_job(child.pid):
                time.sleep(0.1)
            self.assertTrue(proc.in_job(child.pid),
                            "the child was spawned outside the job, so nothing reaps it")
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_a_child_outside_the_job_is_reported_as_such(self):
        # The negative case matters: an `in_job` that always answers True would have
        # passed the test above while proving nothing.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            time.sleep(0.3)
            self.assertFalse(proc.in_job(child.pid))
        finally:
            child.kill()
            child.wait(timeout=10)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.stopped = []

        class FakeWorker:
            def stop(self):
                self_stopped = True
                Recorder.append(self)

        Recorder = self.stopped
        self.worker = FakeWorker()

    def test_shutdown_stops_the_shared_worker_and_forgets_it(self):
        imaging._SHARED_WORKER = self.worker
        try:
            imaging.shutdown()
        finally:
            imaging._SHARED_WORKER = None
        self.assertEqual(self.stopped, [self.worker])

    def test_shutdown_is_idempotent(self):
        imaging._SHARED_WORKER = None
        imaging.shutdown()
        imaging.shutdown()

    def test_release_reports_every_step_even_when_one_fails(self):
        calls = []

        def broken():
            calls.append("voice")
            raise RuntimeError("the model was already gone")

        with mock.patch.object(imaging, "shutdown", side_effect=lambda: calls.append("painter")), \
                mock.patch.object(engine.engine, "unload", side_effect=broken), \
                mock.patch("murmur.search.stop", side_effect=lambda timeout=5.0: calls.append("search")), \
                mock.patch.object(store, "close", side_effect=lambda: calls.append("store")):
            steps = lifecycle.release("test")

        self.assertEqual(calls, ["painter", "voice", "search", "store"])
        self.assertEqual(steps["painter"], "ok")
        self.assertIn("RuntimeError", steps["voice"])

    def test_unload_drops_the_model(self):
        engine.engine._model = object()
        engine.engine._g2p["ja"] = object()
        try:
            engine.engine.unload()
            self.assertIsNone(engine.engine._model)
            self.assertEqual(engine.engine._g2p, {})
        finally:
            engine.engine._model = None


class LifespanReleaseTest(unittest.TestCase):
    """uvicorn stopping is one of the three ways out, so it must release too."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.patches = [
            _patch_config("HOME", home),
            _patch_config("DB_PATH", home / "murmur.db"),
            _patch_config("EXPORT_DIR", home / "exports"),
            _patch_config("FILM_DIR", home / "film"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        store.close()
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def test_the_lifespan_releases_when_the_server_stops(self):
        with mock.patch.object(server.engine, "warm_async"), \
                mock.patch("murmur.search.start_background"), \
                mock.patch.object(lifecycle, "release") as released:
            with TestClient(server.app) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                released.assert_not_called()
        released.assert_called_once()
        self.assertIn("stopped", released.call_args.args[0])


class QuitRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.patches = [
            _patch_config("HOME", home),
            _patch_config("DB_PATH", home / "murmur.db"),
            _patch_config("EXPORT_DIR", home / "exports"),
            _patch_config("FILM_DIR", home / "film"),
        ]
        for patch in self.patches:
            patch.start()
        self.started: list[dict] = []
        self.quit_patch = mock.patch.object(
            lifecycle, "quit_process",
            side_effect=lambda **kwargs: self.started.append(kwargs))
        self.quit_patch.start()

    def tearDown(self):
        self.quit_patch.stop()
        store.close()
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def _wait(self, count: int = 1, seconds: float = 3.0) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline and len(self.started) < count:
            time.sleep(0.02)

    def test_a_local_request_can_quit(self):
        with mock.patch.object(server.engine, "warm_async"), \
                mock.patch("murmur.search.start_background"):
            # A loopback client *and* a loopback Host, which is what the guard
            # checks: TestClient's default "testclient" host is neither.
            with TestClient(server.app, base_url="http://127.0.0.1:8765",
                            client=("127.0.0.1", 43210)) as client:
                response = client.post("/api/quit")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["quitting"])
        self._wait()
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.started[0].get("reason"), "quit button")

    def test_a_device_on_the_network_cannot_quit(self):
        # A phone on the LAN must never be able to stop the machine's app.
        with mock.patch.object(server.engine, "warm_async"), \
                mock.patch("murmur.search.start_background"):
            with TestClient(server.app, base_url="http://203.0.113.9:8765",
                            client=("203.0.113.9", 43210)) as client:
                response = client.post("/api/quit")
        self.assertEqual(response.status_code, 403, response.text)
        time.sleep(0.15)
        self.assertEqual(self.started, [])


class QuitProcessTest(unittest.TestCase):
    def test_quit_process_releases_then_exits(self):
        order: list[str] = []
        with mock.patch.object(lifecycle, "release", side_effect=lambda reason: order.append("release")), \
                mock.patch("os._exit", side_effect=lambda code: order.append(f"exit {code}")):
            lifecycle.quit_process("test")
        self.assertEqual(order, ["release", "exit 0"])


if __name__ == "__main__":
    unittest.main()
