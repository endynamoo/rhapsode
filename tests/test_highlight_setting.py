"""Highlight the spoken word: the setting, its default, and that it sticks.

The reader's word highlight is on by default - it is the feature - but it is a
switch because some readers find any movement in the text distracting. Manga
never highlights (there is nothing to follow), and the setting is consulted by
`updateWordHighlight()` in web/app.js, so what these tests pin is the value the
page receives: a fresh install gets `true`, and turning it off survives a
restart instead of silently coming back on.
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from murmur import server, store

from tests.test_auto_generate import temp_home


class HighlightSettingTests(unittest.TestCase):
    def test_a_fresh_install_highlights_the_spoken_word(self) -> None:
        with temp_home():
            self.assertIs(store.DEFAULT_SETTINGS["highlightWords"], True)
            self.assertTrue(store.get_settings()["highlightWords"])

    def test_turning_it_off_survives_a_restart(self) -> None:
        with temp_home():
            store.set_settings({"highlightWords": False})
            store.close()
            store.init()
            self.assertFalse(store.get_settings()["highlightWords"])

    def test_the_page_reads_the_switch_from_the_settings_api(self) -> None:
        with temp_home():
            with TestClient(server.app) as client:
                self.assertTrue(client.get("/api/settings").json()["highlightWords"])
                reply = client.put("/api/settings", json={"highlightWords": False})
                self.assertEqual(reply.status_code, 200, reply.text)
                self.assertFalse(client.get("/api/settings").json()["highlightWords"])
