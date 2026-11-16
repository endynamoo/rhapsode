"""Subprocess wrappers that never flash a console window on Windows.

A windowed build (PyInstaller `console=False`, `pythonw`, or the pywebview
desktop app) has no console of its own, so every console child it starts —
`tailscale`, `ffmpeg`, `powershell` — would otherwise pop its own window. Route
all spawning through here and the user sees nothing.
"""
from __future__ import annotations

import os
import subprocess


def hidden() -> dict:
    """Keyword arguments that keep a child process invisible on Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def run(command, **kwargs):
    kwargs.update(hidden())
    return subprocess.run(command, **kwargs)


def popen(command, **kwargs):
    kwargs.update(hidden())
    return subprocess.Popen(command, **kwargs)
