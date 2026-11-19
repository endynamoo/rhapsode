"""Tests for the warm start: cheap availability probe and load-before-the-click."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from murmur import film, imaging


def make_venv(root: Path, *, torch: bool = True, diffusers: bool = True) -> Path:
    """A directory shaped like a Windows venv, so the probe can be tested offline."""
    site = root / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    for name, present in (("torch", torch), ("diffusers", diffusers)):
        if present:
            package = site / name
            package.mkdir(exist_ok=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
    (site / "distutils-precedence.pth").write_text(
        "import os; var = 'x'; enabled = __import__('_distutils_hack')", encoding="utf-8")
    scripts = root / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    python = scripts / "python.exe"
    python.write_text("", encoding="utf-8")
    return python


class AvailabilityTests(unittest.TestCase):
    def test_packages_are_found_by_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            python = make_venv(Path(tmp))
            self.assertTrue(imaging._has_packages(python))

    def test_a_missing_package_is_not_reported_as_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            python = make_venv(Path(tmp), diffusers=False)
            self.assertFalse(imaging._has_packages(python))

    def test_a_pth_file_can_provide_a_package_from_elsewhere(self) -> None:
        # This machine's image venv gets torch from an unrelated studio environment
        # through `_torch_layer.pth`; a site-packages listing alone would call the
        # engine missing and paint a placeholder.
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            python = make_venv(Path(tmp), torch=False)
            borrowed = Path(other) / "torch"
            borrowed.mkdir()
            (borrowed / "__init__.py").write_text("", encoding="utf-8")
            site = Path(tmp) / "Lib" / "site-packages"
            (site / "_torch_layer.pth").write_text(str(Path(other)), encoding="utf-8")
            self.assertTrue(imaging._has_packages(python))

    def test_available_does_not_launch_the_image_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            python = make_venv(Path(tmp))
            backend = imaging.FluxPythonBackend()
            with mock.patch.object(type(backend), "python", python), \
                    mock.patch.object(imaging, "proc") as spy, \
                    mock.patch.object(type(backend), "_probe", None):
                spy.run.side_effect = AssertionError("available() imported torch")
                self.assertTrue(backend.available())
                spy.run.assert_not_called()

    def test_a_negative_answer_still_asks_the_interpreter(self) -> None:
        # Nothing found on disk is not proof: a .pth that runs code can add paths
        # this cannot read, so the import probe must still decide.
        with tempfile.TemporaryDirectory() as tmp:
            python = make_venv(Path(tmp), torch=False)
            backend = imaging.FluxPythonBackend()
            with mock.patch.object(type(backend), "python", python), \
                    mock.patch.object(type(backend), "_probe", None), \
                    mock.patch.object(imaging, "proc") as spy:
                spy.run.return_value = mock.Mock(returncode=1)
                self.assertFalse(backend.available())
                spy.run.assert_called_once()

    def test_available_is_false_without_the_interpreter(self) -> None:
        backend = imaging.FluxPythonBackend()
        with mock.patch.object(type(backend), "python", Path("no-such-python.exe")), \
                mock.patch.object(type(backend), "_probe", None):
            self.assertFalse(backend.available())


def warmable(*, warm_result=True, side_effect=None, alive=False):
    """A stills-engine mock that passes the guards: real backend, RAM above floor.

    The load itself happens in `FilmImages.warm()`, and `backend.worker()` is only
    consulted to notice an engine that is already resident.
    """
    gen = mock.Mock()
    painter = mock.Mock()
    painter.alive = alive
    if side_effect is not None:
        gen.warm.side_effect = side_effect
    else:
        gen.warm.return_value = warm_result
    backend = mock.Mock()
    backend.name = "flux2-klein-diffusers"
    backend.worker = mock.Mock(return_value=painter)
    gen.backend.return_value = backend
    return gen, painter


class WarmEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        film._warm_thread = None

    def tearDown(self) -> None:
        film._warm_thread = None

    def patches(self, gen, *, settings=None, ram=None):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, {"MURMUR_WARM_STILLS": "1"}))
        stack.enter_context(mock.patch(
            "murmur.store.get_settings", return_value=settings or {"warmStills": True}))
        stack.enter_context(mock.patch.object(imaging, "image_gen", gen))
        stack.enter_context(mock.patch.object(
            imaging, "ram_info",
            return_value=ram or {"free_gib": 12.0, "floor_mib": 6000}))
        return stack

    def test_warming_is_skipped_when_the_setting_is_off(self) -> None:
        gen, painter = warmable()
        with self.patches(gen, settings={"warmStills": False}):
            self.assertFalse(film.warm_engine())
        gen.warm.assert_not_called()

    def test_the_environment_can_forbid_warming(self) -> None:
        gen, painter = warmable()
        with mock.patch.dict(os.environ, {"MURMUR_WARM_STILLS": "0"}), \
                mock.patch("murmur.store.get_settings", return_value={"warmStills": True}), \
                mock.patch.object(imaging, "image_gen", gen):
            self.assertFalse(film.warm_engine())
        gen.warm.assert_not_called()

    def test_an_install_without_the_engine_is_left_alone(self) -> None:
        gen = mock.Mock()
        gen.backend.return_value = mock.Mock(spec=["name"], name="placeholder")
        with self.patches(gen), mock.patch.object(film, "activity") as activity:
            self.assertFalse(film.warm_engine())
        gen.warm.assert_not_called()
        activity.phase.assert_not_called()

    def test_a_machine_below_the_ram_floor_is_not_warmed(self) -> None:
        # The floor refuses a cold load; warming must not turn that into a visible
        # failure every time somebody opens Cinema.
        gen, painter = warmable()
        with self.patches(gen, ram={"free_gib": 3.0, "floor_mib": 6000}), \
                mock.patch.object(film, "activity") as activity:
            self.assertFalse(film.warm_engine())
        gen.warm.assert_not_called()
        activity.phase.assert_not_called()

    def test_warming_starts_the_load_in_the_background(self) -> None:
        released = threading.Event()

        def slow_warm() -> bool:
            released.wait(5)
            return True

        gen, painter = warmable(side_effect=slow_warm)
        with self.patches(gen):
            self.assertTrue(film.warm_engine())          # returns at once...
            self.assertIsNotNone(film._warm_thread)      # ...with a load in flight
            self.assertTrue(film._warm_thread.is_alive())
            released.set()
            film._warm_thread.join(5)
        gen.warm.assert_called_once()

    def test_an_already_resident_painter_is_not_loaded_again(self) -> None:
        gen, painter = warmable(alive=True)
        with self.patches(gen), mock.patch.object(film, "activity") as activity:
            self.assertTrue(film.warm_engine())
        gen.warm.assert_not_called()
        activity.phase.assert_not_called()

    def test_a_failing_load_does_not_raise(self) -> None:
        gen, painter = warmable(side_effect=RuntimeError("not enough VRAM"))
        with self.patches(gen), mock.patch.object(film, "activity") as activity:
            self.assertTrue(film.warm_engine())
            film._warm_thread.join(5)
            activity.finish.assert_called()
            self.assertIn("not enough VRAM", str(activity.finish.call_args))


class FilmImagesWarmTests(unittest.TestCase):
    def test_warm_loads_the_backend_worker_and_marks_the_session(self) -> None:
        gen = imaging.FilmImages()
        painter = mock.Mock(alive=False)
        backend = mock.Mock(name="backend")
        backend.worker.return_value = painter
        with mock.patch.object(gen, "backend", return_value=backend):
            self.assertTrue(gen.warm())
        painter.warm.assert_called_once()
        self.assertTrue(gen._warm)

    def test_warm_is_a_no_op_for_a_backend_without_a_resident_worker(self) -> None:
        gen = imaging.FilmImages()
        backend = mock.Mock(spec=["name"])
        backend.name = "placeholder"
        with mock.patch.object(gen, "backend", return_value=backend):
            self.assertFalse(gen.warm())
        self.assertFalse(gen._warm)


class PlanWarmsTests(unittest.TestCase):
    def _app(self):
        from fastapi import FastAPI

        from murmur.film_api import router as film_router

        app = FastAPI()
        app.include_router(film_router)
        return app

    def test_asking_for_the_plan_starts_the_engine(self) -> None:
        from fastapi.testclient import TestClient

        doc = {"id": "abc123", "title": "T", "units": ["One sentence here."],
               "pages": 1, "kind": "text", "toc": []}
        scene = mock.Mock(index=0, units=[0], text="One sentence here.")
        scene.public.return_value = {"index": 0, "text": "One sentence here."}

        with mock.patch("murmur.film_api.store.get_doc", return_value=doc), \
                mock.patch("murmur.film.plan_chapter", return_value=[scene]), \
                mock.patch("murmur.film.film_model", return_value=("m", 0, 0)), \
                mock.patch("murmur.film.still_final", return_value=False), \
                mock.patch("murmur.film.scene_image_path", return_value=Path("nope.png")), \
                mock.patch("murmur.film.painter.status", return_value={}), \
                mock.patch("murmur.film.scene_activity", return_value={}), \
                mock.patch("murmur.film.warm_engine", return_value=True) as warm:
            response = TestClient(self._app()).get("/api/film/plan", params={"doc": "abc123"})
        self.assertEqual(response.status_code, 200, response.text)
        warm.assert_called_once()

    def test_a_finished_chapter_does_not_wake_the_engine(self) -> None:
        from fastapi.testclient import TestClient

        doc = {"id": "abc123", "title": "T", "units": ["One sentence here."],
               "pages": 1, "kind": "text", "toc": []}
        scene = mock.Mock(index=0, units=[0], text="One sentence here.")
        scene.public.return_value = {"index": 0, "text": "One sentence here."}

        with mock.patch("murmur.film_api.store.get_doc", return_value=doc), \
                mock.patch("murmur.film.plan_chapter", return_value=[scene]), \
                mock.patch("murmur.film.film_model", return_value=("m", 0, 0)), \
                mock.patch("murmur.film.still_final", return_value=True), \
                mock.patch("murmur.film.scene_image_path", return_value=Path("done.png")), \
                mock.patch("murmur.film.painter.status", return_value={}), \
                mock.patch("murmur.film.scene_activity", return_value={}), \
                mock.patch("murmur.film.warm_engine", return_value=True) as warm:
            response = TestClient(self._app()).get("/api/film/plan", params={"doc": "abc123"})
        self.assertEqual(response.status_code, 200, response.text)
        warm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
