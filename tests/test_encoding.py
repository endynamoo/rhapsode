"""Every text file this repository ships must be plain UTF-8 with no leftovers.

Twice now a file has been through a cp1252 round-trip and shipped mojibake to the front
page: `README.md` after the #96 merge (every em dash became `a€™`), and `murmur/imaging.py`
plus the installer script before it. Mojibake is invisible to the test suite and to a
diff reviewer reading fast, so it is checked mechanically here.

The signature is a run of non-ASCII characters that decodes as UTF-8 when its characters
are encoded as cp1252 - which is exactly what double encoding produces, and what correct
text cannot produce (a real em dash is 0x97 in cp1252, not a valid UTF-8 byte).
"""
from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

TEXT_SUFFIXES = {".md", ".py", ".js", ".mjs", ".css", ".html", ".json", ".jsonc", ".yml",
                 ".yaml", ".ps1", ".sh", ".txt", ".iss", ".spec", ".cfg", ".toml", ".svg"}
SKIP_DIRS = {".git", ".venv", "dist", "build", "build_assets", "node_modules",
             "__pycache__", "cache"}
BOM = b"\xef\xbb\xbf"
REPLACEMENT = "\ufffd"


def text_files() -> list[pathlib.Path]:
    found = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        found.append(path)
    return found


def mojibake_runs(text: str) -> list[str]:
    """Runs that look like UTF-8 text read as cp1252."""
    bad = []
    index = 0
    while index < len(text):
        if ord(text[index]) <= 127:
            index += 1
            continue
        end = index
        while end < len(text) and ord(text[end]) > 127:
            end += 1
        run = text[index:end]
        try:
            if run.encode("cp1252").decode("utf-8") != run:
                bad.append(run)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass  # correct text: a genuine em dash is not valid UTF-8 once encoded
        index = end
    return bad


class EncodingTest(unittest.TestCase):
    def test_every_text_file_is_utf8_without_a_bom(self) -> None:
        problems = []
        for path in text_files():
            raw = path.read_bytes()
            relative = path.relative_to(ROOT)
            if raw.startswith(BOM):
                problems.append(f"{relative}: starts with a UTF-8 BOM")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                problems.append(f"{relative}: not UTF-8 ({exc})")
                continue
            if REPLACEMENT in text:
                problems.append(f"{relative}: contains U+FFFD (a lost character)")
            runs = mojibake_runs(text)
            if runs:
                sample = ", ".join(repr(run) for run in runs[:3])
                problems.append(f"{relative}: {len(runs)} mojibake run(s): {sample}")
        self.assertEqual([], problems, "\n".join(problems))

    def test_the_checker_recognises_double_encoding(self) -> None:
        # A guard that cannot fail is worse than none: the exact string that shipped.
        broken = "Rhapsode \u00e2\u20ac\u201d turn anything you can read"
        self.assertEqual(["\u00e2\u20ac\u201d"], mojibake_runs(broken))
        self.assertEqual([], mojibake_runs("Rhapsode \u2014 turn anything you can read"))
        self.assertEqual([], mojibake_runs("0.75\u20133\u00d7 \u00b7 9 languages"))


if __name__ == "__main__":
    unittest.main()
