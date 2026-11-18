"""Decide what the selected voice should actually say.

Kokoro voices are language-specific, so a Spanish voice fed English text sounds
like English read with Spanish phonemes. When translation is on, sentences are
translated into the voice's language first.
"""
from __future__ import annotations

import hashlib

from . import language, store, translate, voices


def _cache_key(target: str, text: str) -> str:
    return hashlib.sha1(f"v1|{target}|{text}".encode("utf-8")).hexdigest()


def prepare(text: str, voice: str, enabled: bool = True) -> dict:
    """What this voice should say, and whether anything stopped it.

    Translation is needed only when the text is in another language than the
    voice. Reading a language identifier off the sentence first is what keeps an
    English book read by an English voice from asking for a translation - and so
    from reporting that no translation backend exists (issue #41).
    """
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

    source = language.detect(text)
    result["source"] = source or ""
    if source == result["target"]:
        # Already the voice's language: nothing to translate and nothing to say.
        return result

    if not translate.translator.available():
        # Nothing to translate with. Only complain when the sentence is
        # confidently in *another* language: an unknown answer ("Yes.") is not
        # evidence that a translation was wanted.
        if source:
            result["error"] = translate.missing_advice()
        return result

    try:
        output = translate.translator.translate(text, result["target"], source or "auto")
    except (translate.TranslateUnavailable, translate.TranslateError) as exc:
        result["error"] = str(exc)
        return result

    result["source"] = output.get("source", "") or result["source"]
    if output.get("translated") and output.get("text"):
        store.translation_put(key, result["source"], result["target"], text, output["text"])
        result["text"] = output["text"]
        result["translated"] = True
    return result
