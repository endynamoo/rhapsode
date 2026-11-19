"""The licence folder must exist, be complete, and stay wired into the builds.

A compliance artefact that is only correct on the day it was written is not
compliance, so this test states the invariants rather than the contents:
the texts are present and canonical, both build paths copy them, the installer
shows the notice, and every copyleft component named in THIRD_PARTY.md has an
entry in SOURCES.md.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSES = ROOT / "licenses"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class LicenceFolderTests(unittest.TestCase):
    def test_the_folder_holds_every_text_it_offers(self):
        expected = {
            "GPL-3.0.txt": "GNU GENERAL PUBLIC LICENSE",
            "LGPL-2.1.txt": "GNU LESSER GENERAL PUBLIC LICENSE",
            "LGPL-3.0.txt": "GNU LESSER GENERAL PUBLIC LICENSE",
            "SOURCES.md": "copyleft",
            "NOTICE.txt": "MIT",
        }
        for name, marker in expected.items():
            path = LICENSES / name
            self.assertTrue(path.exists(), f"licenses/{name} is missing")
            self.assertIn(marker, read(path), f"licenses/{name} does not look canonical")

    def test_the_texts_are_the_versions_they_claim_to_be(self):
        self.assertIn("Version 3, 29 June 2007", read(LICENSES / "GPL-3.0.txt"))
        self.assertIn("17. Interpretation of Sections 15 and 16",
                      read(LICENSES / "GPL-3.0.txt"))
        self.assertIn("Version 2.1, February 1999", read(LICENSES / "LGPL-2.1.txt"))
        self.assertIn("0. Additional Definitions", read(LICENSES / "LGPL-3.0.txt"))

    def test_no_licence_file_carries_a_byte_order_mark_or_crlf(self):
        for path in LICENSES.iterdir():
            if not path.is_file():
                continue
            data = path.read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"), f"{path.name} has a BOM")
            self.assertNotIn(b"\r\n", data, f"{path.name} has CRLF line endings")

    def test_every_licence_file_is_valid_utf8(self):
        # A notice nobody can open is not a notice; the OpenCV one is 181 KB of
        # prose from a dozen projects, so it is the likely place for a stray byte.
        for path in LICENSES.iterdir():
            if path.is_file():
                path.read_bytes().decode("utf-8")

    def test_sources_names_every_copyleft_component_the_notice_lists(self):
        sources = read(LICENSES / "SOURCES.md")
        for component in ("phonemizer", "eSpeak NG", "libsndfile", "libheif", "FFmpeg"):
            self.assertIn(component, sources, f"SOURCES.md does not cover {component}")

    def test_sources_carries_a_written_offer(self):
        sources = read(LICENSES / "SOURCES.md").lower()
        self.assertIn("three years", sources)
        self.assertIn("issue", sources)


class BuildWiringTests(unittest.TestCase):
    def test_the_windows_installer_ships_the_folder_and_shows_the_notice(self):
        iss = read(ROOT / "packaging" / "Rhapsode-setup.iss")
        self.assertIn("LicenseFile=..\\licenses\\NOTICE.txt", iss)
        self.assertRegex(iss, r'Source: "\.\.\\licenses\\\*"; DestDir: "\{app\}\\licenses"')

    def test_the_macos_bundle_copies_the_folder(self):
        build = read(ROOT / "build_app.sh")
        self.assertRegex(build, r"cp -Rf licenses dist/Rhapsode\.app/Contents/Resources/")

    def test_readme_and_notice_point_at_each_other(self):
        self.assertIn("licenses/", read(ROOT / "README.md"))
        self.assertIn("THIRD_PARTY.md", read(LICENSES / "NOTICE.txt"))
        self.assertIn("licenses/", read(ROOT / "THIRD_PARTY.md"))

    def test_the_source_tarball_script_does_not_exclude_the_folder(self):
        script = read(ROOT / "packaging" / "make_source_tar.ps1")
        excluded = re.search(r"/XD([^\n|]*)", script)
        self.assertIsNotNone(excluded, "the robocopy exclusion list disappeared")
        self.assertNotIn("licenses", excluded.group(1))
