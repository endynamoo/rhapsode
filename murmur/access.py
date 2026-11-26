"""Who is calling, and whether they are allowed in.

Split out of `server.py` because two sides need the same answer: the access
middleware, and the `/api/network` route that tells a local caller how to reach
the server. Keeping the token in one module means a route can never disagree
with the guard that let it through.
"""
from __future__ import annotations

import hmac
import ipaddress
import sys
from urllib.parse import urlsplit

from fastapi import Request

from . import config, netinfo

def public_bind(host: str, argv: list[str]) -> bool:
    """Whether this process will accept connections from another machine.

    `MURMUR_HOST` is how the app's own launchers widen the bind, and the value is
    already resolved into `host`. A bare `uvicorn murmur.server:app --host 0.0.0.0`
    does not set it: the socket is open to the LAN while `PUBLIC` stayed false, which
    meant the guard let every remote request through with no token. Reading the host
    out of the command line closes that path, and the default (nothing named) is still
    local-only.
    """
    if host and host.strip() not in config.LOOPBACK:
        return True
    for index, part in enumerate(argv):
        if part == "--host" and index + 1 < len(argv):
            return argv[index + 1].strip() not in config.LOOPBACK
        if part.startswith("--host="):
            return part.split("=", 1)[1].strip() not in config.LOOPBACK
    return False


PUBLIC = config.PUBLIC if config.PUBLIC is not None else public_bind(config.HOST, sys.argv)
TOKEN = config.access_token() if PUBLIC else ""


def client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def is_local(request: Request) -> bool:
    host = client_host(request)
    return host in config.LOOPBACK or host.startswith("127.")


def local_host_ok(request: Request) -> bool:
    """Reject DNS-rebinding requests that reach the loopback server by name."""
    raw = request.headers.get("host", "")
    try:
        host = (urlsplit(f"//{raw}").hostname or "").lower()
    except ValueError:
        return False
    if host in config.LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def matches(value: str, expected: str) -> bool:
    try:
        return bool(expected) and hmac.compare_digest(value, expected)
    except TypeError:
        return False


def same_origin(request: Request) -> bool:
    """Whether an unsafe request came from this app, not a page on another site.

    127.0.0.1 is not a CSRF boundary: any web page the reader has open can POST to
    the loopback server, and a no-body POST needs no CORS preflight. Quit, engine
    setup and the model downloads are all reachable that way, so every unsafe method
    must prove it came from this origin. Browsers send `Origin` on cross-site POSTs
    and `Sec-Fetch-Site` on every request; clients that are not browsers (curl, the
    test suite, the desktop shell) send neither, and are allowed because running them
    already requires access to this machine.
    """
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if site and site not in {"same-origin", "none"}:
        return False
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return True
    if origin == "null":
        return False
    try:
        parts = urlsplit(origin)
        header = urlsplit(f"//{(request.headers.get('host') or '').strip()}")
    except ValueError:
        return False
    if not parts.hostname or not header.hostname:
        return False
    if parts.hostname.lower() != header.hostname.lower():
        return False
    return parts.port == header.port


__all__ = ["PUBLIC", "TOKEN", "access_urls", "client_host", "is_local", "local_host_ok",
           "matches", "public_bind", "same_origin"]


def access_urls() -> list[dict]:
    """Advertised URLs for this server, with the token only when the LAN is on."""
    return netinfo.access_urls(config.PORT, TOKEN if PUBLIC else None)
