"""Language detection: enough to know when not to translate (issue #41)."""
from __future__ import annotations

import unittest

from murmur import language


class DetectTests(unittest.TestCase):
    def test_english(self) -> None:
        self.assertEqual(
            "en", language.detect("The lantern keeper walked down to the shore and waited.")
        )

    def test_spanish(self) -> None:
        self.assertEqual(
            "es",
            language.detect("El farero caminaba por la orilla y esperaba la marea."),
        )

    def test_french(self) -> None:
        self.assertEqual(
            "fr",
            language.detect("Le gardien du phare marchait sur la rive et attendait la mer."),
        )

    def test_italian(self) -> None:
        self.assertEqual(
            "it",
            language.detect("Il guardiano del faro camminava sulla riva e aspettava il mare."),
        )

    def test_portuguese(self) -> None:
        self.assertEqual(
            "pt",
            language.detect("O faroleiro caminhava pela praia e esperava a maré."),
        )

    def test_hindi_by_script(self) -> None:
        self.assertEqual("hi", language.detect("समुद्र के किनारे एक प्रकाशस्तंभ था।"))

    def test_japanese_by_script(self) -> None:
        self.assertEqual("ja", language.detect("灯台守は浜辺を歩いていました。"))

    def test_chinese_by_script(self) -> None:
        self.assertEqual("zh", language.detect("灯塔看守人沿着海岸走着。"))

    def test_japanese_kanji_only_sentence_is_chinese(self) -> None:
        # No kana: nothing separates it from Chinese, and saying "zh" is more
        # useful than saying nothing at all.
        self.assertEqual("zh", language.detect("東京都庁舎"))

    def test_diacritics_are_decisive(self) -> None:
        self.assertEqual("es", language.detect("¿Dónde está el niño?"))
        self.assertEqual("pt", language.detect("Não, a mãe está aqui."))

    def test_short_or_ambiguous_text_is_unknown(self) -> None:
        for text in ("Yes.", "Hello", "", "   ", "42", "Rhapsode"):
            with self.subTest(text=text):
                self.assertIsNone(language.detect(text))

    def test_matches_only_when_confident(self) -> None:
        self.assertTrue(language.matches("The keeper waited by the sea.", "en"))
        self.assertFalse(language.matches("The keeper waited by the sea.", "fr"))
        self.assertFalse(language.matches("Yes.", "en"))

    def test_names(self) -> None:
        self.assertEqual("Mandarin Chinese", language.describe("zh"))
        self.assertEqual("an unknown language", language.describe(None))


if __name__ == "__main__":
    unittest.main()
