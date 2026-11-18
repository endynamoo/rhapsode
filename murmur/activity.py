"""What the stills engine is doing right now, in one object every surface reads.

Cinema used to say "Painting scene..." for the whole pipeline. Behind that label
sit prompt composition, an optional character reference, a cold model load, the
diffusion pass, gutter and lettering cleanup, the polish redraw and, when no
backend exists, a placeholder. A cold load can outlast the paint, yet the UI
called all of it painting, so the two surfaces that describe the engine
(Cinema and Settings -> Engine) disagreed.

This module is the single vocabulary. `film.py` and `imaging.py` report into it
where the work actually happens; the API returns it; the UI renders it. Nothing
infers a phase in JavaScript, and per-step diffusion progress is deliberately
absent: no backend reports step counts, so the bar is indeterminate rather than
a fabricated percentage.
"""
from __future__ import annotations

import threading
import time

# Phases a still passes through. "queued" is derived by the API from the
# painter's queue, not written here, and "idle" is the initial state.
PHASES = (
    "idle",
    "planning",
    "reference",
    "loading",
    "painting",
    "cleanup",
    "polishing",
    "ready",
    "placeholder",
    "failed",
)

# Phases where one still is still being decided: the UI keeps the scene
# indicator up and shows an indeterminate bar.
BUSY = frozenset({"planning", "reference", "loading", "painting", "cleanup", "polishing"})

_PLACEHOLDER_DETAIL = (
    "No stills engine on this machine, so this scene is a placeholder. "
    "Install the film requirements, or pick a profile with a supported GPU."
)


def _now() -> float:
    return round(time.time(), 3)


class SceneActivity:
    """Thread-safe record of the still being painted.

    One painter runs per process, so one record is enough; a new `begin()` is
    the start of the next still and intentionally drops the previous error.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = {
            "phase": "idle",
            "detail": "",
            "doc": None,
            "chapter": None,
            "scene": None,
            "backend": None,
            "model": None,
            "profile": None,
            "coldStart": False,
            "error": None,
            "startedAt": None,
            "updatedAt": None,
        }

    def _write(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)
            self._state["updatedAt"] = _now()

    def begin(self, doc: str, chapter: int, scene: int, *,
              model: str | None = None, backend: str | None = None,
              profile: str | None = None) -> None:
        """Start of a still: composes the prompt before anything is painted."""
        self._write(
            phase="planning",
            detail="Composing the scene prompt",
            doc=doc,
            chapter=chapter,
            scene=scene,
            backend=backend,
            model=model,
            profile=profile,
            coldStart=False,
            error=None,
            startedAt=_now(),
        )

    def phase(self, phase: str, detail: str = "", **fields) -> None:
        """Move to a named phase, keeping every field not passed in."""
        if phase not in PHASES:
            raise ValueError(f"unknown scene phase {phase!r}")
        self._write(phase=phase, detail=detail, **fields)

    def finish(self, phase: str, detail: str = "", error: str | None = None) -> None:
        """Terminal state for this still: ready, placeholder or failed."""
        if phase not in {"ready", "placeholder", "failed"}:
            raise ValueError(f"not a terminal scene phase: {phase!r}")
        self._write(phase=phase, detail=detail, error=error, coldStart=False)

    def snapshot(self) -> dict:
        with self._lock:
            state = dict(self._state)
        state["busy"] = state["phase"] in BUSY
        # Honest about what the backend can tell us: never a fake percentage.
        state["determinate"] = False
        return state


activity = SceneActivity()


def placeholder_detail() -> str:
    return _PLACEHOLDER_DETAIL
