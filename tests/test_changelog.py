"""The README changelog copy must be generated, never hand-edited."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "changelog_render.py"


class ChangelogTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_the_readme_copy_is_current(self) -> None:
        result = self._run("--check")
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_a_version_section_can_be_rendered_for_a_release(self) -> None:
        result = self._run("--release", "0.2.0")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("0.2.0", result.stdout)
        self.assertIn("### Added", result.stdout)

    def test_an_unknown_version_is_an_error(self) -> None:
        result = self._run("--release", "9.9.9")
        self.assertNotEqual(0, result.returncode)

    def test_the_changelog_file_has_an_unreleased_section(self) -> None:
        text = (ROOT / "CHANGELOG.md").read_text("utf-8")
        self.assertIn("[Unreleased]", text)


if __name__ == "__main__":
    unittest.main()
