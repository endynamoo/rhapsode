"""Best-effort hardware and platform detection.

The result is cached and exposed through /api/health so the UI can show the
active accelerator. Detection never raises: an unknown machine is reported as
CPU-only.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import threading
from pathlib import Path

from . import tts

_cache: dict | None = None
_lock = threading.Lock()
_warm_started = False


def _vendor(name: str) -> str:
    low = (name or "").lower()
    if any(word in low for word in ("nvidia", "geforce", "quadro", "rtx", "tesla")):
        return "nvidia"
    if any(word in low for word in ("amd", "radeon", "ati ", "firepro")):
        return "amd"
    if any(word in low for word in ("intel", "iris", "uhd graphics", "arc ")):
        return "intel"
    if "apple" in low:
        return "apple"
    return "unknown"


def _windows_gpu() -> tuple[str, str, float | None]:
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode != 0:
            return "", "", None
        data = json.loads(result.stdout or "[]")
    except Exception:
        return "", "", None
    if isinstance(data, dict):
        data = [data]
    for item in data:
        name = str(item.get("Name") or "").strip()
        vendor = _vendor(name)
        if vendor == "unknown":
            continue
        ram = item.get("AdapterRAM")
        vram = round(float(ram) / (1024**3), 1) if ram else None
        return name, vendor, vram
    return "", "", None


def _linux_gpu() -> tuple[str, str, float | None]:
    vendors = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        device = card / "device"
        try:
            vid = (device / "vendor").read_text("utf-8").strip().lower()
        except OSError:
            continue
        vendor = vendors.get(vid, "unknown")
        try:
            name = (device / "product_name").read_text("utf-8").strip()
        except OSError:
            name = ""
        vram = None
        try:
            total = int((device / "mem_info_vram_total").read_text("utf-8").strip())
            if total > 0:
                vram = round(total / (1024**3), 1)
        except (OSError, ValueError):
            pass
        if vendor != "unknown" or name:
            return name or f"{vendor} GPU", vendor, vram
    return "", "", None


def _mac_gpu() -> tuple[str, str, float | None]:
    arch = platform.machine()
    if arch == "arm64":
        return "Apple Silicon GPU", "apple", None
    return "Mac GPU", "apple", None


def _gpu() -> tuple[str, str, float | None]:
    if sys.platform == "darwin":
        return _mac_gpu()
    if sys.platform.startswith("win"):
        return _windows_gpu()
    return _linux_gpu()


def _fallback() -> dict:
    return {
        "os": "windows" if sys.platform.startswith("win") else sys.platform,
        "arch": platform.machine(),
        "gpu": "",
        "gpuVendor": "unknown",
        "vramGb": None,
        "providers": tts.available_providers(),
        "ttsProvider": tts.active_provider(),
        "acceleratedOnnx": tts.accelerated_installed(),
        "probing": True,
    }


def _warm() -> None:
    try:
        probe()
    except Exception:
        pass


def _ensure_warm() -> None:
    global _warm_started
    if _warm_started:
        return
    with _lock:
        if _warm_started:
            return
        _warm_started = True
    threading.Thread(target=_warm, name="capability-probe", daemon=True).start()


def snapshot() -> dict:
    """Cheap, non-blocking view for health endpoints."""
    if _cache is not None:
        return _cache
    _ensure_warm()
    return _fallback()


def probe(refresh: bool = False) -> dict:
    """Return cached platform/accelerator information."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    with _lock:
        if _cache is not None and not refresh:
            return _cache
        name, vendor, vram = _gpu()
        providers = tts.available_providers()
        _cache = {
            "os": "windows" if sys.platform.startswith("win") else sys.platform,
            "arch": platform.machine(),
            "gpu": name,
            "gpuVendor": vendor,
            "vramGb": vram,
            "providers": providers,
            "ttsProvider": tts.active_provider(),
            "acceleratedOnnx": tts.accelerated_installed(),
        }
        return _cache


def reset() -> None:
    """Clear the cache (tests and hot-plug scenarios)."""
    global _cache, _warm_started
    with _lock:
        _cache = None
        _warm_started = False
