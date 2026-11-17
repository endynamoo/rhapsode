"""The one-shot fallback must build the same job the worker got.

Every hand-repair bug so far lived in this seam: the job carried a mask or a
strength that the fallback never forwarded, so a failed painter silently produced
an unmasked repaint.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import imaging


class RunOnceCommandTests(unittest.TestCase):
    def _command(self, job: dict) -> list[str]:
        child = mock.MagicMock()
        child.returncode = 1
        child.communicate.return_value = ("", "stop")
        backend = imaging.FluxPythonBackend()
        with mock.patch.object(imaging.proc, "spawn", return_value=child) as spawn:
            with self.assertRaises(imaging.ImageError):
                backend._run_once(Path("fluxgen.py"), job)
        return list(spawn.call_args.args[0])

    def test_mask_and_strength_are_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "still.png")
            command = self._command({
                "prompt": "a hand",
                "out": out,
                "width": 120,
                "height": 160,
                "seed": 3,
                "steps": 2,
                "reference": out,
                "mask": out + ".mask.png",
                "strength": 0.75,
            })
        self.assertIn("--mask", command)
        self.assertEqual(out + ".mask.png", command[command.index("--mask") + 1])
        self.assertIn("--strength", command)
        self.assertEqual("0.75", command[command.index("--strength") + 1])
        self.assertEqual("2", command[command.index("--steps") + 1])

    def test_plain_job_has_no_mask_or_strength(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = self._command({
                "prompt": "a lighthouse",
                "out": str(Path(tmp) / "still.png"),
                "width": 640,
                "height": 360,
                "seed": 1,
                "steps": 2,
                "reference": None,
                "mask": None,
                "strength": None,
            })
        self.assertNotIn("--mask", command)
        self.assertNotIn("--strength", command)


class WorkerSingletonTests(unittest.TestCase):
    def test_backends_share_one_painter(self) -> None:
        imaging._SHARED_WORKER = None
        first = imaging.FluxPythonBackend().worker()
        second = imaging.FluxPythonBackend().worker()
        self.assertIs(first, second)
        imaging._SHARED_WORKER = None


if __name__ == "__main__":
    unittest.main()
