"""Render the changelog: README copy, release notes, and a staleness check.

`CHANGELOG.md` is the running file. The README carries a truncated copy between the
CHANGELOG markers, and a GitHub release carries one version's section in full.

    python scripts/changelog_render.py                   # rewrite the README block
    python scripts/changelog_render.py --check           # exit 1 when the README is stale
    python scripts/changelog_render.py --release 0.2.0   # print one section
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
README = ROOT / "README.md"

BEGIN = "<!-- CHANGELOG:BEGIN -->"
END = "<!-- CHANGELOG:END -->"
LIMIT = 1500


def sections(text: str) -> list[tuple[str, str]]:
    """(version heading, body) pairs, newest first."""
    parts = re.split(r"(?m)^## ", text)
    out: list[tuple[str, str]] = []
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        out.append((heading.strip(), body.rstrip()))
    return out


def entry_count(body: str) -> int:
    """Bullets, counting a wrapped bullet once (its continuation lines are indented)."""
    return len(re.findall(r"(?m)^[-*] ", body))


def groups(body: str) -> list[str]:
    """Split a version section into whole Added / Changed / Fixed groups."""
    parts = re.split(r"(?m)^### ", body.strip())
    return ["### " + part.strip() for part in parts if part.strip()]


def render_readme_block(text: str) -> tuple[str, int]:
    """The newest whole groups that fit the budget, and how many entries were left out.

    Whole groups only: cutting in the middle leaves a "### Fixed" heading with nothing
    under it, which reads like a mistake rather than a truncation.
    """
    budget = LIMIT
    total = sum(entry_count(body) for _heading, body in sections(text))
    shown = 0
    blocks: list[str] = []
    for heading, body in sections(text):
        if budget <= 0:
            break
        rendered = [f"### {heading}"]
        used = len(rendered[0]) + 2
        for group in groups(body):
            if used + len(group) + 2 > budget:
                break
            rendered.append(group)
            used += len(group) + 2
            shown += entry_count(group)
        if len(rendered) > 1:
            blocks.append("\n\n".join(rendered))
            budget -= used
    block = "\n\n".join(blocks).rstrip()
    remaining = max(0, total - shown)
    if remaining:
        plural = "entry" if remaining == 1 else "entries"
        block += (
            f"\n\n*…and {remaining} more {plural} in "
            "[CHANGELOG.md](CHANGELOG.md).*"
        )
    return block, remaining


def readme_with_block(block: str) -> str:
    text = README.read_text("utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"README is missing {BEGIN} / {END}")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n\n{block}\n\n{END}{tail}"


def main(argv: list[str]) -> int:
    if not CHANGELOG.exists():
        print("CHANGELOG.md is missing", file=sys.stderr)
        return 1
    text = CHANGELOG.read_text("utf-8")

    if "--release" in argv:
        wanted = argv[argv.index("--release") + 1].lstrip("v")
        for heading, body in sections(text):
            if wanted in heading:
                print(f"## {heading}\n\n{body}".strip())
                return 0
        print(f"no section for {wanted}", file=sys.stderr)
        return 1

    block, remaining = render_readme_block(text)
    updated = readme_with_block(block)

    if "--check" in argv:
        if README.read_text("utf-8") != updated:
            print(
                "README changelog is stale: run python scripts/changelog_render.py",
                file=sys.stderr,
            )
            return 1
        print("README changelog is current")
        return 0

    README.write_text(updated, "utf-8")
    print(f"README changelog rendered ({remaining} entries left out)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
