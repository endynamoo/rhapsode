"""Standalone Murmur engine sidecar.

Run with:

    python -m murmur.engine_api

It exposes the same film API as the Rhapsode server, without the UI/static
routes. The desktop app can keep using the in-process router, or other clients
can point at this service.
"""
from __future__ import annotations

import os

from fastapi import FastAPI

from . import __version__, film, ocr, store
from .backends import detect
from .engine import cache_stats, engine
from .film_api import router as film_router

app = FastAPI(title="Murmur Engine", docs_url=None, redoc_url=None)
app.include_router(film_router)


@app.on_event("startup")
def _startup() -> None:
    store.init()


def _health() -> dict:
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
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
