"""Security guards for the URL importer. No network access required."""
from __future__ import annotations

import socket
import unittest
from unittest import mock

from murmur import extract


def _addr(ip: str, family: int = socket.AF_INET):
    return (family, socket.SOCK_STREAM, 6, "", (ip, 0))


class PrivateAddressTests(unittest.TestCase):
    def test_loopback_and_lan_names_are_blocked(self) -> None:
        for host in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.1.4", "169.254.1.1", "::1"):
            with self.subTest(host=host):
                self.assertFalse(extract._is_public_host(host))

    def test_public_address_is_allowed(self) -> None:
        with mock.patch("socket.getaddrinfo", return_value=[_addr("93.184.216.34")]):
            self.assertTrue(extract._is_public_host("example.com"))

    def test_any_private_answer_blocks_the_host(self) -> None:
        answers = [_addr("93.184.216.34"), _addr("127.0.0.1")]
        with mock.patch("socket.getaddrinfo", return_value=answers):
            self.assertFalse(extract._is_public_host("mixed.example"))

    def test_unresolvable_host_is_blocked(self) -> None:
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            self.assertFalse(extract._is_public_host("missing.example"))


class UrlSchemeTests(unittest.TestCase):
    def test_extract_url_rejects_unsupported_schemes(self) -> None:
        for url in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,hi",
            "ftp://example.com/x",
        ):
            with self.subTest(url=url), self.assertRaises(extract.ExtractError):
                extract.extract_url(url)

    def test_file_urls_are_rejected(self) -> None:
        with self.assertRaises(extract.ExtractError):
            extract._check_fetch_url("file:///etc/passwd")

    def test_javascript_urls_are_rejected(self) -> None:
        with self.assertRaises(extract.ExtractError):
            extract._check_fetch_url("javascript:alert(1)")

    def test_private_ip_urls_are_rejected(self) -> None:
        for url in (
            "http://127.0.0.1:8765/api/network",
            "http://192.168.1.4/",
            "http://[::1]/",
        ):
            with self.subTest(url=url), self.assertRaises(extract.ExtractError):
                extract._check_fetch_url(url)

    def test_public_url_passes(self) -> None:
        extract._check_fetch_url("https://8.8.8.8/")


if __name__ == "__main__":
    unittest.main()
