#!/usr/bin/env python3
"""Mirror the newest CHANGELOG.md entries into a short block in README.md.

CHANGELOG.md is the full history and the file that gets edited. This script
keeps a truncated copy of the newest entries at the bottom of README.md, between
the markers below, so the repo's front page says what changed lately without
anyone scrolling a thousand lines. tests/test_changelog.py runs it with
--check, so a changelog that was edited without refreshing the README fails CI.

    python scripts/changelog_render.py                  rewrite the README block
    python scripts/changelog_render.py --check          exit 1 if the block is stale
    python scripts/changelog_render.py --release 0.2.0-alpha  print that version's full
                                                       section, for release notes:
        python scripts/changelog_render.py --release 0.2.0-alpha > notes.md
        gh release create v0.2.0-alpha --notes-file notes.md
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
README = ROOT / "README.md"

BEGIN = "<!-- CHANGELOG:BEGIN -->"
END = "<!-- CHANGELOG:END -->"

BUDGET = 1500       # characters of entries after which the block is cut
MAX_SECTIONS = 4    # at most this many version headings reach the README
MIN_BULLETS = 6     # always show this many entries when the changelog has them
MAX_BLOCK = 2600    # hard ceiling for the generated block, markers included

SECTION = re.compile(r"^##\s+(?P<title>.+?)\s*$")
SUBSECTION = re.compile(r"^###\s+(?P<title>.+?)\s*$")
BULLET = re.compile(r"^(?:[-*+]|\d+\.)\s+(?P<text>.+)$")


def read_text(path: Path) -> str:
    """Read a file without newline translation, so CRLF survives a rewrite."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def script_name() -> str:
    """Path of this script relative to the repo root, for the README pointer."""
    try:
        return Path(__file__).resolve().relative_to(ROOT).as_posix()
    except ValueError:  # pragma: no cover - only if the tree was copied oddly
        return Path(__file__).name


def parse(text: str) -> list[tuple[str, list[tuple[str | None, str]]]]:
    """Return [(section title, [(subsection title or None, bullet text)])].

    Wrapped bullet lines are joined back into one entry so the README copy stays
    one line per change. Sections without bullets (intros, notes) are dropped.
    """
    sections: list[tuple[str, list[tuple[str | None, str]]]] = []
    subsection: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        match = SECTION.match(line)
        if match:
            sections.append((match.group("title"), []))
            subsection = None
            continue
        if not sections or not sections[-1][1] and not line:
            continue
        if not sections:
            continue
        match = SUBSECTION.match(line)
        if match:
            subsection = match.group("title")
            continue
        match = BULLET.match(line.lstrip())
        if match:
            sections[-1][1].append((subsection, match.group("text").strip()))
        elif line.startswith("  ") and sections[-1][1]:
            # continuation of the previous bullet
            sub, previous = sections[-1][1][-1]
            sections[-1][1][-1] = (sub, (previous + " " + line.strip()).strip())
    return [(title, items) for title, items in sections if items]


def render_block(changelog_text: str | None = None) -> str:
    """Return the marked-up, truncated changelog block for README.md."""
    if changelog_text is None:
        changelog_text = read_text(CHANGELOG)
    sections = parse(changelog_text)
    total = sum(len(items) for _, items in sections)

    chosen: list[tuple[str, list[tuple[str | None, str]]]] = []
    used = 0
    shown = 0
    for title, items in sections[:MAX_SECTIONS]:
        picked: list[tuple[str | None, str]] = []
        for item in items:
            if shown >= MIN_BULLETS and used + len(item[1]) + 8 > BUDGET:
                break
            if used + len(item[1]) + 8 > BUDGET and picked:
                break
            picked.append(item)
            used += len(item[1]) + 8
            shown += 1
        if picked:
            chosen.append((title, picked))
        if shown >= MIN_BULLETS and used >= BUDGET:
            break

    lines = [BEGIN, "## Changelog", ""]
    lines.append(
        "Newest first. The full history is in [CHANGELOG.md](CHANGELOG.md); this "
        "block is generated from it by `%s`, so edit the changelog, not this copy."
        % script_name()
    )
    lines.append("")
    for title, items in chosen:
        lines.append("### " + title.replace("[", "").replace("]", ""))
        lines.append("")
        current = object()
        for subsection, bullet in items:
            if subsection != current:
                current = subsection
                if subsection:
                    if lines[-1] != "":
                        lines.append("")
                    lines.append("**%s**" % subsection)
                    lines.append("")
            lines.append("- " + bullet)
        lines.append("")

    remaining = total - shown
    if remaining > 0:
        lines.append(
            "*...and %d more %s in [CHANGELOG.md](CHANGELOG.md).*"
            % (remaining, "entry" if remaining == 1 else "entries")
        )
        lines.append("")
    lines.append(END)
    block = "\n".join(lines).rstrip("\n") + "\n"
    if len(block) > MAX_BLOCK:  # pragma: no cover - guards a malformed changelog
        raise SystemExit(
            "changelog block would be %d characters (limit %d); shorten the top "
            "entries in CHANGELOG.md" % (len(block), MAX_BLOCK)
        )
    return block


def apply_block(readme_text: str, block: str) -> str:
    """Return readme_text with the generated block in place, markers included."""
    newline = "\r\n" if "\r\n" in readme_text else "\n"
    text = readme_text.replace("\r\n", "\n")
    block_lf = block.replace("\r\n", "\n")
    start = text.find(BEGIN)
    end = text.find(END, start + len(BEGIN)) if start >= 0 else -1
    if start < 0 and end < 0:
        text = text.rstrip("\n") + "\n\n" + block_lf
    elif start < 0 or end < 0:
        raise SystemExit(
            "README.md has only one of %s / %s; restore both markers" % (BEGIN, END)
        )
    else:
        text = text[:start] + block_lf.rstrip("\n") + text[end + len(END):]
    if newline != "\n":
        text = text.replace("\n", newline)
    return text


def raw_sections(text: str) -> list[tuple[str, list[str]]]:
    """Return [(section title, original lines)] split on the `## ` headings."""
    sections: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        match = SECTION.match(raw)
        if match:
            sections.append((match.group("title"), []))
        elif sections:
            sections[-1][1].append(raw)
    return sections


def release_notes(version: str, changelog_text: str | None = None) -> str:
    """Return the full, unabridged section for one version, as release notes.

    The README block is the truncated view; a GitHub release carries the whole
    section, so `gh release create <tag> --notes-file <(this)` never ships a
    shortened or hand-written description.
    """
    if changelog_text is None:
        changelog_text = read_text(CHANGELOG)
    wanted = version.strip().lstrip("[vV").split("]")[0].strip().lower()
    for title, lines in raw_sections(changelog_text):
        candidate = title.strip().lstrip("[").split("]")[0].strip().lstrip("vV").lower()
        if candidate == wanted:
            body = "\n".join(lines).strip("\n")
            return body.strip("\n") + "\n"
    raise SystemExit("no changelog section for version %r" % version)


def check() -> tuple[bool, str]:
    """Return (is_current, message) for the README copy of the changelog."""
    if not CHANGELOG.exists():
        return False, "CHANGELOG.md is missing"
    if not README.exists():
        return False, "README.md is missing"
    expected = apply_block(read_text(README), render_block())
    actual = read_text(README)
    if expected.replace("\r\n", "\n") != actual.replace("\r\n", "\n"):
        return False, (
            "the Changelog block in README.md is stale; run `python %s`"
            % script_name()
        )
    return True, "the Changelog block in README.md is current"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 rather than rewriting when the README block is stale",
    )
    parser.add_argument(
        "--release",
        metavar="VERSION",
        help="print that version's full changelog section, for release notes",
    )
    args = parser.parse_args(argv)

    if args.release:
        sys.stdout.write(release_notes(args.release))
        return 0

    if args.check:
        ok, message = check()
        print(message)
        return 0 if ok else 1

    text = read_text(README)
    updated = apply_block(text, render_block())
    if updated == text:
        print("README.md already current")
        return 0
    README.write_text(updated, encoding="utf-8", newline="")
    print("README.md Changelog block refreshed from CHANGELOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
