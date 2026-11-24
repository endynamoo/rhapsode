"""The access guard: who counts as local, and when a token is required.

ARCHITECTURE describes the Host-header check and the remote token, but the suite only
covered the URL importer - so nothing would have caught the guard being switched off.
"""
from __future__ import annotations

import unittest

from murmur import access, config


class FakeRequest:
    def __init__(self, host_header: str, client: str = "127.0.0.1") -> None:
        self.headers = {"host": host_header}
        self.client = type("Client", (), {"host": client})()


class HostHeaderTests(unittest.TestCase):
    def test_loopback_names_are_accepted(self) -> None:
        for name in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765"):
            with self.subTest(name=name):
                self.assertTrue(access.local_host_ok(FakeRequest(name)))

    def test_a_rebinding_name_is_refused(self) -> None:
        self.assertFalse(access.local_host_ok(FakeRequest("evil.example:8765")))

    def test_a_lan_address_is_not_loopback(self) -> None:
        self.assertFalse(access.local_host_ok(FakeRequest("192.168.1.5:8765")))

    def test_a_lan_client_is_not_local(self) -> None:
        self.assertFalse(access.is_local(FakeRequest("192.168.1.5:8765", "192.168.1.5")))
        self.assertTrue(access.is_local(FakeRequest("127.0.0.1:8765", "127.0.0.1")))


class TokenTests(unittest.TestCase):
    def test_only_the_same_token_matches(self) -> None:
        self.assertTrue(access.matches("abc", "abc"))
        self.assertFalse(access.matches("abc", "abd"))
        self.assertFalse(access.matches("abc", "abcd"))

    def test_an_empty_or_missing_value_never_matches(self) -> None:
        self.assertFalse(access.matches("", ""))
        self.assertFalse(access.matches("", "abc"))
        self.assertFalse(access.matches(None, "abc"))


class PublicBindTests(unittest.TestCase):
    LOCAL = "127.0.0.1"

    def test_the_default_is_local(self) -> None:
        self.assertIn(self.LOCAL, config.LOOPBACK)
        self.assertFalse(access.public_bind(self.LOCAL, []))

    def test_an_env_host_widens_it(self) -> None:
        self.assertTrue(access.public_bind("0.0.0.0", []))

    def test_a_uvicorn_host_flag_widens_it_too(self) -> None:
        # `uvicorn murmur.server:app --host 0.0.0.0` never sets MURMUR_HOST. Reading the
        # command line is what turns the token guard on for that start; before, the socket
        # was open to the network while PUBLIC stayed false.
        argv = ["uvicorn", "murmur.server:app", "--host", "0.0.0.0"]
        self.assertTrue(access.public_bind(self.LOCAL, argv))
        self.assertTrue(access.public_bind(self.LOCAL, ["uvicorn", "murmur.server:app", "--host=0.0.0.0"]))

    def test_a_loopback_flag_stays_local(self) -> None:
        argv = ["uvicorn", "murmur.server:app", "--host", "127.0.0.1"]
        self.assertFalse(access.public_bind(self.LOCAL, argv))


if __name__ == "__main__":
    unittest.main()
