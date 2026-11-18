"""Decide what the selected voice should actually say.

Kokoro voices are language-specific, so a Spanish voice fed English text sounds
like English read with Spanish phonemes. When translation is on, sentences are
translated into the voice's language first.
"""
from __future__ import annotations

import hashlib

from . import store, translate, voices


def _cache_key(target: str, text: str) -> str:
    return hashlib.sha1(f"v1|{target}|{text}".encode("utf-8")).hexdigest()


def prepare(text: str, voice: str, enabled: bool = True) -> dict:
    result = {
        "text": text,
        "translated": False,
        "source": "",
        "target": voices.base_language(voice),
        "error": None,
    }
    if not enabled or not result["target"]:
        return result

    key = _cache_key(result["target"], text)
    cached = store.translation_get(key)
    if cached is not None:
        result["text"] = cached
        result["translated"] = cached.strip() != text.strip()
        return result

    if not translate.translator.available():
        # Nothing to translate with: read the original text and say why, in words
        # that fit this platform (the macOS cause is a missing helper or macOS < 26).
        result["error"] = translate.missing_advice()
        return result

    try:
        output = translate.translator.translate(text, result["target"])
    except (translate.TranslateUnavailable, translate.TranslateError) as exc:
        result["error"] = str(exc)
        return result

    result["source"] = output.get("source", "")
    if output.get("translated") and output.get("text"):
        store.translation_put(key, result["source"], result["target"], text, output["text"])
        result["text"] = output["text"]
        result["translated"] = True
    return result
