"""Set up the stills engine from inside the app.

The installer can run `scripts/setup_engine.ps1` as an optional task, and a reader who
declines it meets Cinema as "no stills engine" with a fix they have to find. This runs the
same script as a background job: the first-run card starts it with one tick, Settings can
re-run it, and its output is visible while it works.

The script is the source of truth for what gets installed (torch for the card's vendor,
diffusers, the q4 transformer, the detectors, the profile); this module only supervises it
and reports state. It is Windows-only (PowerShell + `Scripts\\python.exe`); macOS has
`scripts/setup_flux.sh`, and Linux has no script at all, which the status says plainly.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from . import config, proc

_lock = threading.RLock()
_MAX_LINES = 400
_state = {"running": False, "done": False, "error": None, "lines": []}
_child = None          # proc.spawn handle
_reader: threading.Thread | None = None


def script_path() -> Path | None:
    """The setup script for this platform, when one ships."""
    if sys.platform.startswith("win"):
        candidate = config.APP_ROOT / "scripts" / "setup_engine.ps1"
        return candidate if candidate.is_file() else None
    if sys.platform == "darwin":
        candidate = config.APP_ROOT / "scripts" / "setup_flux.sh"
        return candidate if candidate.is_file() else None
    return None


def venv_python() -> Path:
    return Path(config.IMAGE_PYTHON)


def installed() -> bool:
    """An interpreter exists where the painter looks for one."""
    return venv_python().is_file()


def needed() -> bool:
    """A machine that can paint, with nothing set up yet."""
    active = config.PROFILE or {}
    return (
        script_path() is not None
        and not installed()
        and active.get("profile") not in {None, "", "cpu"}
    )


def command() -> list[str]:
    script = script_path()
    if script is None:
        raise RuntimeError(no_script_reason())
    if script.suffix == ".ps1":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    return ["bash", str(script)]


def no_script_reason() -> str:
    if sys.platform.startswith("linux"):
        return (
            "Linux has no setup script: build the stills interpreter by hand where the "
            "painter looks for it (docs/STILLS.md, 'Install the stills engine once')."
        )
    return "The stills setup script is missing from this installation."


def status() -> dict:
    with _lock:
        state = dict(_state)
        state["lines"] = list(_state["lines"])
    return {
        "available": script_path() is not None,
        "installed": installed(),
        "needed": needed(),
        "python": str(venv_python()),
        "profile": (config.PROFILE or {}).get("profile"),
        "run": state,
        "reason": "" if script_path() is not None else no_script_reason(),
    }


def start() -> dict:
    """Run the setup in the background. Idempotent while it is running or already done."""
    global _child, _reader
    with _lock:
        if _state["running"]:
            return status()
        if installed():
            _state.update(running=False, done=True, error=None)
            return status()
        if script_path() is None:
            _state.update(running=False, done=False, error=no_script_reason())
            return status()
        _state.update(running=True, done=False, error=None, lines=[])
        try:
            _child = proc.spawn(
                command(),
                cwd=str(config.APP_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised at the caller
            _state.update(running=False, done=False, error=str(exc))
            return status()

    def pump() -> None:
        assert _child is not None
        try:
            for line in _child.stdout:
                text = line.rstrip()
                if not text:
                    continue
                with _lock:
                    _state["lines"].append(text)
                    del _state["lines"][:-_MAX_LINES]
            code = _child.wait()
            with _lock:
                _state["running"] = False
                if code == 0:
                    _state.update(done=True, error=None)
                else:
                    _state.update(done=False, error=f"setup exited with code {code}")
        except Exception as exc:  # noqa: BLE001 - the reader is told, the app carries on
            with _lock:
                _state.update(running=False, done=False, error=str(exc))

    _reader = threading.Thread(target=pump, name="stills-setup", daemon=True)
    _reader.start()
    return status()


def stop() -> None:
    """Stop a setup that is still running. Safe when nothing runs."""
    global _child
    with _lock:
        child, _child = _child, None
    if child is not None and child.poll() is None:
        try:
            child.terminate()
            child.wait(timeout=5)
        except Exception:  # noqa: BLE001 - a child that will not die is reaped by the job object
            pass
    with _lock:
        _state["running"] = False
