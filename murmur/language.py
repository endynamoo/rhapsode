"""Which language is this text in? Enough to know when *not* to translate.

Kokoro voices are language-specific, so the reader translates a sentence into
the voice's language before speaking it. Doing that to text that is *already* in
that language is waste - and on a machine with no translation backend it turned
into a warning on every English book read by an English voice (issue #41).

This module answers one narrow question: does this sentence look like language
X? It is not a general language identifier and does not try to be.

- Script ranges settle Japanese, Chinese and Hindi outright: kana can only be
  Japanese, Han without kana is treated as Chinese, Devanagari is Hindi.
- The Latin-script languages the voice catalogue offers (en, es, fr, it, pt)
  are separated by function words, which are short, frequent and hard to
  confuse with content ("the/and/of" against "el/la/de" against "le/les/des").
  A handful of diacritics add a point where they are decisive.

It is deliberately allowed to say "no idea". That is what keeps a one-word
sentence or a proper noun from being confidently waved through: the caller can
treat an unknown answer differently from a known mismatch.
"""
from __future__ import annotations

import re
import unicodedata

# Languages the voice catalogue speaks, base codes. Kept here rather than
# imported from voices so this module stays dependency-free and testable alone.
LANGUAGES = ("en", "es", "fr", "hi", "it", "ja", "pt", "zh")

NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "hi": "Hindi",
    "it": "Italian",
    "ja": "Japanese",
    "pt": "Portuguese",
    "zh": "Mandarin Chinese",
}

# Function words, chosen for how much they say about the language and how little
# they overlap with the other four. Collisions ("de", "que", "como", "la") are
# fine: they score for both sides, and the runner-up margin decides.
_WORDS = {
    "en": frozenset(
        """the and of to is in that it was he she you with for not this but they
        have from at as his her were on are be by an or we which their has had
        will would there been if when what all can more no than then them these
        those into over after out about your its our my""".split()
    ),
    "es": frozenset(
        """el la los las de del que y en un una unos unas por con para no se su
        sus al es son como más pero este esta estos estas cuando muy sin sobre
        entre hasta desde también porque lo le les está están era fue hay nada
        dijo ella yo todo""".split()
    ),
    "fr": frozenset(
        """le les des de du un une est que qui dans pour pas sur avec ce cette
        ces sont au aux ne plus par mais comme leur leurs nous vous ils elles
        être avoir fait tout tous toute aussi chez sans sous entre était ont
        son sa ses et la on y donc puis lorsque où là très déjà peut alors
        ainsi dont vers même encore il elle je tu oui madame monsieur dit rien
        avait""".split()
    ),
    "it": frozenset(
        """il lo la gli le di del della dei delle che per con non si sono come
        più anche nel nella alla alle dagli dagli questo questa questi quando
        molto senza tra fra perché essere era stato hanno suo sua suoi è un una
        al alla lui lei io disse nulla signore dove sulla dopo prima quale""".split()
    ),
    "pt": frozenset(
        """o os as de do da dos das em um uma uns umas para com não na no nas nos
        por mais como ao aos é são seu sua seus mas quando muito sem sobre entre
        até desde também porque você eles elas está estão era foi há pelo pela
        e ou se já só depois nada ele ela eu sim senhor disse""".split()
    ),
}

# Characters that only one of these languages uses, weighted higher than a
# function word because a single occurrence is already decisive.
_MARKS = {
    "es": ("ñ", "¿", "¡"),
    "pt": ("ã", "õ"),
    "fr": ("ç", "œ"),
    "it": ("ì", "ò"),
}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_HIRAGANA = (0x3041, 0x309F)
_KATAKANA = (0x30A1, 0x30FF)
_HAN = (0x4E00, 0x9FFF)
_DEVANAGARI = (0x0900, 0x097F)


def _script_counts(text: str) -> dict[str, int]:
    counts = {"kana": 0, "han": 0, "devanagari": 0, "latin": 0, "other": 0}
    for char in text:
        code = ord(char)
        if _HIRAGANA[0] <= code <= _HIRAGANA[1] or _KATAKANA[0] <= code <= _KATAKANA[1]:
            counts["kana"] += 1
        elif _HAN[0] <= code <= _HAN[1]:
            counts["han"] += 1
        elif _DEVANAGARI[0] <= code <= _DEVANAGARI[1]:
            counts["devanagari"] += 1
        elif char.isalpha():
            counts["latin" if code < 0x0250 or 0x1E00 <= code <= 0x1EFF else "other"] += 1
        elif char.isdigit() or char.isspace() or unicodedata.category(char).startswith("P"):
            continue
        else:
            counts["other"] += 1
    return counts


def _latin_scores(text: str) -> tuple[dict[str, int], dict[str, int]]:
    """Scores per language, and how many of those hits only one language claims.

    The second count is what lets a short sentence be decided at all: one word
    that no other candidate uses ("oui", "sim", "nada") is worth more than three
    that four languages share ("la", "de", "que").
    """
    lowered = unicodedata.normalize("NFC", text).lower()
    tokens = _WORD_RE.findall(lowered)
    scores = {code: 0 for code in _WORDS}
    unique = {code: 0 for code in _WORDS}
    for token in tokens:
        owners = [code for code, words in _WORDS.items() if token in words]
        for code in owners:
            scores[code] += 1
        if len(owners) == 1:
            unique[owners[0]] += 1
    for code, marks in _MARKS.items():
        if any(mark in lowered for mark in marks):
            scores[code] += 2
            unique[code] += 1
    return scores, unique


def detect(text: str) -> str | None:
    """Best guess at the language of `text`, or None when it is not clear.

    Returns a base code from LANGUAGES. None means "cannot tell", which callers
    must treat as a third answer - not as a match and not as a mismatch.
    """
    if not text or not text.strip():
        return None

    counts = _script_counts(text)
    if counts["kana"]:
        return "ja"
    if counts["devanagari"]:
        return "hi"
    if counts["han"]:
        # Han with no kana: Chinese, unless it is a single stray character in a
        # Latin sentence, which the count check below keeps from happening.
        if counts["han"] >= max(1, counts["latin"] // 4):
            return "zh"
        return None
    if counts["latin"] < 2:
        return None

    scores, unique = _latin_scores(text)
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    best, best_score = ranked[0]
    _, second_score = ranked[1]
    if best_score >= 2 and best_score > second_score:
        return best
    # A single word is enough when nothing else can claim it: dialogue like
    # "Oui, madame." or "Sim, senhor." is common enough in a novel to matter.
    if best_score >= 1 and second_score == 0 and unique[best] >= 1:
        return best
    return None


def matches(text: str, language: str) -> bool:
    """True only when the text is confidently in `language`."""
    return bool(language) and detect(text) == language


def describe(code: str | None) -> str:
    if not code:
        return "an unknown language"
    return NAMES.get(code, code)
