"""The stills polish pass: gating, idempotency, and never losing art."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import detectors, polish


def _noise(path: Path, size: tuple[int, int] = (300, 300)) -> bytes:
    """A real PNG big enough to pass the still-size threshold."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(1)
    array = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    Image.fromarray(array).save(path, format="PNG")
    return path.read_bytes()


class FakeRun:
    def __init__(self, size: tuple[int, int] = (300, 300)) -> None:
        self.size = size
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""

    def __call__(self, command, **kwargs):
        out = Path(command[command.index("--out") + 1])
        _noise(out, self.size)
        return self


def _still(folder: Path) -> Path:
    path = folder / "still.png"
    _noise(path, (120, 160))
    return path


class PolishGateTests(unittest.TestCase):
    def test_disabled_setting_does_no_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            polish.store, "get_settings", return_value={"polishStills": False}
        ), mock.patch.object(polish.detectors, "inspect") as inspect:
            result = polish.apply(_still(Path(tmp)))
            self.assertEqual("disabled", result["action"])
            inspect.assert_not_called()

    def test_still_without_people_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            polish.store, "get_settings", return_value={"polishStills": True}
        ), mock.patch.object(polish.detectors, "available", return_value=True), mock.patch.object(
            polish.detectors, "inspect", return_value={"faces": [], "people": [], "error": None}
        ), mock.patch.object(polish.proc, "run") as run:
            path = _still(Path(tmp))
            result = polish.apply(path, "a lighthouse")
            self.assertEqual("skipped", result["action"])
            self.assertEqual("no people", result["reason"])
            run.assert_not_called()
            self.assertTrue(polish.sidecar(path).exists())

    def test_detector_failure_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            polish.store, "get_settings", return_value={"polishStills": True}
        ), mock.patch.object(polish.detectors, "available", return_value=True), mock.patch.object(
            polish.detectors, "inspect", return_value={"faces": [], "people": [], "error": "offline"}
        ):
            result = polish.apply(_still(Path(tmp)))
            self.assertEqual("failed", result["action"])
            self.assertIn("offline", result["error"])


class PolishRunTests(unittest.TestCase):
    def _patches(self):
        return (
            mock.patch.object(polish.store, "get_settings", return_value={"polishStills": True}),
            mock.patch.object(polish.detectors, "available", return_value=True),
            mock.patch.object(
                polish.detectors, "inspect",
                return_value={"faces": [(10, 10, 60, 60)], "people": [(0, 0, 200, 300)], "error": None},
            ),
            mock.patch.object(polish.config, "IMAGE_PYTHON", Path(__file__)),
        )

    def test_refine_replaces_the_still_and_records_a_sidecar(self) -> None:
        settings, available, inspect, python = self._patches()
        with tempfile.TemporaryDirectory() as tmp, settings, available, inspect, python, mock.patch.object(
            polish.proc, "run", FakeRun()
        ):
            path = _still(Path(tmp))
            before = path.read_bytes()
            result = polish.apply(path, "a seated man")

            self.assertEqual("refined", result["action"])
            self.assertNotEqual(before, path.read_bytes())
            self.assertTrue(polish.backup(path).exists(), "the pre-polish still is kept")
            record = polish._read_record(path)
            self.assertEqual(polish.POLISH_VERSION, record["version"])
            self.assertEqual(1, record["faces_before"])
            self.assertIn("strength", record)

    def test_second_pass_on_an_unchanged_still_is_cached(self) -> None:
        settings, available, inspect, python = self._patches()
        with tempfile.TemporaryDirectory() as tmp, settings, available, inspect, python, mock.patch.object(
            polish.proc, "run", side_effect=FakeRun()
        ) as run:
            path = _still(Path(tmp))
            polish.apply(path, "a seated man")
            again = polish.apply(path, "a seated man")
            self.assertEqual("cached", again["action"])
            self.assertEqual(1, run.call_count)

    def test_failed_render_keeps_the_original(self) -> None:
        settings, available, inspect, python = self._patches()
        run = mock.MagicMock()
        run.return_value = mock.MagicMock(returncode=1, stderr="boom", stdout="")
        with tempfile.TemporaryDirectory() as tmp, settings, available, inspect, python, mock.patch.object(
            polish.proc, "run", run
        ):
            path = _still(Path(tmp))
            before = path.read_bytes()
            result = polish.apply(path, "a seated man")
            self.assertEqual("failed", result["action"])
            self.assertEqual(before, path.read_bytes())


class DetectorShapeTests(unittest.TestCase):
    def test_models_are_named_and_downloadable(self) -> None:
        for name in (detectors.FACES_MODEL, detectors.PERSON_MODEL, detectors.POSE_MODEL):
            self.assertIn(name, detectors.MODEL_URLS)
            self.assertTrue(detectors.MODEL_URLS[name].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
