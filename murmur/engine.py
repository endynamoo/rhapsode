"""Kokoro-82M inference with a disk cache.

Voice styles, text, and model version determine the cache key. Speed is applied
in the browser (pitch-preserved), so changing speed never re-synthesizes.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from . import cache, config, langs, text as textmod
from .backends import tts as tts_backends
from .voices import DEFAULT_VOICE, VOICES, lang_for_voice

# The clip sidecars are read once per sentence in a chapter; keep the hot ones in RAM.
_CLIP_META = cache.LRUCache(maxsize=1024, name="clips")


def missing_model_advice() -> str:
    """How to get the weights back, in terms the reader can act on.

    A packaged install has no interpreter to run, so pointing at `python -m
    murmur.bootstrap` there is advice a stranger cannot follow - found by the
    fresh-install harness on its first run.
    """
    if config.FROZEN:
        return ("Open Settings and press Download it again in the Voice model row.")
    return "Run `python -m murmur.bootstrap` to download it again."


class TTSEngine:
    def __init__(self) -> None:
        self._model = None
        self._load_lock = threading.Lock()
        self._compute = threading.Semaphore(2)
        self._model_version = 0.0
        self.last_error: str | None = None
        self._g2p: dict[str, object] = {}
        self._g2p_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            tts_backends.configure()
            # Check the weights before importing the engine that loads them: a fresh install
            # has no model, and the advice for that must not depend on the TTS package being
            # installed (CI runs the suite without kokoro-onnx).
            problem = config.voice_model_problem()
            if problem:
                raise FileNotFoundError(
                    f"The voice model is incomplete: {problem}. "
                    + missing_model_advice()
                )
            from kokoro_onnx import Kokoro

            self._model = Kokoro(str(config.MODEL_PATH), str(config.VOICES_PATH))
            self._model_version = config.MODEL_PATH.stat().st_mtime
            self.last_error = None
            # Warm the graph so the first real sentence is not slow.
            try:
                self._model.create("Hello.", voice=DEFAULT_VOICE, speed=1.0, lang="en-us")
            except Exception:  # pragma: no cover - warmup is best effort
                pass

    def unload(self) -> None:
        """Drop the voice model.

        The process exits a moment later anyway, but a quit that is asked for - the
        Quit button, the window closing - should give the 340 MB and the ONNX arenas
        back before it returns, not leave them to the interpreter's exit path.
        """
        with self._load_lock:
            self._model = None
            self._g2p.clear()
        gc.collect()

    # -- synthesis ---------------------------------------------------------
    def _key(self, text: str, voice: str) -> str:
        raw = f"kokoro-v1.0|g2p2|{self._model_version}|{voice}|{text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:26]

    @staticmethod
    def _groups(timings) -> list[tuple[float, float]]:
        groups: list[list[float]] = []
        current: list[float] | None = None
        for t in timings:
            if not t.phoneme.strip():
                if current:
                    groups.append(current)
                    current = None
                continue
            if current is None:
                current = [float(t.start), float(t.end)]
            else:
                current[1] = float(t.end)
        if current:
            groups.append(current)
        return [(g[0], g[1]) for g in groups]

    def _phonemize(self, text: str, lang: str) -> str | None:
        """Kokoro's bundled espeak cannot handle Japanese or Chinese; use misaki."""
        base = lang.split("-")[0]
        if base not in {"ja", "zh"}:
            return None

        state = langs.support(base)
        if not state["available"]:
            # A ValueError is a 400 with this text: the reader is told what is missing
            # and where to get it, instead of a compiler traceback from pyopenjtalk.
            raise ValueError(state["reason"])
        with self._g2p_lock:
            g2p = self._g2p.get(base)
            if g2p is None:
                if base == "ja":
                    from misaki.ja import JAG2P

                    g2p = JAG2P(version="pyopenjtalk")
                else:
                    from misaki.zh import ZHG2P

                    g2p = ZHG2P()
                self._g2p[base] = g2p
            output = g2p(text)  # type: ignore[operator]

        phonemes = output[0] if isinstance(output, tuple) else output
        phonemes = str(phonemes).strip()
        return phonemes or None

    @staticmethod
    def _temp_paths(wav_path: Path, meta_path: Path) -> tuple[Path, Path]:
        """Sibling temp names that still END in `.wav` / `.json`.

        Unique per call, because playback and an export can ask for the same sentence at the
        same time. The extension has to survive: soundfile picks the container from it, and a
        name ending in `.tmp` fails with "No format specified" - which is what every
        synthesis answered when the first version of this used `.wav.<stamp>.tmp`.
        """
        stamp = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        return (
            wav_path.with_name(f"{wav_path.stem}.{stamp}.tmp.wav"),
            meta_path.with_name(f"{meta_path.stem}.{stamp}.tmp.json"),
        )

    def synthesize(self, text: str, voice: str = DEFAULT_VOICE) -> dict:
        """Return {path, duration, sample_rate, words, key}, synthesizing if needed."""
        text = text.strip()
        if not text:
            raise ValueError("No text to speak.")
        if voice not in VOICES:
            voice = DEFAULT_VOICE

        self.load()
        key = self._key(text, voice)
        wav_path = config.CACHE_DIR / f"{key}.wav"
        meta_path = config.CACHE_DIR / f"{key}.json"

        cached = _CLIP_META.get(key)
        if cached is not None and cached.get("text") == text:
            if wav_path.exists():
                os.utime(wav_path, None)
                return dict(cached)

        if wav_path.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
                meta["path"] = str(wav_path)
                meta["key"] = key
                if meta.get("text") == text:
                    _CLIP_META.put(key, dict(meta))
                    os.utime(wav_path, None)
                    return meta
            except (json.JSONDecodeError, OSError):
                pass

        lang = lang_for_voice(voice)
        payload = text
        is_phonemes = False
        try:
            phonemes = self._phonemize(text, lang)
            if phonemes:
                payload = phonemes
                is_phonemes = True
        except ValueError:
            # Missing language support (or nothing to speak) carries advice for the reader;
            # the HTTP layer maps ValueError to 400 with that text. Wrapping it turned a
            # clear "install the language extra" into a 500.
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not prepare {lang} phonemes: {exc}") from exc

        with self._compute:
            audio, sample_rate, timings = self._model.create_timed(  # type: ignore[union-attr]
                payload,
                voice=voice,
                speed=1.0,
                lang=lang,
                is_phonemes=is_phonemes,
                trim=True,
                sentence_pause=0.16,
                clause_pause=0.05,
            )

        if audio is None or len(audio) == 0:
            raise RuntimeError("The voice model returned no audio.")

        import soundfile as sf

        duration = float(len(audio)) / float(sample_rate)
        words = textmod.align_words(text, self._groups(timings), duration)

        meta = {
            "key": key,
            "text": text,
            "voice": voice,
            "duration": round(duration, 3),
            "sample_rate": int(sample_rate),
            "words": words,
            "path": str(wav_path),
        }

        # Unique per call. Playback and an export can ask for the same sentence at the
        # same time; with a temp name derived only from the cache key, the second writer
        # replaced a file the first had already moved, or published a half-written WAV.
        tmp_wav, tmp_meta = self._temp_paths(wav_path, meta_path)
        sf.write(tmp_wav, np.asarray(audio, dtype=np.float32), sample_rate, subtype="PCM_16")
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
        try:
            os.replace(tmp_wav, wav_path)
            os.replace(tmp_meta, meta_path)
        finally:
            tmp_wav.unlink(missing_ok=True)
            tmp_meta.unlink(missing_ok=True)
        _CLIP_META.put(key, dict(meta, path=str(wav_path), key=key))
        return meta

    def warm_async(self) -> None:
        def _run() -> None:
            try:
                self.load()
            except Exception as exc:  # pragma: no cover
                self.last_error = str(exc)

        threading.Thread(target=_run, name="kokoro-warmup", daemon=True).start()

    def recheck(self) -> None:
        """Re-arm the load once the weights land on disk.

        A first run has no weights, so the warmup fails and the reason is kept
        for `/api/health`. `python -m murmur.bootstrap` can then finish while the
        app is still open — without this, that cached reason outlived the cause
        and the pill sat on "Loading voice model…" until a restart.
        """
        if self._model is not None or self.last_error is None:
            return
        if not config.voice_model_ready():
            return
        self.last_error = None
        self.warm_async()


def prune_cache(limit: int | None = None) -> int:
    """Drop least-recently-used clips until the cache fits the budget."""
    limit = limit if limit is not None else config.CACHE_LIMIT
    files = []
    total = 0
    for path in config.CACHE_DIR.glob("*.wav"):
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((stat.st_mtime, stat.st_size, path))
        total += stat.st_size
    if total <= limit:
        return 0
    removed = 0
    for _, size, path in sorted(files):
        if total <= limit:
            break
        try:
            path.unlink()
            path.with_suffix(".json").unlink(missing_ok=True)
            total -= size
            removed += 1
        except OSError:
            pass
    return removed


def clip_cache_stats() -> dict:
    """Hit rate of the in-memory clip-metadata cache."""
    return _CLIP_META.stats()


def cache_stats() -> dict:
    files = list(config.CACHE_DIR.glob("*.wav"))
    size = sum(f.stat().st_size for f in files if f.exists())
    return {"clips": len(files), "bytes": size}


engine = TTSEngine()
