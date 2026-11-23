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


class _IoCounters(ctypes.Structure):
    """IO_COUNTERS: six ULONGLONGs. Omitting it made the struct 64 bytes where the API
    requires 112, so SetInformationJobObject failed with ERROR_BAD_LENGTH and the job
    was never created - which meant no child was ever adopted, silently."""

    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimit(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION, field for field."""

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
        ("IoInfo", _IoCounters),
        # Without these four the struct is 112 bytes, the API wants 144, and
        # SetInformationJobObject answers ERROR_BAD_LENGTH (24).
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32():
    """kernel32 with the prototypes defined.

    Without an explicit restype ctypes marshals a HANDLE as a 32-bit int, so the
    64-bit job handle was truncated on x64 and every call that used it failed.
    """
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    library.CreateJobObjectW.restype = ctypes.c_void_p
    library.SetInformationJobObject.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.SetInformationJobObject.restype = ctypes.c_int
    library.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.AssignProcessToJobObject.restype = ctypes.c_int
    library.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    library.IsProcessInJob.restype = ctypes.c_int
    library.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    library.OpenProcess.restype = ctypes.c_void_p
    library.CloseHandle.argtypes = [ctypes.c_void_p]
    library.CloseHandle.restype = ctypes.c_int
    return library


def _job():
    """A kill-on-close job object, created once and shared by every child."""
    global _JOB, _JOB_TRIED
    if os.name != "nt":
        return None
    if _JOB_TRIED:
        return _JOB
    _JOB_TRIED = True
    try:
        kernel32 = _kernel32()
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _ExtendedLimit()
        info.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
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
        kernel32 = _kernel32()
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


def in_job(pid: int) -> bool:
    """Is this process in our kill-on-close job? For tests and diagnostics."""
    job = _job()
    if not job or not pid:
        return False
    try:
        kernel32 = _kernel32()
        # PROCESS_QUERY_LIMITED_INFORMATION is enough for IsProcessInJob and needs no
        # elevated rights.
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            answer = ctypes.c_int(0)
            if not kernel32.IsProcessInJob(handle, job, ctypes.byref(answer)):
                return False
            return bool(answer.value)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def spawn(command, **kwargs):
    """Popen that also ties the child's lifetime to this process.

    Windows: the job object reaps it, even on TerminateProcess.
    POSIX: no equivalent survives SIGKILL, so two things happen instead - the child
    gets its own session (a group someone can kill deliberately, and it no longer dies
    of a Ctrl+C sent to the terminal), and it is told who its parent is so it can
    watch for the parent disappearing. See `fluxgen.watch_parent`.
    """
    if os.name != "nt":
        kwargs.setdefault("start_new_session", True)
        environment = dict(kwargs.get("env") or os.environ)
        environment.setdefault("MURMUR_PARENT_PID", str(os.getpid()))
        kwargs["env"] = environment
    process = popen(command, **kwargs)
    adopt(process.pid)
    return process
