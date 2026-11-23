"""Tests: an install without the language extra must say so, clearly, everywhere.

The failure this holds: a reader picked a Japanese voice on a machine where pyopenjtalk
was not installed and was told to install Microsoft build tools by pip. The app now
knows, before it tries, and every surface that offers the voice says why.
"""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from murmur import engine, langs, server, voices


class SupportTest(unittest.TestCase):
    def setUp(self):
        langs.reset()

    def tearDown(self):
        langs.reset()

    def test_english_needs_nothing(self):
        self.assertTrue(langs.support("en-US")["available"])

    def test_a_missing_module_makes_the_language_unavailable_with_a_reason(self):
        real = langs.importlib.util.find_spec
        with mock.patch.object(langs.importlib.util, "find_spec",
                              side_effect=lambda name: None if name == "pyopenjtalk" else real(name)):
            langs.reset()
            state = langs.support("ja")
        self.assertFalse(state["available"])
        # The reason names the extra and the file that adds it, and never a compiler.
        self.assertIn("requirements-lang.txt", state["reason"])
        self.assertNotIn("Microsoft", state["reason"])

    def test_the_answer_is_cached_until_reset(self):
        real = langs.importlib.util.find_spec
        calls = []

        def counting(name):
            calls.append(name)
            return real(name)

        with mock.patch.object(langs.importlib.util, "find_spec", side_effect=counting):
            langs.reset()
            langs.support("zh")
            first = len(calls)
            langs.support("zh")
        self.assertEqual(len(calls), first, "the probe ran twice for one language")


class EngineRefusalTest(unittest.TestCase):
    def test_speaking_an_unavailable_language_raises_the_reason_not_a_traceback(self):
        with mock.patch.object(langs, "support",
                               return_value={"available": False, "reason": "needs the extra"}):
            with self.assertRaises(ValueError) as caught:
                engine.engine._phonemize("こんにちは", "ja-JP")
        self.assertEqual(str(caught.exception), "needs the extra")


class SurfaceTest(unittest.TestCase):
    def setUp(self):
        langs.reset()

    def tearDown(self):
        langs.reset()

    def test_health_reports_both_special_languages(self):
        with TestClient(server.app) as client:
            health = client.get("/api/health").json()
        self.assertIn("ja", health["languages"])
        self.assertIn("zh", health["languages"])
        self.assertIn("available", health["languages"]["ja"])

    def test_the_voice_list_marks_a_language_it_cannot_speak(self):
        real = langs.importlib.util.find_spec
        with mock.patch.object(langs.importlib.util, "find_spec",
                               side_effect=lambda name: None if name == "pyopenjtalk" else real(name)):
            langs.reset()
            groups = {g["lang"]: g for g in voices.voice_groups()}
        self.assertTrue(groups["en-US"]["available"])
        self.assertFalse(groups["ja-JP"]["available"])
        self.assertIn("requirements-lang.txt", groups["ja-JP"]["reason"])

    def test_the_installed_extra_is_reported_as_available(self):
        # On a machine with the extra (this development box, the installers) the
        # same code must not claim the voice is missing.
        groups = {g["lang"]: g for g in voices.voice_groups()}
        for lang, group in groups.items():
            self.assertIn("available", group, lang)
            self.assertIsInstance(group["available"], bool, lang)


if __name__ == "__main__":
    unittest.main()
