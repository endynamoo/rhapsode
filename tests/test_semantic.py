"""The optional embedding half: tokenizer, availability and honest degradation.

No test here downloads anything or loads onnxruntime, so the suite stays fast
and offline on CI (onnxruntime is not in requirements-test.txt). The tokenizer
is exercised against a tiny hand-written vocabulary, which is the part most
likely to be wrong: it is a reimplementation of BertNormalizer + WordPiece.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import config, semantic

VOCAB = {
    "[PAD]": 0,
    "[UNK]": 100,
    "[CLS]": 101,
    "[SEP]": 102,
    "un": 103,
    "##believ": 104,
    "##able": 105,
    "cat": 106,
    "##s": 107,
    "the": 108,
    "cafe": 109,
    ",": 110,
}


def write_tokenizer(folder: Path, vocab: dict[str, int] | None = None) -> Path:
    payload = {
        "truncation": {"max_length": 128},
        "model": {
            "type": "WordPiece",
            "unk_token": "[UNK]",
            "continuing_subword_prefix": "##",
            "max_input_chars_per_word": 100,
            "vocab": vocab or VOCAB,
        },
    }
    path = folder / "tokenizer.json"
    path.write_text(json.dumps(payload), "utf-8")
    return path


class TokenizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="rhapsode-tokenizer-"))
        self.tokenizer = semantic.Tokenizer(write_tokenizer(self.folder), max_length=32)

    def tearDown(self) -> None:
        for path in self.folder.glob("*"):
            path.unlink()
        self.folder.rmdir()

    def test_wordpiece_splits_and_rejoins_a_word(self) -> None:
        self.assertEqual(
            [101, 103, 104, 105, 106, 107, 102],
            self.tokenizer.encode("Unbelievable cats"),
        )

    def test_lowercases_and_strips_accents(self) -> None:
        self.assertEqual([101, 109, 102], self.tokenizer.encode("Café"))

    def test_punctuation_is_its_own_token(self) -> None:
        self.assertEqual([101, 106, 110, 106, 102], self.tokenizer.encode("cat, cat"))

    def test_an_unknown_word_becomes_the_unk_token(self) -> None:
        self.assertIn(100, self.tokenizer.encode("zzzz"))

    def test_long_text_is_truncated_to_the_model_limit(self) -> None:
        ids = self.tokenizer.encode("cat " * 200)
        self.assertLessEqual(len(ids), 32)
        self.assertEqual(101, ids[0])
        self.assertEqual(102, ids[-1])

    def test_a_batch_is_padded_and_masked(self) -> None:
        ids, mask, segments = self.tokenizer.encode_batch(["cat", "the cat cats"])
        width = ids.shape[1]
        self.assertEqual((2, width), ids.shape)
        # "the cat cats" is the longer row: [CLS] the cat cat ##s [SEP]
        self.assertEqual(6, width)
        self.assertEqual([1, 1, 1, 1, 1, 1], mask[1].tolist())
        self.assertEqual([1, 1, 1, 0, 0, 0], mask[0].tolist())
        self.assertEqual([0, 0, 0], ids[0][3:].tolist())
        self.assertFalse(segments.any())


class AvailabilityTests(unittest.TestCase):
    def _home(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory()

    def test_without_files_semantic_search_is_unavailable_and_says_missing(self) -> None:
        with self._home() as folder:
            base = Path(folder)
            with mock.patch.object(config, "HOME", base), mock.patch.object(
                config, "MODELS_DIR", base / "models"
            ):
                self.assertFalse(semantic.available())
                status = semantic.status()
                self.assertFalse(status["available"])
                self.assertEqual("missing", status["state"])
                self.assertEqual("all-MiniLM-L6-v2", status["model"])
                self.assertEqual("Apache-2.0", status["license"])
                self.assertEqual(0, status["bytes"])
                with self.assertRaises(RuntimeError):
                    semantic.embed(["anything"])

    def test_switching_it_off_wins_over_present_files(self) -> None:
        with self._home() as folder:
            base = Path(folder)
            target = base / "models" / "minilm-l6-v2"
            target.mkdir(parents=True)
            for name in ("model.onnx", "tokenizer.json"):
                (target / name).write_bytes(b"x")
            with mock.patch.object(config, "HOME", base), mock.patch.object(
                config, "MODELS_DIR", base / "models"
            ), mock.patch.dict(os.environ, {"MURMUR_SEMANTIC": "off"}):
                self.assertFalse(semantic.available())
                status = semantic.status()
                self.assertEqual("off", status["state"])
                self.assertFalse(status["enabled"])
                with self.assertRaises(RuntimeError):
                    semantic.start_download()

    def test_present_files_are_reported_as_ready_with_their_size(self) -> None:
        with self._home() as folder:
            base = Path(folder)
            target = base / "models" / "minilm-l6-v2"
            target.mkdir(parents=True)
            (target / "model.onnx").write_bytes(b"x" * 1024)
            (target / "tokenizer.json").write_bytes(b"x" * 32)
            with mock.patch.object(config, "HOME", base), mock.patch.object(
                config, "MODELS_DIR", base / "models"
            ), mock.patch.dict(os.environ, {"MURMUR_SEMANTIC": "on"}):
                self.assertTrue(semantic.available())
                status = semantic.status()
                self.assertTrue(status["available"])
                self.assertEqual("ready", status["state"])
                self.assertEqual(1056, status["bytes"])

    def test_a_partial_download_is_not_mistaken_for_a_model(self) -> None:
        with self._home() as folder:
            base = Path(folder)
            target = base / "models" / "minilm-l6-v2"
            target.mkdir(parents=True)
            (target / "model.onnx").write_bytes(b"x" * 1024)
            (target / "tokenizer.json.part").write_bytes(b"x" * 4)
            with mock.patch.object(config, "HOME", base), mock.patch.object(
                config, "MODELS_DIR", base / "models"
            ), mock.patch.dict(os.environ, {"MURMUR_SEMANTIC": "on"}):
                self.assertFalse(semantic.available())
                self.assertEqual("missing", semantic.status()["state"])


class QuantityTests(unittest.TestCase):
    def test_embedding_nothing_returns_an_empty_matrix(self) -> None:
        matrix = semantic.embed([])
        self.assertEqual((0, semantic.DIM), matrix.shape)


if __name__ == "__main__":
    unittest.main()
