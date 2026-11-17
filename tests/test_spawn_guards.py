"""Structure guards for the two seams that broke on Windows.

Neither of these is about behaviour a test can observe directly: a console
window flashing on Settings, and a temp file another process could not open.
Both are decided by *how* the code spawns and writes, so the guard reads the
source and keeps future changes on the sanctioned path.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "murmur"

SPAWN = re.compile(r"subprocess\.(?:run|Popen|call|check_call|check_output)\(")
DELETE_ON_CLOSE = re.compile(r"NamedTemporaryFile\([^)]*delete\s*=\s*True", re.S)


def _sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _offenders(pattern: re.Pattern[str], skip: set[str]) -> list[str]:
    found: list[str] = []
    for path in _sources():
        if path.name in skip:
            continue
        text = path.read_text("utf-8")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{path.relative_to(ROOT).as_posix()}:{line} {match.group(0)}")
    return found


class SpawnGuardTests(unittest.TestCase):
    def test_processes_are_spawned_through_proc(self) -> None:
        offenders = _offenders(SPAWN, skip={"proc.py"})
        self.assertEqual(
            [],
            offenders,
            "Call murmur.proc.run/popen instead: a bare subprocess call pops a "
            "console window in windowed builds.",
        )

    def test_no_temporary_file_is_deleted_while_open(self) -> None:
        offenders = _offenders(DELETE_ON_CLOSE, skip=set())
        self.assertEqual(
            [],
            offenders,
            "A delete-on-close temp file cannot be reopened on Windows; write the "
            "bytes to a closed path (see extract.extract_upload).",
        )


class DatabaseHandleTests(unittest.TestCase):
    def test_store_uses_the_closing_helper(self) -> None:
        text = (PACKAGE / "store.py").read_text("utf-8")
        self.assertNotIn(
            "with _conn() as conn:",
            text,
            "_conn() does not close; use _db() so Windows can release murmur.db.",
        )


if __name__ == "__main__":
    unittest.main()
