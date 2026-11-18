"""The painter is started isolated, so a source install can paint (issue: #40 follow-up)."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from murmur import imaging


class PainterCommandTests(unittest.TestCase):
    def test_the_command_is_isolated(self) -> None:
        command = imaging._painter_command(Path("py"), Path("fluxgen.py"), "--serve")
        self.assertEqual(["-I"], command[1:2], "the painter must run isolated")
        self.assertIn("--serve", command)

    def test_both_spawn_sites_use_it(self) -> None:
        # A regression here would be silent: the resident worker is the normal
        # path and the one-shot run is the fallback, so both must be isolated.
        source = Path(imaging.__file__).read_text("utf-8")
        self.assertEqual(
            2, source.count("_painter_command(self.python"),
            "the resident worker and the one-shot painter must both call it",
        )


class OneShotPainterTests(unittest.TestCase):
    def test_the_fallback_painter_is_isolated(self) -> None:
        backend = imaging.FluxPythonBackend()
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "still.png"
            seen: dict = {}

            class Child:
                returncode = 0

                def communicate(self, timeout=None):
                    return ("", "")

                def kill(self):
                    pass

            def fake_spawn(command, **kwargs):
                seen["command"] = command
                out.write_bytes(b"x")
                return Child()

            job = {
                "prompt": "a harbour at dusk",
                "width": 832, "height": 480, "seed": 1, "steps": 2,
                "out": str(out),
            }
            with mock.patch.object(imaging.proc, "spawn", side_effect=fake_spawn):
                backend._run_once(Path("fluxgen.py"), job)

        self.assertEqual("-I", seen["command"][1])


class ShadowingRegressionTest(unittest.TestCase):
    """The reason -I matters: a sibling profile.py shadows the stdlib module."""

    def test_isolated_interpreter_ignores_a_sibling_profile_module(self) -> None:
        import subprocess
        import sys

        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "profile.py").write_text("BROKEN = True\n", "utf-8")
            script = directory / "child.py"
            script.write_text(
                "import profile\n"
                "print(getattr(profile, 'BROKEN', 'stdlib'))\n",
                "utf-8",
            )
            shadowed = subprocess.run(
                [sys.executable, str(script)], capture_output=True, text=True,
            )
            isolated = subprocess.run(
                [sys.executable, "-I", str(script)], capture_output=True, text=True,
            )
        self.assertEqual("True", shadowed.stdout.strip(), "plain run sees the sibling")
        self.assertEqual("stdlib", isolated.stdout.strip(), "-I must not")


if __name__ == "__main__":
    unittest.main()
