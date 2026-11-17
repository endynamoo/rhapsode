"""Subprocess wrappers that never flash a console window on Windows.

A windowed build (PyInstaller `console=False`, `pythonw`, or the pywebview
desktop app) has no console of its own, so every console child it starts —
`tailscale`, `ffmpeg`, `powershell` — would otherwise pop its own window. Route
all spawning through here and the user sees nothing.

It is also the only place that knows how to make a child die with its parent: the
painter holds ~13 GiB of VRAM, and an orphan of it keeps the card until the machine
is rebooted.
"""
from __future__ import annotations

import ctypes
import os
import subprocess

_JOB = None
_JOB_TRIED = False


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


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


def _job():
    """A kill-on-close job object, created once and shared by every child."""
    global _JOB, _JOB_TRIED
    if os.name != "nt":
        return None
    if _JOB_TRIED:
        return _JOB
    _JOB_TRIED = True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _ExtendedLimit()
        info.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(handle)
            return None
        _JOB = handle
    except Exception:
        _JOB = None
    return _JOB


def adopt(pid: int) -> bool:
    """Put a child in the job so the OS reaps it when this process goes away.

    `atexit` covers a clean Python exit; this covers the rest (a TerminateProcess,
    a harness job-object kill, the app being closed while a child is mid-render).
    """
    job = _job()
    if not job or not pid:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_TERMINATE = 0x0001
        PROCESS_SET_QUOTA = 0x0100
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, int(pid))
        if not handle:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(job, handle))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def spawn(command, **kwargs):
    """Popen that also registers the child with the job object."""
    process = popen(command, **kwargs)
    adopt(process.pid)
    return process
