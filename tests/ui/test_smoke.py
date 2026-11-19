"""UI smoke tests: the journeys a reader takes, driven in a real browser.

This is the safety net for splitting `web/app.js` into modules. It runs against
a real server (same command CI's Linux `run` job uses) with a throwaway
`MURMUR_HOME`, and only covers what a break silently ruins: the library, opening
a book, search, Settings and adding a document.

Run it with playwright installed:

    python -m unittest tests.ui.test_smoke -v

On Windows, point it at an already-installed Edge so no browser download is
needed:  set PLAYWRIGHT_CHANNEL=msedge
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx

try:  # pragma: no cover - the suite is skipped when playwright is absent
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAVE_PLAYWRIGHT = False

ROOT = Path(__file__).resolve().parents[2]

TITLE = "Smoke Test Book"
BODY = (
    "Chapter One\n\n"
    "The lantern keeper counted the ships that evening, as she had done every "
    "evening for eleven years.\n\n"
    "Chapter Two\n\n"
    "The ledger answered on the seventh night, and the answer was a single line "
    "in her grandmother's hand.\n"
)
PHRASE = "lantern"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url: str, timeout: float = 45.0) -> dict:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            body = httpx.get(url, timeout=2).json()
            if body.get("ok"):
                return body
        except Exception as exc:  # server still starting
            last = exc
        time.sleep(0.3)
    raise AssertionError(f"{url} never answered: {last}")


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class SmokeTests(unittest.TestCase):
    """One server, one browser, a handful of journeys."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = {
            **os.environ,
            "MURMUR_HOME": cls.home.name,
            # The UI journey opens Cinema; a real painter must not start.
            "MURMUR_WARM_STILLS": "0",
            "MURMUR_PORT": str(cls.port),
            "MURMUR_SEMANTIC": "off",
        }
        cls.log = (Path(cls.home.name) / "server.log").open("wb")
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "murmur"],
            cwd=str(ROOT),
            env=env,
            stdout=cls.log,
            stderr=subprocess.STDOUT,
        )
        wait_for(f"{cls.base}/api/health")

        cls.doc = httpx.post(
            f"{cls.base}/api/docs",
            json={"title": TITLE, "text": BODY},
            timeout=20,
        ).json()
        httpx.post(f"{cls.base}/api/search/index", timeout=30)
        cls._wait_for_index()

        cls.playwright = sync_playwright().start()
        # "chromium" is the bundled build CI installs; a machine that would
        # rather not download it can name an installed channel, e.g. msedge.
        kwargs: dict = {
            "headless": True,
            "channel": os.environ.get("PLAYWRIGHT_CHANNEL", "chromium"),
        }
        try:
            cls.browser = cls.playwright.chromium.launch(**kwargs)
        except Exception as exc:
            # No browser on this machine: skip rather than fail, so `unittest
            # discover` stays useful where playwright is installed but its
            # Chromium has not been downloaded.
            cls.playwright.stop()
            cls.server.terminate()
            cls.server.wait(timeout=10)
            cls.log.close()
            cls.home.cleanup()
            raise unittest.SkipTest(f"no Chromium to drive ({exc})") from exc

    @classmethod
    def _wait_for_index(cls) -> None:
        deadline = time.time() + 60
        while time.time() < deadline:
            status = httpx.get(f"{cls.base}/api/search/status", timeout=5).json()
            if not status.get("pending") and not status.get("indexing", {}).get("running"):
                return
            time.sleep(0.4)
        raise AssertionError("the search index never finished")

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.browser.close()
            cls.playwright.stop()
        finally:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                cls.server.kill()
            cls.log.close()
            if cls._outcome_failed():
                log = Path(cls.home.name) / "server.log"
                if log.exists():
                    print("\n--- server log ---")
                    print(log.read_text("utf-8", "replace")[-4000:])
            cls.home.cleanup()

    @classmethod
    def _outcome_failed(cls) -> bool:
        result = getattr(cls, "_outcome", None)
        return bool(getattr(result, "errors", None) or getattr(result, "failures", None))

    def setUp(self) -> None:
        self.page = self.browser.new_page()
        self.page.set_default_timeout(15000)
        self.console: list[str] = []
        self.page.on("console", lambda message: self.console.append(message.text))
        self.page.on("pageerror", lambda error: self.console.append(f"pageerror: {error}"))

    def tearDown(self) -> None:
        if self.console:
            print("\nconsole:", *self.console, sep="\n  ")
        self.page.close()

    # ---------------------------------------------------------------- helpers
    def open_app(self) -> None:
        self.page.goto(self.base + "/", wait_until="load")
        self.page.wait_for_selector(".lib-item, .lib-empty, .empty-title")

    def open_settings(self) -> None:
        self.page.click("#settingsBtn")
        self.page.wait_for_selector("#settingsModal:not([hidden])")

    # ---------------------------------------------------------------- journeys
    def test_the_library_lists_the_book(self) -> None:
        self.open_app()
        titles = self.page.locator(".lib-item .lib-title").all_inner_texts()
        self.assertTrue(any(TITLE in title for title in titles), titles)

    def test_opening_a_book_shows_its_text(self) -> None:
        self.open_app()
        self.page.locator(".lib-item", has_text=TITLE).first.click()
        self.page.wait_for_selector("#reader .unit, #reader p")
        meta = self.page.locator("#docMeta").inner_text()
        self.assertIn("words", meta)
        text = self.page.locator("#reader").inner_text()
        self.assertIn("lantern keeper", text)
        # The bar must not still be advertising the empty library: that line was
        # written once at startup and survived opening a book (issue #52).
        subtitle = self.page.locator("#nowSub").inner_text()
        self.assertNotIn("Add a document to begin", subtitle, subtitle)
        self.assertIn("Press play", subtitle, subtitle)

    def test_the_settings_panel_is_wired(self) -> None:
        # The settings view used to be 450 lines in the middle of app.js; it is a
        # module now, so this checks the seam rather than the styling: the panel
        # opens, its remote fetches land, and nothing threw while it did.
        self.open_app()
        self.open_settings()
        self.page.wait_for_selector("#setEngineProfile button")
        summary = self.page.locator("#engineSummary").inner_text()
        self.assertTrue(summary.strip(), "the engine summary never filled in")
        self.assertNotIn("unavailable", summary.lower(), summary)
        status = self.page.locator("#remoteStatus").inner_text()
        self.assertIn("off", status.lower(), status)
        self.assertGreater(self.page.locator("#setEngineProfile button").count(), 1)
        self.assertGreater(self.page.locator("#setFontFamily option").count(), 1)
        errors = [line for line in self.console if line.startswith("pageerror:")]
        self.assertEqual(errors, [], "the page threw while opening Settings")

    def test_search_finds_a_phrase_in_the_book(self) -> None:
        self.open_app()
        self.page.click("#searchBtn")
        self.page.fill("#searchInput", PHRASE)
        self.page.wait_for_selector(".search-hit", timeout=20000)
        hit = self.page.locator(".search-hit").first
        self.assertIn(PHRASE, hit.inner_text().lower())

    def test_theme_changes_and_survives_a_reload(self) -> None:
        self.open_app()
        self.open_settings()
        before = self.page.evaluate("document.documentElement.dataset.theme")
        options = self.page.locator("#setThemeSelect option")
        self.assertGreater(options.count(), 1)
        values = [options.nth(i).get_attribute("value") for i in range(options.count())]
        other = next(value for value in values if value != before)
        self.page.select_option("#setThemeSelect", other)
        self.assertNotEqual(
            before,
            self.page.evaluate("document.documentElement.dataset.theme"),
        )
        self.page.reload(wait_until="load")
        self.page.wait_for_selector(".lib-item")
        self.assertEqual(
            other,
            self.page.evaluate("document.documentElement.dataset.theme"),
            "the theme is saved through /api/settings, so a reload must keep it",
        )

    def test_auto_generate_switches_flip_and_persist(self) -> None:
        self.open_app()
        self.open_settings()
        scenes = self.page.locator("#setAutoScenes")
        films = self.page.locator("#setAutoFilms")
        self.assertEqual("true", scenes.get_attribute("aria-checked"), "Scenes defaults on")
        self.assertEqual("false", films.get_attribute("aria-checked"), "Chapter films defaults off")

        films.click()
        self.page.wait_for_selector("#setAutoFilms[aria-checked='true']")
        saved = httpx.get(f"{self.base}/api/settings", timeout=10).json()
        self.assertTrue(saved["autoFilms"])
        self.assertNotIn("autoGenerate", saved, "the old single switch is gone")

        films.click()
        self.page.wait_for_selector("#setAutoFilms[aria-checked='false']")

    def test_a_document_can_be_added_through_the_modal(self) -> None:
        self.open_app()
        self.page.click("#addBtn")
        self.page.wait_for_selector("#addModal:not([hidden])")
        self.page.fill("#pasteTitle", "Added Through The UI")
        self.page.fill("#pasteText", "A short passage added by the smoke test. It has two sentences.")
        # Assert what was typed before submitting: on a cold runner the fills can
        # land before the modal finishes rendering, and the failure then reads as a
        # wrong document title far from the cause.
        self.assertEqual("Added Through The UI", self.page.input_value("#pasteTitle"))
        self.page.click("#pasteAdd")
        # A hidden modal is not "visible", so this must wait for the hidden state.
        self.page.locator("#addModal").wait_for(state="hidden", timeout=20000)
        # Wait for the row we just added, not for "a second row": the library is
        # seeded, so nth-child(2) can match before the new title is painted.
        row = self.page.locator(".lib-title", has_text="Added Through The UI").first
        row.wait_for(timeout=20000)
        titles = self.page.locator(".lib-title").all_inner_texts()
        self.assertTrue(any("Added Through The UI" in title for title in titles), titles)


if __name__ == "__main__":
    unittest.main()
