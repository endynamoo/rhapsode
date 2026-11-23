"""FastAPI backend for Murmur.

The app, the access guard, audio export and the static mounts. Documents live in
`library_api.py`; health, settings and the event bus in `system_api.py`; films and
search in `film_api.py` and `search_api.py`.
"""
from __future__ import annotations

import contextlib
import re
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import access, config, netinfo, proc, search, speak, store
from .engine import engine, prune_cache
from .film_api import router as film_router
from .library_api import router as library_router
from .search_api import router as search_router
from .system_api import router as system_router
from .voices import DEFAULT_VOICE


from . import lifecycle


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Startup work, run once before the app serves the first request.

    This replaces the `on_event("startup")` hook: the same calls in the same
    order, on the mechanism FastAPI still supports.
    """
    store.init()
    engine.warm_async()
    threading.Thread(target=_maintenance, name="cache-prune", daemon=True).start()
    # Search indexing is a background nicety: the reader must be usable before
    # the first chunk is written, so this only wakes a worker after startup.
    search.start_background(delay=2.0)
    if PUBLIC:
        print("Rhapsode is available on the network:")
        for item in access.access_urls():
            if item["label"] != netinfo.LOCAL_LABEL:
                print(f"  {item['label']}: {item['url']}")
        print(f"  token file: {config.HOME / 'access_token'}", flush=True)
    yield
    # uvicorn is stopping: stop the painter and drop the voice model here, so the
    # VRAM is free the moment the app looks closed rather than whenever the
    # interpreter gets around to it.
    lifecycle.release("server stopped")


app = FastAPI(title="Rhapsode", docs_url=None, redoc_url=None, lifespan=_lifespan)
app.include_router(film_router)
app.include_router(search_router)
app.include_router(system_router)
app.include_router(library_router)

# Network exposure. Loopback requests (the desktop window, Quick Read) are always
# allowed; other devices must present the token once.
PUBLIC = access.PUBLIC
TOKEN = access.TOKEN


def _is_local(request: Request) -> bool:
    return access.is_local(request)


def _local_host_ok(request: Request) -> bool:
    return access.local_host_ok(request)


def _matches(value: str, expected: str) -> bool:
    return access.matches(value, expected)


@app.middleware("http")
async def access_guard(request: Request, call_next):
    local = _is_local(request)
    if local and not _local_host_ok(request):
        return JSONResponse({"detail": "Unexpected host header."}, status_code=403)
    if not PUBLIC or local:
        return await call_next(request)

    if _matches(request.cookies.get("murmur_token", ""), TOKEN):
        return await call_next(request)

    supplied = request.query_params.get("token", "")
    if _matches(supplied, TOKEN):
        response = RedirectResponse(url=request.url.path or "/", status_code=303)
        response.set_cookie(
            "murmur_token",
            TOKEN,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
        )
        return response

    return JSONResponse(
        {"detail": "Rhapsode is private. Open the link containing ?token=… to continue."},
        status_code=401,
    )


# ---------------------------------------------------------------- startup
def _prune_exports(max_age_days: int = 7) -> int:
    """Remove finished export files that were never downloaded."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for path in config.EXPORT_DIR.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _maintenance() -> None:
    _prune_exports()
    prune_cache()


# ---------------------------------------------------------------- export
JOBS: dict[str, dict] = {}
_EXPORT_LOCK = threading.Lock()


def _export_worker(job_id: str, doc: dict, voice: str) -> None:
    job = JOBS[job_id]
    job["state"] = "running"
    units = doc["units"]
    job["total"] = len(units)
    try:
        import soundfile as sf

        settings = store.get_settings()
        translate_on = bool(settings.get("translateToVoice", True))
        pieces: list[np.ndarray] = []
        sample_rate = config.SAMPLE_RATE
        for index, unit in enumerate(units):
            prepared = speak.prepare(unit, voice, translate_on)
            meta = engine.synthesize(prepared["text"], voice)
            data, sample_rate = sf.read(meta["path"], dtype="float32")
            pieces.append(np.asarray(data))
            job["done"] = index + 1
            if job.get("cancelled"):
                job["state"] = "cancelled"
                return
        audio = np.concatenate(pieces) if pieces else np.zeros(1, dtype="float32")
        wav_path = config.EXPORT_DIR / f"{job_id}.wav"
        mp3_path = config.EXPORT_DIR / f"{job_id}.mp3"
        sf.write(wav_path, audio, sample_rate, subtype="PCM_16")

        ffmpeg = config.find_tool("ffmpeg")
        if ffmpeg:
            proc.run(
                [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav_path),
                 "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path)],
                check=True,
            )
            wav_path.unlink(missing_ok=True)
            job["file"] = str(mp3_path)
            job["mime"] = "audio/mpeg"
            job["ext"] = "mp3"
        else:
            job["file"] = str(wav_path)
            job["mime"] = "audio/wav"
            job["ext"] = "wav"
        job["state"] = "done"
    except Exception as exc:  # pragma: no cover
        job["state"] = "error"
        job["error"] = str(exc)


@app.post("/api/docs/{doc_id}/export")
def start_export(doc_id: str, payload: dict | None = None) -> dict:
    doc = store.get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    voice = (payload or {}).get("voice") or doc.get("voice") or DEFAULT_VOICE
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "id": job_id,
        "state": "queued",
        "done": 0,
        "total": len(doc["units"]),
        "title": doc["title"],
        "docId": doc_id,
        "suggestedName": export_name({"title": doc["title"], "ext": "mp3"}),
    }
    def _run() -> None:
        _EXPORT_LOCK.acquire()
        try:
            _export_worker(job_id, doc, voice)
        finally:
            _EXPORT_LOCK.release()

    threading.Thread(target=_run, daemon=True).start()
    return JOBS[job_id]


@app.get("/api/export/{job_id}")
def export_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export not found")
    return job


def export_name(job: dict) -> str:
    """The filename an export should be saved as, built from the book's title."""
    safe = re.sub(r"[^\w\s-]", "", job.get("title", "murmur"))[:80].strip() or "murmur"
    return f"{safe}.{job.get('ext', 'mp3')}"


@app.get("/api/export/{job_id}/file")
def export_file(job_id: str, consume: bool = False):
    """Serve the finished audio.

    `consume=1` is sent by a client that has already put the bytes where the
    reader asked for them: the copy under the data directory is then removed, so an
    export does not quietly leave a second file on disk.
    """
    job = JOBS.get(job_id)
    if not job or job.get("state") != "done":
        raise HTTPException(status_code=404, detail="Export is not ready")
    path = Path(job["file"])
    if not path.exists():
        raise HTTPException(status_code=404)
    response = FileResponse(
        path, media_type=job.get("mime", "audio/mpeg"), filename=export_name(job)
    )
    if consume:
        def _drop() -> None:
            try:
                path.unlink(missing_ok=True)
            finally:
                job["consumed"] = True

        response.background = BackgroundTask(_drop)
    return response


@app.delete("/api/export/{job_id}")
def cancel_export(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job:
        job["cancelled"] = True
    return {"ok": True}


# ---------------------------------------------------------------- static
class RevalidatingStaticFiles(StaticFiles):
    """Serve assets that a browser must revalidate before using.

    Without a Cache-Control header the browser applies its own freshness
    heuristic to `/assets/*`, and a module it already holds will be run again
    after a reload - so an updated `app.js` or view stays invisible to a source
    install long after the file on disk changed (this cost a round of
    "the fix is not there" during 0.3.0-beta). A local file answers a
    conditional request with 304 in well under a millisecond, so revalidating
    every load is the cheaper mistake.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount(
    "/assets",
    RevalidatingStaticFiles(directory=str(config.WEB_DIR)),
    name="assets",
)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        config.WEB_DIR / "index.html",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; media-src 'self' blob:; "
                "connect-src 'self'; frame-src 'self'; "
                "object-src 'none'; base-uri 'none'; form-action 'self'; "
                "frame-ancestors 'none'"
            ),
        },
    )


@app.get("/favicon.ico")
def favicon():
    return JSONResponse({}, status_code=204)
