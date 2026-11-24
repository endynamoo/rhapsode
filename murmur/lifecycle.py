"""Give the models back.

Two things hold memory in this app and neither one is freed by the Python garbage
collector at the moment a user quits:

* the voice model (Kokoro, ~340 MB plus ONNX arenas) inside this process;
* the stills painter, a child process holding ~13 GiB of VRAM for the whole session.

The child used to be the only long-lived one that was not in the kill-on-close job
object, so quitting the app any way other than a clean interpreter exit left the card
pinned. This module is the one place that releases both, and it is called from three
places: the lifespan's shutdown (uvicorn stopping), the `/api/quit` route (browser
mode), and the desktop entry after its window closes.
"""
from __future__ import annotations

import os
import sys
import threading
import time

from . import config, engine, imaging, localmt, search, store

_RELEASED = threading.Event()


def release(reason: str = "quit") -> dict:
    """Stop the painter and drop the models. Returns what happened, per step.

    Every step is best effort: a quit must not fail because one subsystem already
    died, and the caller may be shutting down a partly broken process.
    """
    steps: dict[str, str] = {}

    def step(name: str, action) -> None:
        try:
            action()
            steps[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            steps[name] = f"{type(exc).__name__}: {exc}"

    step("painter", imaging.shutdown)
    step("translation", localmt.stop)
    step("voice", engine.engine.unload)
    step("search", lambda: search.stop(timeout=2.0))
    step("store", store.close)
    _RELEASED.set()
    _log(reason, steps)
    return steps


def released() -> bool:
    return _RELEASED.is_set()


def quit_process(reason: str = "quit", exit_code: int = 0) -> None:
    """Release everything, then end the process.

    `os._exit` rather than a normal return: the server runs in a daemon thread under
    the desktop window, and a plain return from a request handler leaves the window
    (and the process) alive. Nothing is left to flush - the caches are files and the
    database is closed above - so skipping atexit is the point, not a shortcut.
    """
    steps = release(reason)
    sys.stdout.flush()
    try:
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 - windowed builds may have no stderr
        pass
    _log(f"{reason}: exiting after {steps}", None)
    os._exit(exit_code)


def _log(reason: str, steps: dict[str, str] | None) -> None:
    """One line in the data directory, because a windowed build has no console."""
    detail = "" if steps is None else " " + ", ".join(f"{k}={v}" for k, v in steps.items())
    try:
        with open(config.HOME / "app.log", "a", encoding="utf-8") as handle:
            handle.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} {reason}{detail} ===\n")
    except OSError:
        pass
