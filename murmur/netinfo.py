"""Discover the addresses this Mac can be reached on (LAN and Tailscale)."""
from __future__ import annotations

import json
import re
import socket
import subprocess

from . import proc
_TAILSCALE_RE = re.compile(r"inet (100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+)")


def _run(command: list[str], timeout: float = 2.0) -> str:
    try:
        result = proc.run(command, capture_output=True, text=True, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def tailscale_state() -> str:
    """BackendState from the CLI, or 'unknown' when it is not installed."""
    output = _run(["tailscale", "status", "--json"], timeout=3.0)
    if not output:
        return "unknown"
    try:
        return str(json.loads(output).get("BackendState") or "unknown")
    except (ValueError, AttributeError):
        return "unknown"


def tailscale_ips() -> list[str]:
    # The live interface is the reliable signal; the CLI keeps reporting the
    # last address even while Tailscale is stopped.
    ips: set[str] = set(_TAILSCALE_RE.findall(_run(["ifconfig"])))
    if not ips and tailscale_state() == "Running":
        for line in _run(["tailscale", "ip", "-4"]).splitlines():
            line = line.strip()
            if line.count(".") == 3:
                ips.add(line)
    return sorted(ips)


def tailscale_name() -> str:
    if tailscale_state() != "Running":
        return ""
    output = _run(["tailscale", "status", "--json"], timeout=3.0)
    if not output:
        return ""
    try:
        data = json.loads(output)
        name = (data.get("Self") or {}).get("DNSName") or ""
        return name.rstrip(".")
    except (ValueError, AttributeError):
        return ""


def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def access_urls(port: int, token: str | None = None) -> list[dict]:
    """Return labelled URLs for reaching the server from elsewhere."""
    suffix = f"?token={token}" if token else ""
    urls: list[dict] = [{"label": "This Mac", "url": f"http://127.0.0.1:{port}/"}]

    tail = tailscale_ips()
    for ip in tail:
        urls.append({"label": "Tailscale", "url": f"http://{ip}:{port}/{suffix}"})

    name = tailscale_name()
    if name:
        urls.append({"label": "Tailscale DNS", "url": f"http://{name}:{port}/{suffix}"})

    for ip in lan_ips():
        if ip in tail:
            continue
        urls.append({"label": "Local network", "url": f"http://{ip}:{port}/{suffix}"})

    return urls
