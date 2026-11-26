"""Standalone Murmur engine sidecar.

Run with:

    python -m murmur.engine_api

It exposes the same film API as the Rhapsode server, without the UI/static
routes. The desktop app can keep using the in-process router, or other clients
can point at this service.
"""
from __future__ import annotations

import contextlib
import os

from fastapi import FastAPI

from . import __version__, film, ocr, store
from .backends import detect
from .engine import cache_stats, engine
from .film_api import router as film_router


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """The sidecar has one startup task: make sure the store exists."""
    store.init()
    yield


app = FastAPI(title="Rhapsode Engine", docs_url=None, redoc_url=None, lifespan=_lifespan)
app.include_router(film_router)


def _health() -> dict:
    engine.recheck()
    return {
        "ok": True,
        "name": "murmur-engine",
        "version": __version__,
        "modelReady": engine.ready,
        "modelError": engine.last_error,
        "ocr": ocr.available(),
        "ocrBackend": ocr.backend_name(),
        "platform": detect.snapshot(),
        "cache": cache_stats(),
        "films": len(film.JOBS),
    }


@app.get("/health")
@app.get("/api/engine/health")
def health() -> dict:
    return _health()


@app.get("/version")
@app.get("/api/engine/version")
def version() -> dict:
    return {
        "name": "murmur-engine",
        "version": __version__,
        "api": 1,
        "film": True,
    }


def main() -> None:
    import uvicorn

    port = int(os.environ.get("MURMUR_ENGINE_PORT", "8931"))
    host = os.environ.get("MURMUR_ENGINE_HOST", "127.0.0.1")
    # This sidecar has no token guard of its own (the main server's middleware is not
    # mounted here), and it can paint and delete film files. A non-loopback bind would
    # put those routes on the LAN unauthenticated, so it is refused rather than warned
    # about.
    if host.strip().lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            "murmur.engine_api only listens on loopback: it has no authentication, "
            "so binding it to another interface would expose the film routes to the "
            "network. Use the main app for remote access."
        )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
