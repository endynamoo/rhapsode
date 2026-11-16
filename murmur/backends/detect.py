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

from .. import proc
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


def _windows_vram() -> list[tuple[str, float]]:
    """Adapter sizes from the display-adapter class key.

    WMI reports AdapterRAM as a uint32, so anything above 4 GB comes back as
    4 GB (or 0). The registry keeps the real figure as a QWORD, in bytes.
    """
    try:
        import winreg
    except ImportError:
        return []
    class_key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    found: list[tuple[str, float]] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, class_key)
    except OSError:
        return []
    with root:
        for index in range(16):
            try:
                sub = winreg.OpenKey(root, f"{index:04d}")
            except OSError:
                continue
            with sub:
                try:
                    desc = str(winreg.QueryValueEx(sub, "DriverDesc")[0])
                    size, _kind = winreg.QueryValueEx(sub, "HardwareInformation.qwMemorySize")
                except OSError:
                    continue
                if isinstance(size, bytes) and len(size) >= 8:
                    size = int.from_bytes(size[:8], "little")
                try:
                    total = float(size)
                except (TypeError, ValueError):
                    continue
                if total > 0:
                    found.append((desc, round(total / (1024 ** 3), 1)))
    return found


def _windows_gpu() -> tuple[str, str, float | None]:
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"
    )
    try:
        result = proc.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return "", "", None
        data = json.loads(result.stdout or "[]")
    except Exception:
        return "", "", None
    if isinstance(data, dict):
        data = [data]
    registry = {name.lower(): size for name, size in _windows_vram()}
    for item in data:
        name = str(item.get("Name") or "").strip()
        vendor = _vendor(name)
        if vendor == "unknown":
            continue
        ram = item.get("AdapterRAM")
        vram = round(float(ram) / (1024**3), 1) if ram else None
        # WMI's uint32 cannot express a card larger than 4 GB.
        if vram is None or vram < 4.05:
            exact = registry.get(name.lower())
            if exact and exact > (vram or 0):
                vram = exact
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
