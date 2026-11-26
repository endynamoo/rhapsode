"""The stills engine setup: when it is offered, and what it reports.

Nothing here runs the real script - it builds an interpreter and downloads several GB.
These tests are about the decision (a GPU machine without a venv is offered one; a CPU
machine is not) and about the refusal that leaves a reader with a sentence instead of a
silent nothing.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from murmur import config, enginesetup


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        enginesetup._state.update(running=False, done=False, error=None, lines=[])

    def test_a_machine_with_no_gpu_does_not_need_it(self) -> None:
        with mock.patch.object(config, "PROFILE", {"profile": "cpu"}), \
                mock.patch.object(enginesetup, "script_path", return_value=Path("setup.ps1")), \
                mock.patch.object(enginesetup, "installed", return_value=False):
            self.assertFalse(enginesetup.needed())

    def test_a_gpu_machine_without_a_venv_needs_it(self) -> None:
        # The offer is Windows-only (macOS paints through mflux, Linux has no script), so a
        # Linux CI run must still see the Windows decision this test is about.
        with mock.patch.object(enginesetup.sys, "platform", "win32"), \
                mock.patch.object(config, "PROFILE", {"profile": "balanced"}), \
                mock.patch.object(enginesetup, "script_path", return_value=Path("setup.ps1")), \
                mock.patch.object(enginesetup, "installed", return_value=False):
            self.assertTrue(enginesetup.needed())

    def test_an_installed_engine_is_not_offered_again(self) -> None:
        with mock.patch.object(config, "PROFILE", {"profile": "balanced"}), \
                mock.patch.object(enginesetup, "script_path", return_value=Path("setup.ps1")), \
                mock.patch.object(enginesetup, "installed", return_value=True):
            self.assertFalse(enginesetup.needed())

    def test_linux_says_why_there_is_no_setup_script(self) -> None:
        with mock.patch.object(enginesetup.sys, "platform", "linux"):
            self.assertIsNone(enginesetup.script_path())
            status = enginesetup.status()
        self.assertFalse(status["available"])
        self.assertIn("Linux", status["reason"])
        self.assertIn("docs/STILLS.md", status["reason"])
        self.assertFalse(status["needed"], "Linux is never offered a card it cannot honour")

    def test_a_mac_is_never_offered_a_setup_it_does_not_need(self) -> None:
        # Apple Silicon paints through mflux; the .app does not ship scripts/ and never should.
        with mock.patch.object(enginesetup.sys, "platform", "darwin"), \
                mock.patch.object(config, "PROFILE", {"profile": "balanced"}), \
                mock.patch.object(enginesetup, "installed", return_value=False):
            self.assertFalse(enginesetup.needed())
            self.assertIn("mflux", enginesetup.no_script_reason())

    def test_the_command_runs_the_script_with_powershell(self) -> None:
        script = Path("C:/apps/Rhapsode/scripts/setup_engine.ps1")
        with mock.patch.object(enginesetup, "script_path", return_value=script):
            command = enginesetup.command()
        self.assertEqual("powershell.exe", command[0])
        self.assertIn("-ExecutionPolicy", command)
        self.assertIn(str(script), command)

    def test_the_script_is_looked_for_beside_the_executable(self) -> None:
        # In a frozen build the package lives in _internal while scripts/ sits next to
        # Rhapsode.exe. Looking beside config.APP_ROOT found nothing, and the 2026-11-25
        # fresh install offered a card that could only answer "the script is missing".
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / "scripts" / "setup_engine.ps1"
            script.parent.mkdir(parents=True)
            script.write_text("Write-Host hi", encoding="ascii")
            with mock.patch.object(config, "INSTALL_ROOT", root), \
                    mock.patch.object(enginesetup.sys, "platform", "win32"):
                self.assertEqual(script, enginesetup.script_path())


    def test_the_text_encoder_is_reported_separately(self) -> None:
        # The setup fetches it now; an install from before that must not look complete.
        with mock.patch.object(enginesetup, "text_encoder_bytes", return_value=7 * 1024 ** 3):
            self.assertTrue(enginesetup.text_encoder_ready())
        with mock.patch.object(enginesetup, "text_encoder_bytes", return_value=4 * 1024 ** 3):
            self.assertFalse(enginesetup.text_encoder_ready(), "one shard is not enough")

    def test_status_carries_the_text_encoder_state(self) -> None:
        with mock.patch.object(enginesetup, "text_encoder_bytes", return_value=0):
            status = enginesetup.status()
        self.assertEqual({"ready": False, "bytes": 0}, status["textEncoder"])


class StartTests(unittest.TestCase):
    def setUp(self) -> None:
        enginesetup._state.update(running=False, done=False, error=None, lines=[])

    def test_starting_when_installed_is_a_no_op(self) -> None:
        with mock.patch.object(enginesetup, "installed", return_value=True), \
                mock.patch.object(enginesetup, "script_path", return_value=Path("setup.ps1")):
            status = enginesetup.start()
        self.assertTrue(status["installed"])
        self.assertFalse(status["run"]["running"])
        self.assertIsNone(status["run"]["error"])

    def test_starting_without_a_script_reports_the_reason(self) -> None:
        with mock.patch.object(enginesetup, "installed", return_value=False), \
                mock.patch.object(enginesetup, "script_path", return_value=None):
            status = enginesetup.start()
        self.assertFalse(status["run"]["running"])
        self.assertTrue(status["run"]["error"])
        self.assertIn("Linux" if enginesetup.sys.platform.startswith("linux") else "script",
                      status["run"]["error"])


if __name__ == "__main__":
    unittest.main()
