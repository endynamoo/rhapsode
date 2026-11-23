"""One place to pick the engine configuration, by name or by hardware.

A profile is the set of decisions that used to be scattered across env vars and
VRAM thresholds: which precision, where the text encoder lives, how much headroom
the painter must leave, what quality the stills default to, and whether the polish
pass runs. `recommend()` picks one from the card; `save()` lets anybody override it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PROFILE_FILE = "engine.json"

# Every knob the runtime reads, with the value that suits the machine described.
PROFILES: dict[str, dict] = {
    "lite": {
        # 6-10 GB cards: the card is the scarce resource, speed comes second.
        "precision": "q4",
        "text_device": "cpu",
        "reserve_mib": 2000,
        "quality": "fast",
        "polish": False,
        "why": "q4 transformer with the text encoder on the CPU: ~5 GiB, leaves the card to the desktop",
    },
    "balanced": {
        # 12-20 GB cards shared with a desktop or a remote-desktop encoder.
        "precision": "q4",
        "text_device": "gpu",
        "reserve_mib": 2500,
        "quality": "high",
        "polish": True,
        "why": "q4 transformer and encoder on the GPU: ~13 GiB, 1080p stills with the polish pass",
    },
    "max": {
        # 24 GB and up, nothing else using the card.
        "precision": "bf16",
        "text_device": "gpu",
        "reserve_mib": 2500,
        "quality": "high",
        "polish": True,
        "why": "full bf16 residency: fastest, and the card is big enough to spare it",
    },
    "cpu": {
        "precision": "cpu",
        "text_device": "cpu",
        "reserve_mib": 0,
        "quality": "fast",
        "polish": False,
        "why": "no usable GPU: placeholder stills, reader unaffected",
    },
}


def recommend(vram_gb: float | None) -> str:
    """Name of the profile that fits a card of this size."""
    if vram_gb is None or vram_gb <= 0:
        return "cpu"
    if vram_gb < 10:
        return "lite"
    if vram_gb < 22:
        return "balanced"
    return "max"


def path(home: Path) -> Path:
    return home / PROFILE_FILE


#: What a reconciliation replaced, for the UI to mention once. Reset by `load`.
RECONCILED: dict = {}


def load(home: Path) -> dict:
    """The saved profile, or a recommendation for this machine.

    A saved profile this build cannot honour is treated as a recommendation, not a
    setting: 0.3.0 shipped a "cpu" button that turned every scene into a placeholder, the
    button is gone, and a machine that once chose it stayed in placeholder mode forever
    with copy that blamed its hardware. The file is rewritten when that happens, so the
    state does not come back on the next start.
    """
    file = path(home)
    RECONCILED.clear()
    try:
        data = json.loads(file.read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    recommended = recommend(_detect_vram())
    saved = data.get("profile")
    name = saved or recommended
    if saved == "cpu" and recommended != "cpu":
        # The card can paint; the stored answer cannot. Say so once and move on.
        RECONCILED.update({"was": "cpu", "now": recommended})
        name = recommended
        data = {}
    profile = dict(PROFILES.get(name, PROFILES["balanced"]))
    profile["profile"] = name if name in PROFILES else "balanced"
    if RECONCILED:
        try:
            save(home, recommended)
        except OSError:
            pass
    # Any explicit value in the file wins over the preset.
    for key in ("precision", "text_device", "reserve_mib", "quality", "polish"):
        if key in data:
            profile[key] = data[key]
    return profile


def save(home: Path, name: str | None = None, **overrides) -> dict:
    """Write a profile (and any per-key overrides) into the data directory."""
    profile = dict(PROFILES.get(name or "balanced", PROFILES["balanced"]))
    profile.pop("why", None)
    profile.update({key: value for key, value in overrides.items() if value is not None})
    file = path(home)
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"profile": name or "balanced", **profile}
    file.write_text(json.dumps(payload, indent=2), "utf-8")
    return payload


def describe(profile: dict) -> str:
    """A line a human can read, for the installer and for health."""
    where = "CPU" if profile.get("text_device") == "cpu" else "GPU"
    return (
        f"{profile.get('profile', '?')}: {profile.get('precision', '?')} transformer, "
        f"text encoder on the {where}, {profile.get('reserve_mib', 0)} MiB reserved, "
        f"{profile.get('quality', 'high')} stills, polish "
        f"{'on' if profile.get('polish') else 'off'}"
    )


def apply_env(profile: dict) -> None:
    """Hand the profile to the painter, which reads env for its decisions."""
    os.environ["MURMUR_FLUX_PRECISION"] = str(profile.get("precision", "q4"))
    if profile.get("text_device") == "cpu":
        os.environ["MURMUR_FLUX_TEXT_DEVICE"] = "cpu"
    else:
        os.environ.pop("MURMUR_FLUX_TEXT_DEVICE", None)
    if profile.get("reserve_mib") is not None:
        os.environ["MURMUR_FLUX_RESERVE_MB"] = str(profile["reserve_mib"])


def activate(home: Path, name: str, **overrides) -> dict:
    """Switch the engine now: save the profile, hand it to the environment, seed
    the reading defaults, and let the caller restart the painter."""
    from . import config, store  # local: config imports this module

    saved = save(home, name, **overrides)
    loaded = load(home)
    apply_env(loaded)
    config.PROFILE = loaded
    patch = {}
    if "quality" in loaded:
        patch["filmQuality"] = loaded["quality"]
    if "polish" in loaded:
        patch["polishStills"] = bool(loaded["polish"])
    if patch:
        store.set_settings(patch)
    return loaded

def _detect_vram() -> float | None:
    """VRAM in GB from the display-adapter key, without importing torch."""
    try:
        import winreg

        class_key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        best = 0.0
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, class_key)
        with root:
            for index in range(16):
                try:
                    sub = winreg.OpenKey(root, f"{index:04d}")
                except OSError:
                    continue
                with sub:
                    try:
                        size, _ = winreg.QueryValueEx(sub, "HardwareInformation.qwMemorySize")
                    except OSError:
                        continue
                    if isinstance(size, bytes) and len(size) >= 8:
                        size = int.from_bytes(size[:8], "little")
                    try:
                        best = max(best, float(size) / 2**30)
                    except (TypeError, ValueError):
                        continue
        return best or None
    except Exception:
        return None
