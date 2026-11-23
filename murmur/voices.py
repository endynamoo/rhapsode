"""Voice catalogue for Kokoro-82M v1.0.

Grades come from hexgrad/Kokoro-82M VOICES.md: an estimate of the quality and
quantity of the training data behind each voice (A best, F worst).
"""
from __future__ import annotations

# (voice_id, language, gender, grade)
_RAW: list[tuple[str, str, str, str]] = [
    # American English
    ("af_heart", "en-US", "F", "A"),
    ("af_bella", "en-US", "F", "A-"),
    ("af_nicole", "en-US", "F", "B-"),
    ("af_aoede", "en-US", "F", "C+"),
    ("af_kore", "en-US", "F", "C+"),
    ("af_sarah", "en-US", "F", "C+"),
    ("af_alloy", "en-US", "F", "C"),
    ("af_nova", "en-US", "F", "C"),
    ("af_sky", "en-US", "F", "C-"),
    ("af_jessica", "en-US", "F", "D"),
    ("af_river", "en-US", "F", "D"),
    ("am_fenrir", "en-US", "M", "C+"),
    ("am_michael", "en-US", "M", "C+"),
    ("am_puck", "en-US", "M", "C+"),
    ("am_echo", "en-US", "M", "D"),
    ("am_eric", "en-US", "M", "D"),
    ("am_liam", "en-US", "M", "D"),
    ("am_onyx", "en-US", "M", "D"),
    ("am_santa", "en-US", "M", "D-"),
    ("am_adam", "en-US", "M", "F+"),
    # British English
    ("bf_emma", "en-GB", "F", "B-"),
    ("bf_isabella", "en-GB", "F", "C"),
    ("bf_alice", "en-GB", "F", "D"),
    ("bf_lily", "en-GB", "F", "D"),
    ("bm_fable", "en-GB", "M", "C"),
    ("bm_george", "en-GB", "M", "C"),
    ("bm_lewis", "en-GB", "M", "D+"),
    ("bm_daniel", "en-GB", "M", "D"),
    # Japanese
    ("jf_alpha", "ja-JP", "F", "C+"),
    ("jf_gongitsune", "ja-JP", "F", "C"),
    ("jf_tebukuro", "ja-JP", "F", "C"),
    ("jf_nezumi", "ja-JP", "F", "C-"),
    ("jm_kumo", "ja-JP", "M", "C-"),
    # Mandarin
    ("zf_xiaoxiao", "zh-CN", "F", "D"),
    ("zf_xiaobei", "zh-CN", "F", "D"),
    ("zf_xiaoni", "zh-CN", "F", "D"),
    ("zf_xiaoyi", "zh-CN", "F", "D"),
    ("zm_yunxi", "zh-CN", "M", "D"),
    ("zm_yunjian", "zh-CN", "M", "D"),
    ("zm_yunxia", "zh-CN", "M", "D"),
    ("zm_yunyang", "zh-CN", "M", "D"),
    # Spanish
    ("ef_dora", "es-ES", "F", "—"),
    ("em_alex", "es-ES", "M", "—"),
    ("em_santa", "es-ES", "M", "—"),
    # French
    ("ff_siwis", "fr-FR", "F", "B-"),
    # Hindi
    ("hf_alpha", "hi-IN", "F", "C"),
    ("hf_beta", "hi-IN", "F", "C"),
    ("hm_omega", "hi-IN", "M", "C"),
    ("hm_psi", "hi-IN", "M", "C"),
    # Italian
    ("if_sara", "it-IT", "F", "C"),
    ("im_nicola", "it-IT", "M", "C"),
    # Brazilian Portuguese
    ("pf_dora", "pt-BR", "F", "—"),
    ("pm_alex", "pt-BR", "M", "—"),
    ("pm_santa", "pt-BR", "M", "—"),
]

LANG_LABEL = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "ja-JP": "Japanese",
    "zh-CN": "Chinese (Mandarin)",
    "es-ES": "Spanish",
    "fr-FR": "French",
    "hi-IN": "Hindi",
    "it-IT": "Italian",
    "pt-BR": "Portuguese (BR)",
}

FLAG = {
    "en-US": "🇺🇸",
    "en-GB": "🇬🇧",
    "ja-JP": "🇯🇵",
    "zh-CN": "🇨🇳",
    "es-ES": "🇪🇸",
    "fr-FR": "🇫🇷",
    "hi-IN": "🇮🇳",
    "it-IT": "🇮🇹",
    "pt-BR": "🇧🇷",
}

LANG_CODE = {
    "en-US": "en-us",
    "en-GB": "en-gb",
    "ja-JP": "ja",
    "zh-CN": "zh",
    "es-ES": "es",
    "fr-FR": "fr-fr",
    "hi-IN": "hi",
    "it-IT": "it",
    "pt-BR": "pt-br",
}

LANG_ORDER = ["en-US", "en-GB", "es-ES", "fr-FR", "it-IT", "pt-BR", "hi-IN", "ja-JP", "zh-CN"]

VOICES: dict[str, dict] = {
    vid: {
        "id": vid,
        "name": vid.split("_", 1)[1].replace("_", " ").title(),
        "lang": lang,
        "lang_label": LANG_LABEL[lang],
        "flag": FLAG[lang],
        "gender": gender,
        "grade": grade,
        "recommended": grade.startswith("A") or vid in ("bf_emma", "ff_siwis", "am_fenrir", "am_michael"),
    }
    for vid, lang, gender, grade in _RAW
}

DEFAULT_VOICE = "af_heart"


def lang_for_voice(voice: str) -> str:
    meta = VOICES.get(voice)
    return LANG_CODE[meta["lang"]] if meta else "en-us"


def base_language(voice: str) -> str:
    """ISO-639-1 code used for translation (en, es, fr, …)."""
    meta = VOICES.get(voice)
    return meta["lang"].split("-")[0].lower() if meta else "en"


def voice_groups() -> list[dict]:
    groups = []
    from . import langs

    for lang in LANG_ORDER:
        items = [v for v in VOICES.values() if v["lang"] == lang]
        if not items:
            continue
        items.sort(key=lambda v: (not v["recommended"], v["gender"], v["id"]))
        state = langs.support(lang)
        groups.append(
            {
                "lang": lang,
                "label": LANG_LABEL[lang],
                "flag": FLAG[lang],
                "voices": items,
                # A voice list is a promise: the picker greys these out rather than
                # letting somebody choose a voice that cannot speak.
                "available": state["available"],
                "reason": state["reason"],
            }
        )
    return groups
