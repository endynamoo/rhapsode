"""Check that translation really works, in every language a voice speaks.

The reader translates a sentence into the voice's language before speaking it,
so a broken pairing is only noticed by someone listening to that voice. This
script asks a running lane for one sentence in each of the eight voice
languages, then reads the answer back with ``murmur.language.detect`` to prove
it came back in the language that was asked for - no ears required.

    python scripts/verify_translation.py
    python scripts/verify_translation.py --url http://127.0.0.1:8091/v1/chat/completions

With no --url the usual local ports are probed, the same way the server does it.
Exit code 0 means every language came back in the target language.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur import language, translate  # noqa: E402

SOURCE = "The lighthouse keeper walked down to the shore and waited for the boat."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="", help="OpenAI-compatible chat completions URL")
    parser.add_argument("--model", default="", help="model id, when the lane serves several")
    parser.add_argument(
        "--source",
        default=SOURCE,
        help="the English sentence to translate into every language",
    )
    args = parser.parse_args()

    backend = translate.HttpTranslator(url=args.url, model=args.model)
    if not backend.available():
        print("no translation lane answered - start one, or pass --url", file=sys.stderr)
        return 2
    print(f"lane: {backend.url}  model: {backend.model or '(first offered)'}")
    print(f"source ({language.detect(args.source) or '?'}): {args.source}\n")

    failures = 0
    for code in language.LANGUAGES:
        try:
            result = backend.translate(args.source, code)
        except (translate.TranslateUnavailable, translate.TranslateError) as exc:
            failures += 1
            print(f"  FAIL  {code:3} {language.NAMES[code]:18} {exc}")
            continue
        output = (result.get("text") or "").strip()
        detected = language.detect(output)
        ok = detected == code
        if not ok:
            failures += 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {code:3} {language.NAMES[code]:18} [{detected or '?'}] {output[:70]}")

    total = len(language.LANGUAGES)
    print(f"\n{total - failures}/{total} languages translated into themselves")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
