"""The voice download path a packaged app uses, without the models or the network.

`python -m murmur.bootstrap` is a source-install convenience; the app drives the same
weights through `start_download()`. Anything the CLI does that the app does not is a
dev-machine assumption, which is what these tests guard.
"""
from __future__ import annotations

import unittest
from unittest import mock

from murmur import bootstrap


class StartDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap._STATE.update(running=False, done=False, error=None, progress=None)

    def test_the_app_download_also_prepares_the_language_data(self) -> None:
        # pyopenjtalk fetches a 23 MB dictionary on the first Japanese sentence otherwise,
        # with no progress and no explanation. The CLI calls fetch_language_data; the app
        # must too.
        calls: list[str] = []
        with mock.patch.object(bootstrap.config, "voice_model_ready", return_value=False), \
                mock.patch.object(bootstrap, "download_weights"), \
                mock.patch.object(bootstrap, "fetch_language_data",
                                  side_effect=lambda log=None: calls.append("fetched")):
            bootstrap.start_download()
            bootstrap._download_thread.join(timeout=10) if hasattr(bootstrap, "_download_thread") else None
            for _ in range(100):
                if not bootstrap._STATE["running"]:
                    break
                import time

                time.sleep(0.05)
        self.assertTrue(calls, "the voice download must also fetch the Japanese dictionary")
        self.assertTrue(bootstrap._STATE["done"])
        self.assertIsNone(bootstrap._STATE["error"])

    def test_a_failed_dictionary_does_not_fail_the_voice_download(self) -> None:
        # fetch_language_data never raises by contract; prove the app tolerates a False result.
        with mock.patch.object(bootstrap.config, "voice_model_ready", return_value=False), \
                mock.patch.object(bootstrap, "download_weights"), \
                mock.patch.object(bootstrap, "fetch_language_data", return_value=False):
            bootstrap.start_download()
            for _ in range(100):
                if not bootstrap._STATE["running"]:
                    break
                import time

                time.sleep(0.05)
        self.assertTrue(bootstrap._STATE["done"])
        self.assertIsNone(bootstrap._STATE["error"])


if __name__ == "__main__":
    unittest.main()
