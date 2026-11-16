"""List fonts installed on the host so the reader can offer them.

The list is cached for a week because enumerating system fonts can be slow on
macOS and Windows.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from . import config, proc

CACHE_PATH = config.HOME / "fonts.json"
CACHE_SECONDS = 7 * 24 * 60 * 60
MAX_FONTS = 400
SKIP_WORDS = (
    "braille",
    "emoji",
    "symbol",
    "icons",
    "dingbat",
    "webding",
    "wingding",
    "lastresort",
    "noto color",
    "apple color",
)


def _clean(names: list[str]) -> list[str]:
    seen: set[str] = set()
    fonts: list[str] = []
    for raw in names:
        name = " ".join(str(raw or "").split()).strip()
        if not name or len(name) > 60:
            continue
        low = name.lower()
        if any(word in low for word in SKIP_WORDS):
            continue
        if low in seen:
            continue
        seen.add(low)
        fonts.append(name)
    fonts.sort(key=str.casefold)
    return fonts[:MAX_FONTS]


def _macos() -> list[str]:
    try:
        from AppKit import NSFontManager

        families = NSFontManager.sharedFontManager().availableFontFamilies()
        if families:
            return list(families)
    except Exception:
        pass
    return _fc_list()


def _windows() -> list[str]:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts",
        )
        names: list[str] = []
        try:
            index = 0
            while True:
                try:
                    value, _data, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                names.append(value.replace("(TrueType)", "").replace("(OpenType)", ""))
                index += 1
        finally:
            key.Close()
        return names
    except Exception:
        return []


def _fc_list() -> list[str]:
    try:
        result = proc.run(
            ["fc-list", "--format", "%{family}\\n"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return []
        names: list[str] = []
        for line in result.stdout.splitlines():
            names.extend(part.strip() for part in line.split(",") if part.strip())
        return names
    except (OSError, subprocess.SubprocessError):
        return []


def _scan_dirs() -> list[str]:
    folders = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    names = []
    for folder in folders:
        try:
            for path in folder.iterdir():
                if path.suffix.lower() in {".ttf", ".otf", ".ttc", ".dfont"}:
                    names.append(path.stem.replace("-", " "))
        except OSError:
            continue
    return names


def system_fonts(refresh: bool = False) -> list[str]:
    """Return installed font family names, cached for a week."""
    if not refresh:
        try:
            data = json.loads(CACHE_PATH.read_text("utf-8"))
            if (
                isinstance(data, dict)
                and time.time() - float(data.get("created", 0)) < CACHE_SECONDS
                and isinstance(data.get("fonts"), list)
            ):
                return [str(name) for name in data["fonts"]]
        except (OSError, ValueError, TypeError):
            pass

    if sys.platform == "darwin":
        names = _macos() or _fc_list() or _scan_dirs()
    elif sys.platform.startswith("win"):
        names = _windows()
    else:
        names = _fc_list() or _scan_dirs()

    fonts = _clean(names)
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps({"created": time.time(), "platform": platform.system(), "fonts": fonts}),
            "utf-8",
        )
    except OSError:
        pass
    return fonts
