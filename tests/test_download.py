"""download.fetch: write a .part, verify, rename, and fail instead of hanging."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from murmur import download


class FakeResponse:
    def __init__(self, body: bytes, declared: int | None = None) -> None:
        self.body = body
        self.headers = {"content-length": str(len(body) if declared is None else declared)}

    def read(self, size: int) -> bytes:
        block, self.body = self.body[:size], self.body[size:]
        return block

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FetchTests(unittest.TestCase):
    def test_a_complete_download_lands_and_reports_bytes(self) -> None:
        seen: list[int] = []
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "file.bin"
            with mock.patch("urllib.request.urlopen",
                            return_value=FakeResponse(b"x" * 600_000)):
                download.fetch("https://example.invalid/x", dest,
                               progress=lambda d, t, s: seen.append(d))
            self.assertEqual(600_000, dest.stat().st_size)
            self.assertFalse(dest.with_suffix(".bin.part").exists())
            self.assertTrue(seen)
            self.assertEqual(600_000, seen[-1])

    def test_a_checksum_mismatch_is_refused_and_removed(self) -> None:
        wrong = hashlib.sha256(b"not this").hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "weights.gguf"
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"y" * 1000)):
                with self.assertRaises(download.DownloadError) as caught:
                    download.fetch("https://example.invalid/x", dest, sha256=wrong)
            self.assertIn("checksum", str(caught.exception))
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_suffix(".gguf.part").exists())

    def test_a_matching_checksum_is_accepted(self) -> None:
        body = b"z" * 500
        digest = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "weights.gguf"
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(body)):
                download.fetch("https://example.invalid/x", dest, sha256=digest.upper())
            self.assertEqual(500, dest.stat().st_size)

    def test_an_empty_answer_is_an_error_not_an_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "file.bin"
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"")):
                with self.assertRaises(download.DownloadError):
                    download.fetch("https://example.invalid/x", dest)
            self.assertFalse(dest.exists())

    def test_a_transport_failure_leaves_nothing_behind(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "file.bin"
            with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
                with self.assertRaises(download.DownloadError):
                    download.fetch("https://example.invalid/x", dest)
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_suffix(".bin.part").exists())


if __name__ == "__main__":
    unittest.main()
