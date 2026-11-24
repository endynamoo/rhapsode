"""What a voice is asked to say: translate only when it needs translating (#41)."""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest import mock

from murmur import speak, store, translate

# One plain sentence per voice language, and the voice that speaks it.
SAMPLES = {
    "en": ("The lantern keeper walked down to the shore and waited.", "af_heart"),
    "es": ("El farero caminaba por la orilla y esperaba la marea.", "ef_dora"),
    "fr": ("Le gardien du phare marchait sur la rive et attendait la mer.", "ff_siwis"),
    "it": ("Il guardiano del faro camminava sulla riva e aspettava il mare.", "if_sara"),
    "pt": ("O faroleiro caminhava pela praia e esperava a maré.", "pf_dora"),
    "hi": ("समुद्र के किनारे एक प्रकाशस्तंभ था।", "hf_alpha"),
    "ja": ("灯台守は浜辺を歩いていました。", "jf_alpha"),
    "zh": ("灯塔看守人沿着海岸走着。", "zf_xiaobei"),
}


@contextmanager
def translation_cache():
    """An in-memory stand-in for the translations table.

    speak.prepare only needs get/put; keeping sqlite out of these tests keeps
    them about the decision, and leaves no locked murmur.db behind on Windows.
    """
    rows: dict[str, str] = {}

    def get(key: str) -> str | None:
        return rows.get(key)

    def put(key: str, source: str, target: str, original: str, translated: str) -> None:
        rows[key] = translated

    with mock.patch.multiple(store, translation_get=get, translation_put=put):
        yield rows


class NoBackend:
    """A translator chain with nothing behind it, as on a fresh Windows install."""

    def available(self) -> bool:
        return False

    def backend(self, target: str | None = None) -> str | None:
        return None

    def translate(self, text, target, source="auto"):  # pragma: no cover - must not run
        raise AssertionError("nothing should be translated without a backend")


class RecordingBackend:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[tuple] = []

    def available(self) -> bool:
        return True

    def backend(self, target: str | None = None) -> str:
        return "recording"

    def translate(self, text: str, target: str, source: str = "auto") -> dict:
        self.calls.append((text, target, source))
        return {"text": self.output, "source": source if source != "auto" else "en", "translated": True}


class SameLanguageTests(unittest.TestCase):
    def test_every_voice_language_reads_its_own_text_without_a_warning(self) -> None:
        with translation_cache():
            with mock.patch.object(translate, "translator", NoBackend()):
                for language, (text, voice) in SAMPLES.items():
                    with self.subTest(language=language):
                        prepared = speak.prepare(text, voice)
                        self.assertEqual(language, prepared["target"])
                        self.assertIsNone(prepared["error"])
                        self.assertFalse(prepared["translated"])
                        self.assertEqual(text, prepared["text"])

    def test_english_book_with_the_default_voice_asks_for_nothing(self) -> None:
        # The bug in #41: this produced the "translation is not set up" toast.
        with translation_cache():
            with mock.patch.object(translate, "translator", NoBackend()):
                prepared = speak.prepare("The lantern keeper walked down to the shore.", "af_heart")
        self.assertIsNone(prepared["error"])
        self.assertEqual("en", prepared["source"])

    def test_unknown_text_without_a_backend_stays_quiet(self) -> None:
        with translation_cache():
            with mock.patch.object(translate, "translator", NoBackend()):
                prepared = speak.prepare("Yes.", "ef_dora")
        self.assertIsNone(prepared["error"], "a one-word sentence is not evidence of a mismatch")


class MismatchTests(unittest.TestCase):
    def test_foreign_text_without_a_backend_says_why(self) -> None:
        with translation_cache():
            with mock.patch.object(translate, "translator", NoBackend()):
                prepared = speak.prepare("The lantern keeper walked down to the shore.", "ef_dora")
        self.assertEqual("en", prepared["source"])
        self.assertEqual("es", prepared["target"])
        self.assertTrue(prepared["error"])
        self.assertIn("translation", prepared["error"].lower())
        self.assertFalse(prepared["translated"])

    def test_every_language_is_recognised_as_foreign_by_every_other_voice(self) -> None:
        with translation_cache():
            with mock.patch.object(translate, "translator", NoBackend()):
                for language, (text, voice) in SAMPLES.items():
                    for other, (_, other_voice) in SAMPLES.items():
                        if other == language:
                            continue
                        with self.subTest(text=language, voice=other):
                            prepared = speak.prepare(text, other_voice)
                            self.assertEqual(language, prepared["source"])
                            self.assertTrue(
                                prepared["error"],
                                f"{language} text read by a {other} voice should warn",
                            )

    def test_translation_uses_the_detected_source(self) -> None:
        backend = RecordingBackend("El farero caminaba por la orilla.")
        sentence = "The lantern keeper walked down to the shore."
        with translation_cache():
            with mock.patch.object(translate, "translator", backend):
                prepared = speak.prepare(sentence, "ef_dora")
                self.assertTrue(prepared["translated"])
                self.assertEqual("El farero caminaba por la orilla.", prepared["text"])
                self.assertEqual(1, len(backend.calls))
                _, target, source = backend.calls[0]
                self.assertEqual("es", target)
                self.assertEqual("en", source)

                # Second read comes from the translation cache, not the model.
                speak.prepare(sentence, "ef_dora")
                self.assertEqual(1, len(backend.calls))


class SwitchTests(unittest.TestCase):
    def test_turning_translation_off_keeps_the_original(self) -> None:
        with translation_cache():
            with mock.patch.object(translate, "translator", NoBackend()):
                prepared = speak.prepare("The lantern keeper waited.", "ef_dora", enabled=False)
        self.assertFalse(prepared["translated"])
        self.assertIsNone(prepared["error"])


class CacheKeyTests(unittest.TestCase):
    def test_the_key_carries_the_backend_and_the_target(self) -> None:
        # Switching backend (or the bundled model arriving after a server was used) must
        # not serve the old engine's sentence from the cache.
        same = speak._cache_key("local", "fr", "hello")
        self.assertEqual(same, speak._cache_key("local", "fr", "hello"))
        self.assertNotEqual(same, speak._cache_key("http", "fr", "hello"))
        self.assertNotEqual(same, speak._cache_key("local", "es", "hello"))
        self.assertNotEqual(same, speak._cache_key("local", "fr", "hello there"))


if __name__ == "__main__":
    unittest.main()
