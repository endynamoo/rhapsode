"""Resolve how the server should bind (loopback or the local network)."""
from __future__ import annotations

import os

from . import config, store


def resolve_host(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("MURMUR_HOST", "").strip()
    if env:
        return env
    try:
        store.init()
        if store.get_settings().get("remoteAccess"):
            return "0.0.0.0"
    except Exception:
        pass
    return "127.0.0.1"


def apply(host: str, port: int) -> None:
    config.HOST = host
    config.PORT = port
    config.PUBLIC = host not in config.LOOPBACK
