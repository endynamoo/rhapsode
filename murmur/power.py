"""Keep Windows from sleeping while a long GPU job runs.

A chapter film holds the GPU for ~30 minutes. If Windows decides to sleep mid-run,
the power transition can fail inside a USB hub driver — this box bugchecked with
0x9F_3_IMAGE_UsbHub3.sys at 11:50 while a chapter was painting — and the app dies
with it. The set/clear pair below keeps the system and display awake only for the
duration of the work.
"""
from __future__ import annotations

import contextlib
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


@contextlib.contextmanager
def keep_awake():
    """Hold the machine awake for the block; harmless elsewhere."""
    if not sys.platform.startswith("win"):
        yield
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        )
    except Exception:
        yield
        return
    try:
        yield
    finally:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass
