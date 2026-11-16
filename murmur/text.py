"""Text cleaning, sentence segmentation and word/audio alignment.

The TTS engine returns phoneme timings. We fold those into word groups (the
phonemizer emits a space phoneme between words) and map them onto the original
words, which gives real karaoke timing instead of guesses.
"""
from __future__ import annotations

import re

# Words that end in a period but do not end a sentence.
_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "fig",
    "al", "inc", "ltd", "co", "corp", "dept", "est", "approx", "apt", "ave",
    "blvd", "rd", "mt", "ft", "hr", "min", "sec", "vol", "cf", "ed", "eds",
    "trans", "rev", "gen", "col", "capt", "lt", "sgt", "gov", "sen", "rep",
    "pres", "supt", "det", "maj", "cmdr", "adm", "no", "nos", "pp", "p",
    "eq", "ref", "ch", "sec", "art", "op", "ibid", "viz", "esp", "ca",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri",
    "sat", "sun", "dept", "univ", "assn", "bros", "mfg", "dist", "div",
}

_END_RE = re.compile(r"[.!?…]+[\"'\u201d\u2019\)\]]*(?=\s|$)")
_CJK_END_RE = re.compile(r"[。！？]+[\u300d\u300f\u300b\u3009\uff09]*")
_NEWLINE_RE = re.compile(r"\n+")


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\t", " ")
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_markdown(text: str) -> str:
    """Strip formatting that would otherwise be read aloud."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)        # links
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)        # nested leftovers
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)          # inline code
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)             # bold
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)   # headings
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.M)        # blockquotes
    text = re.sub(r"^\s{0,3}[-*+]\s+", "", text, flags=re.M)    # bullets
    text = re.sub(r"^\s{0,3}\d+[.)]\s+", "", text, flags=re.M)  # numbered lists
    text = re.sub(r"^\s*([-*_]\s*){3,}$", "", text, flags=re.M)  # rules
    text = re.sub(r"<[^>\n]{1,80}>", "", text)                  # stray tags
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _boundary_positions(text: str) -> list[int]:
    cuts = {m.end() for m in _END_RE.finditer(text)}
    cuts |= {m.end() for m in _CJK_END_RE.finditer(text)}
    cuts |= {m.end() for m in _NEWLINE_RE.finditer(text)}
    return sorted(c for c in cuts if c > 0)


def _is_false_boundary(segment: str, following: str) -> bool:
    s, n = segment.rstrip(), following.lstrip()
    if not n:
        return False
    body = re.sub(r"[.!?…]+[\"'\u201d\u2019\)\]]*$", "", s).strip()
    match = re.search(r"[A-Za-z][A-Za-z.'\u2019]*$", body)
    token = match.group(0) if match else ""
    low = token.lower().strip(".")
    if low in _ABBREV:
        return True
    # Initialisms: U.S.  e.g.  i.e.
    if re.fullmatch(r"(?:[A-Za-z]\.){1,}[A-Za-z]?\.?", token):
        return True
    # Single initial before a name: "J. K. Rowling"
    if re.fullmatch(r"[A-Z]", token) and n[:1].isupper():
        return True
    # Continuation in lower case is almost never a new sentence.
    if n[:1].islower():
        return True
    if low in {"no", "fig", "vol", "pp", "p", "eq", "ref", "sec", "ch"} and n[:1].isdigit():
        return True
    return False


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", text))


def _join(a: str, b: str) -> str:
    if not a:
        return b
    if _has_cjk(a[-1:]) or _has_cjk(b[:1]):
        return a + b
    return a + " " + b


def _split_long(sentence: str, limit: int = 380) -> list[str]:
    if len(sentence) <= limit:
        return [sentence]
    # Prefer clause punctuation near the middle, then whitespace.
    mid = len(sentence) // 2
    window = range(max(1, mid - limit // 3), min(len(sentence) - 1, mid + limit // 3))
    best = None
    for i in window:
        if sentence[i] in ";:,\u2014\u2013" and sentence[i + 1 : i + 2] == " ":
            score = abs(i - mid)
            if best is None or score < best[0]:
                best = (score, i + 1)
    if best is None:
        for i in window:
            if sentence[i] == " ":
                best = (abs(i - mid), i + 1)
                break
    if best is None:
        return [sentence]
    cut = best[1]
    return _split_long(sentence[:cut].strip(), limit) + _split_long(sentence[cut:].strip(), limit)


def split_units(text: str, limit: int = 380) -> list[str]:
    """Split into speech units: whole sentences, merging tiny fragments."""
    text = clean_markdown(normalize(text))
    if not text:
        return []

    segments: list[str] = []
    prev = 0
    for cut in _boundary_positions(text):
        chunk = text[prev:cut]
        if chunk.strip():
            segments.append(chunk.strip())
        prev = cut
    if text[prev:].strip():
        segments.append(text[prev:].strip())

    # Re-join false sentence breaks (abbreviations, initials, decimals).
    merged: list[str] = []
    for seg in segments:
        if merged and _is_false_boundary(merged[-1], seg):
            merged[-1] = _join(merged[-1], seg)
        else:
            merged.append(seg)

    # Short fragments sound bad alone: attach them to a neighbour.
    units: list[str] = []
    carry = ""
    for seg in merged:
        seg = carry + (" " if carry and not _has_cjk(seg[:1]) else "") + seg if carry else seg
        carry = ""
        if len(seg) < 42:
            carry = seg
            continue
        if units and len(units[-1]) < 42 and len(units[-1]) + len(seg) < limit:
            units[-1] = _join(units[-1], seg)
        else:
            units.append(seg)
    if carry:
        if units and len(units[-1]) + len(carry) < limit + 80:
            units[-1] = _join(units[-1], carry)
        else:
            units.append(carry)

    final: list[str] = []
    for unit in units:
        final.extend(_split_long(unit, limit))
    return [u for u in final if re.search(r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff]", u)]


_WORD_RE = re.compile(r"\S+\s*")
_PAUSE = {",": 0.35, ";": 0.6, ":": 0.55, "\u2014": 0.6, ".": 0.9, "!": 0.9, "?": 0.9, "…": 1.1}


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _speech_weight(token: str) -> float:
    core = re.sub(r"[^\w\u4e00-\u9fff]", "", token)
    vowels = len(re.findall(r"[aeiouyAEIOUY]+", token))
    weight = 0.75 * max(1, len(core)) + 1.1 * vowels
    stripped = token.rstrip()
    if stripped and stripped[-1] in _PAUSE:
        weight += _PAUSE[stripped[-1]]
    return max(1.0, weight)


def _proportional(tokens: list[str], start: float, end: float) -> list[dict]:
    weights = [_speech_weight(t) for t in tokens]
    total = sum(weights) or 1.0
    out, acc = [], 0.0
    for token, weight in zip(tokens, weights):
        t0 = start + (end - start) * acc / total
        acc += weight
        t1 = start + (end - start) * acc / total
        out.append({"text": token, "start": round(t0, 3), "end": round(t1, 3)})
    return out


def align_words(text: str, groups: list[tuple[float, float]], duration: float) -> list[dict]:
    """Map phoneme word-groups onto the source words."""
    tokens = tokenize(text)
    if not tokens:
        return []
    if not groups:
        return _proportional(tokens, 0.0, duration or 1.0)
    if len(groups) == len(tokens):
        return [
            {"text": t, "start": round(g[0], 3), "end": round(g[1], 3)}
            for t, g in zip(tokens, groups)
        ]
    # Numbers, dates and symbols expand into more phoneme groups than words.
    return _proportional(tokens, groups[0][0], groups[-1][1])


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff]+", text))
