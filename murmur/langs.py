"""Which voices this installation can actually speak.

Kokoro's bundled eSpeak covers seven of the nine languages. Japanese and Chinese go
through misaki instead, and Japanese needs pyopenjtalk - a package published only as
source, so installing it needs a C++ toolchain. That made "install Rhapsode from source,
pick a Japanese voice" fail with a compiler error nobody documented.

So the extra is optional, and this module is the single answer to "can this build do it
and, if not, why" - used by the engine before phonemising, by `/api/health`, and by the
voice list so the picker can say so instead of failing at the first sentence.
"""
from __future__ import annotations

import importlib.util
import threading

#: Languages Kokoro's own tokenizer cannot phonemise.
SPECIAL = ("ja", "zh")

#: What each one needs, in the order it is checked.
_NEEDS = {
    "ja": ("misaki", "pyopenjtalk"),
    "zh": ("misaki",),
}

#: How to get it, said the way a reader can act on it.
_HOW = {
    "ja": (
        "The Japanese voices need `pyopenjtalk`, which is an optional extra: it is "
        "included in the installers, but a source install must add `requirements-lang.txt` "
        "(it compiles, so Windows needs the C++ build tools and macOS needs Xcode)."
    ),
    "zh": (
        "The Chinese voices need `misaki`, which is an optional extra: it is included in "
        "the installers; a source install must add `requirements-lang.txt`."
    ),
}

_LOCK = threading.Lock()
_CACHE: dict[str, dict] = {}


def support(lang: str) -> dict:
    """``{"available": bool, "reason": str}`` for a base language code.

    A language this build always handles answers available with no reason, so callers do
    not have to special-case English.
    """
    base = (lang or "").split("-")[0].lower()
    if base not in SPECIAL:
        return {"available": True, "reason": ""}
    with _LOCK:
        cached = _CACHE.get(base)
        if cached is not None:
            return dict(cached)
        missing = [name for name in _NEEDS[base]
                   if importlib.util.find_spec(name) is None]
        result = {"available": not missing, "reason": "" if not missing else _HOW[base]}
        _CACHE[base] = result
        return dict(result)


def reset() -> None:
    """Forget the answers (tests that install or hide a dependency)."""
    with _LOCK:
        _CACHE.clear()
