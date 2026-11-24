"""Synthesis temp names: unique per call, and still a container soundfile can write.

The first version of this fix made the temp name end in `.tmp`, and soundfile infers the
format from the extension: every synthesis answered 500 with "No format specified". The
fresh-install gate caught it on the built artefact; this keeps it caught here.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from murmur import engine

CACHE_WAV = Path("cache") / "abc.wav"
CACHE_META = Path("cache") / "abc.json"


class TempPathTests(unittest.TestCase):
    def test_the_temp_names_keep_a_usable_extension(self) -> None:
        tmp_wav, tmp_meta = engine.TTSEngine._temp_paths(CACHE_WAV, CACHE_META)
        self.assertEqual(".wav", tmp_wav.suffix)
        self.assertEqual(".json", tmp_meta.suffix)
        self.assertNotEqual(CACHE_WAV, tmp_wav)
        self.assertNotEqual(CACHE_META, tmp_meta)
        self.assertEqual("cache", str(tmp_wav.parent))

    def test_two_calls_never_share_a_name(self) -> None:
        first = engine.TTSEngine._temp_paths(CACHE_WAV, CACHE_META)
        second = engine.TTSEngine._temp_paths(CACHE_WAV, CACHE_META)
        self.assertNotEqual(first, second)

    def test_soundfile_writes_the_temp_name(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tmp_wav, _ = engine.TTSEngine._temp_paths(root / "k.wav", root / "k.json")
            sf.write(tmp_wav, np.zeros(64, dtype="float32"), 22050, subtype="PCM_16")
            self.assertTrue(tmp_wav.exists())
            self.assertGreater(tmp_wav.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
