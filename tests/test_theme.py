"""Theme, font, and chapter-HTML helpers."""
from __future__ import annotations

import unittest

from murmur import extract, fonts, library_api


class FontListTests(unittest.TestCase):
    def test_clean_dedupes_and_filters_icon_fonts(self) -> None:
        cleaned = fonts._clean(
            ["Arial", "arial", "Apple Braille", "Noto Color Emoji", "Georgia", "  "]
        )
        self.assertEqual(cleaned, ["Arial", "Georgia"])

    def test_clean_sorts_case_insensitively(self) -> None:
        cleaned = fonts._clean(["Zapfino", "arial", "Georgia"])
        self.assertEqual(cleaned, ["arial", "Georgia", "Zapfino"])


class ChapterStyleTests(unittest.TestCase):
    def test_hex_colors_pass_and_bad_values_fall_back(self) -> None:
        self.assertEqual(library_api._color("#A1b2C3", "#000000"), "#A1b2C3")
        self.assertEqual(library_api._color("red", "#000000"), "#000000")
        self.assertEqual(library_api._color("", "#000000"), "#000000")
        self.assertEqual(library_api._color("url(evil)", "#000000"), "#000000")

    def test_font_stack_accepts_named_fonts(self) -> None:
        stack = library_api._font_stack("Georgia")
        self.assertIn('"Georgia"', stack)
        self.assertIn("sans-serif", stack)

    def test_font_stack_strips_css_injection(self) -> None:
        stack = library_api._font_stack("Bad;}body{display:none")
        self.assertNotIn("}", stack)
        self.assertNotIn(";", stack)
        self.assertIn("sans-serif", stack)

    def test_sans_and_serif_keywords_keep_their_stacks(self) -> None:
        self.assertEqual(library_api._font_stack("sans"), library_api.CHAPTER_FONTS["sans"])
        self.assertEqual(library_api._font_stack("serif"), library_api.CHAPTER_FONTS["serif"])


class UrlGuardTests(unittest.TestCase):
    def test_private_hosts_are_not_public(self) -> None:
        for host in ("127.0.0.1", "10.1.2.3", "192.168.0.9", "::1"):
            with self.subTest(host=host):
                self.assertFalse(extract._is_public_host(host))


if __name__ == "__main__":
    unittest.main()
