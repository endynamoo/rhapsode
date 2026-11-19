"""The shared remote-access copy never names an operating system."""
from __future__ import annotations

import unittest
from pathlib import Path

from murmur import netinfo

ROOT = Path(__file__).resolve().parents[1]

# The surfaces every platform shares. A name like "Mac" here is wrong on four of
# them, and it had been copied into five modules before this test existed.
SHARED_SOURCES = (
    "murmur/netinfo.py",
    "murmur/server.py",
    "murmur/system_api.py",
    "murmur/desktop.py",
    "murmur/__main__.py",
    "web/views/settings.js",
)


class SystemAgnosticCopyTests(unittest.TestCase):
    def test_the_local_address_is_named_for_its_role(self) -> None:
        label = netinfo.LOCAL_LABEL
        self.assertNotIn("Mac", label)
        self.assertTrue(label.strip())

    def test_access_urls_starts_with_the_local_label(self) -> None:
        urls = netinfo.access_urls(8765)
        self.assertEqual(netinfo.LOCAL_LABEL, urls[0]["label"])
        self.assertEqual("http://127.0.0.1:8765/", urls[0]["url"])

    def test_shared_surfaces_do_not_name_an_operating_system(self) -> None:
        offenders = []
        for relative in SHARED_SOURCES:
            for number, line in enumerate((ROOT / relative).read_text("utf-8").splitlines(), 1):
                if "Mac" in line and not line.strip().startswith(("#", "//", "*")):
                    offenders.append(f"{relative}:{number}: {line.strip()[:90]}")
        self.assertEqual([], offenders, "shared copy names one platform")


if __name__ == "__main__":
    unittest.main()
