"""Follow along must gate every scroll path, not just the plain unit ticker.

The chapter highlight has its own scroll: it used to call `scrollIntoView`
unconditionally, so turning the switch off did nothing while reading a book.
This guards the rule without needing audio: both scrollers must ask
`state.autoScroll` before they move the page.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "web" / "app.js"


class FollowAlongGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = APP.read_text(encoding="utf-8")

    def body_of(self, name: str) -> str:
        start = self.source.index(f"function {name}(")
        depth = 0
        for index in range(start, len(self.source)):
            if self.source[index] == "{":
                depth += 1
            elif self.source[index] == "}":
                depth -= 1
                if depth == 0:
                    return self.source[start:index]
        raise AssertionError(f"{name} never closes")

    def test_the_plain_ticker_honours_the_switch(self) -> None:
        self.assertIn("state.autoScroll", self.body_of("maybeAutoScroll"))

    def test_the_chapter_highlight_honours_the_switch(self) -> None:
        body = self.body_of("highlightChapterWord")
        self.assertIn("scrollIntoView", body, "the chapter scroll moved somewhere else")
        self.assertIn(
            "state.autoScroll",
            body,
            "the chapter highlight scrolls without asking the setting",
        )

    def test_no_scroll_path_ignores_the_setting(self) -> None:
        scrollers = [
            name
            for name in re.findall(r"function (\w+)\(", self.source)
            if "scrollTo(" in self.body_of(name) or "scrollIntoView" in self.body_of(name)
        ]
        # Surfaces the setting does not govern: the Contents list scrolls itself to
        # keep the current chapter visible, and scrollToUnit is an explicit jump the
        # reader asked for (clicking a chapter, starting a sentence).
        EXEMPT = {"updateTocActive", "scrollToUnit"}
        self.assertTrue(scrollers, "no scroll path found - did the helpers get renamed?")
        for name in scrollers:
            if name in EXEMPT:
                continue
            body = self.body_of(name)
            self.assertIn(
                "state.autoScroll",
                body,
                f"{name} scrolls the page without asking the setting",
            )


if __name__ == "__main__":
    unittest.main()
