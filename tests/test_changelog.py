"""The changelog stays current, and the README copy of it does too."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "changelog_render.py"


def load_module():
    spec = importlib.util.spec_from_file_location("changelog_render", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChangelogTests(unittest.TestCase):
    def test_changelog_has_a_dated_section(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Changelog"))
        self.assertRegex(text, r"(?m)^##\s+\[?(Unreleased|\d)")

    def test_the_unreleased_section_has_its_heading(self):
        # A usable changelog is read by a stranger: the heading can be lost by a
        # careless replacement without any entry disappearing, which is exactly
        # what happened here.
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        headings = [line for line in text.splitlines() if line.startswith("## [Unreleased]")]
        self.assertEqual(headings, ["## [Unreleased]"], text[:600])

    def test_each_section_has_one_heading_per_kind(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for section in text.split("\n## [")[1:]:
            title = section.splitlines()[0]
            kinds = [
                line.strip()
                for line in section.splitlines()
                if line.startswith("### ")
            ]
            duplicates = {kind for kind in kinds if kinds.count(kind) > 1}
            self.assertFalse(duplicates, f"{title}: duplicated {sorted(duplicates)}")

    def test_readme_block_matches_the_changelog(self):
        ok, message = load_module().check()
        self.assertTrue(ok, message)

    def test_readme_block_is_truncated(self):
        block = load_module().render_block()
        self.assertLessEqual(len(block), 2600)
        self.assertIn("CHANGELOG.md", block)
        self.assertNotRegex(block, r"(?m)^## ", "the block sits under the README's own heading")

    def test_release_notes_carry_the_whole_section(self):
        module = load_module()
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        released = [
            title
            for title, _ in module.raw_sections(text)
            if "unreleased" not in title.lower()
        ]
        self.assertTrue(released, "CHANGELOG.md has no released version yet")
        version = released[0].strip().lstrip("[").split("]")[0].strip()
        notes = module.release_notes(version, text)
        self.assertGreater(len(notes), 40)
        self.assertRegex(notes, r"(?m)^- ")
        self.assertNotRegex(notes, r"(?m)^## ")



    def test_crlf_readme_keeps_its_line_endings(self):
        module = load_module()
        updated = module.apply_block("Intro\r\n\r\n", module.render_block())
        self.assertEqual(updated.count("\n"), updated.count("\r\n"))
        self.assertTrue(updated.endswith("\r\n"))


if __name__ == "__main__":
    unittest.main()
