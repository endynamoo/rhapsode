"""System routes: health, engine profile, settings, speech, and the event bus.

These are the routes that describe or configure the server itself rather than
touch a document. They were the first `/api` block in `server.py`; moving them
here leaves that module as the app factory, the access guard, the document
routes and the static mounts.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import (
    access,
    bootstrap,
    config,
    extract,
    fonts,
    imaging,
    langs,
    lifecycle,
    netinfo,
    ocr,
    polish,
    search,
    speak,
    store,
    translate,
)
from .backends import detect
from .engine import cache_stats, clip_cache_stats, engine, prune_cache
from .voices import DEFAULT_VOICE, VOICES, voice_groups

router = APIRouter()


class TTSIn(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE


# ---------------------------------------------------------------- health
@router.post("/api/voice-model", status_code=202)
def repair_voice_model() -> dict:
    """Download the voice weights again, in the background.

    Reached from the error that names the cause: a truncated or placeholder
    `kokoro-v1.0.onnx` used to leave an install broken with no way back short of
    hand-deleting files.
    """
    if config.voice_model_ready():
        return {"started": False, "state": bootstrap.state()}
    try:
        state = bootstrap.start_download()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"started": True, "state": state}


@router.post("/api/quit")
def quit_app(request: Request) -> dict:
    """Stop Rhapsode and give the models back.

    Needed by browser mode, which has no window to close: without it, closing the tab
    left the server, the voice model and (if Cinema had been opened) the painter
    running until the machine was restarted. Loopback only - a device on the network
    can never stop the app.
    """
    if not access.is_local(request) or not access.local_host_ok(request):
        raise HTTPException(
            status_code=403, detail="Rhapsode can only be quit from this computer."
        )
    threading.Thread(target=lifecycle.quit_process, kwargs={"reason": "quit button"},
                     name="quit", daemon=True).start()
    return {"quitting": True}


@router.get("/api/health")
def health() -> dict:
    engine.recheck()
    return {
        "ok": True,
        "modelReady": engine.ready,
        "modelError": engine.last_error,
        # Why the weights cannot be used, and what a repair is doing: a truncated
        # file used to be invisible until ONNX Runtime threw.
        "modelProblem": config.voice_model_problem(),
        "modelDownload": bootstrap.state(),
        "voices": len(VOICES),
        "ocr": ocr.available(),
        "ocrBackend": ocr.backend_name(),
        "translate": translate.translator.available(),
        # Which voices can actually be spoken here, and why not when they cannot.
        "languages": {code: langs.support(code) for code in langs.SPECIAL},
        "profile": {
            "name": config.PROFILE.get("profile"),
            "summary": __import__("murmur.profile", fromlist=["describe"]).describe(config.PROFILE),
        },
        "polish": polish.status(),
        "painter": imaging.painter_info(),
        "caches": {
            "clips": {**cache_stats(), "hot": clip_cache_stats()},
            "translations": store._translations.stats(),
        },
        "platform": detect.snapshot(),
        "cache": cache_stats(),
    }


# ---------------------------------------------------------------- engine profile
class EngineProfileIn(BaseModel):
    profile: str
    precision: str | None = None
    text_device: str | None = None
    reserve_mib: int | None = None
    quality: str | None = None
    polish: bool | None = None


def _engine_state() -> dict:
    from . import profile as engine_profile

    active = config.PROFILE
    return {
        "profile": active.get("profile"),
        "summary": engine_profile.describe(active),
        "settings": {
            key: active.get(key)
            for key in ("precision", "text_device", "reserve_mib", "quality", "polish")
        },
        "profiles": [
            {
                "name": name,
                "why": preset.get("why", ""),
                "precision": preset.get("precision"),
                "text_device": preset.get("text_device"),
                "quality": preset.get("quality"),
                "polish": preset.get("polish"),
            }
            for name, preset in engine_profile.PROFILES.items()
        ],
        "painter": imaging.painter_info(),
    }


@router.get("/api/engine")
def read_engine() -> dict:
    """Which profile is active, what it means, and what the painter is doing."""
    return _engine_state()


@router.post("/api/engine/profile")
def write_engine_profile(payload: EngineProfileIn) -> dict:
    """Switch profile: save it, seed the reading defaults, restart the painter."""
    from . import profile as engine_profile

    if payload.profile not in engine_profile.PROFILES:
        raise HTTPException(status_code=400, detail=f"unknown profile {payload.profile}")
    engine_profile.activate(
        config.HOME,
        payload.profile,
        precision=payload.precision,
        text_device=payload.text_device,
        reserve_mib=payload.reserve_mib,
        quality=payload.quality,
        polish=payload.polish,
    )
    imaging.restart_painter()
    return _engine_state()


@router.post("/api/engine/restart")
def restart_engine() -> dict:
    """Drop the resident painter so the next still reloads the active profile."""
    return {"painter": imaging.restart_painter()}


# ---------------------------------------------------------------- network and settings
@router.get("/api/network")
def network_info(request: Request, response: Response) -> dict:
    """Connection details, including the token — only for local callers."""
    if not access.is_local(request):
        raise HTTPException(status_code=403, detail="Local access only")
    response.headers["Cache-Control"] = "no-store"
    settings = store.get_settings()
    urls = access.access_urls()
    if not access.PUBLIC:
        # Avoid advertising LAN/Tailscale addresses while remote access is off.
        urls = [item for item in urls if item["label"] == netinfo.LOCAL_LABEL]
    return {
        "enabled": access.PUBLIC,
        "host": config.HOST,
        "port": config.PORT,
        "token": access.TOKEN if access.PUBLIC else "",
        "remoteAccess": bool(settings.get("remoteAccess")),
        "tailscale": netinfo.tailscale_state(),
        "urls": urls,
    }


@router.get("/api/voices")
def voices() -> dict:
    return {"groups": voice_groups(), "default": DEFAULT_VOICE}


@router.get("/api/fonts")
def font_list() -> dict:
    """Font families installed on this machine (cached for a week)."""
    return {"fonts": fonts.system_fonts()}


@router.get("/api/settings")
def read_settings() -> dict:
    return store.get_settings()


@router.put("/api/settings")
def write_settings(patch: dict) -> dict:
    return store.set_settings(patch)


@router.post("/api/cache/prune")
def prune() -> dict:
    removed = prune_cache()
    return {"removed": removed, **cache_stats()}


# ---------------------------------------------------------------- speech
@router.post("/api/tts")
def synthesize(payload: TTSIn) -> dict:
    voice = payload.voice if payload.voice in VOICES else DEFAULT_VOICE
    settings = store.get_settings()
    prepared = speak.prepare(
        payload.text,
        voice,
        bool(settings.get("translateToVoice", True)),
    )
    try:
        meta = engine.synthesize(prepared["text"], voice)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {exc}") from exc
    return {
        "key": meta["key"],
        "url": f"/api/audio/{meta['key']}.wav",
        "duration": meta["duration"],
        "sampleRate": meta["sample_rate"],
        "words": meta["words"],
        "spoken": prepared["text"],
        "translated": prepared["translated"],
        "sourceLang": prepared["source"],
        "targetLang": prepared["target"],
        "translateError": prepared["error"],
    }


@router.get("/api/audio/{name}")
def audio(name: str, request: Request):
    if not re.fullmatch(r"[0-9a-f]{8,40}\.wav", name):
        raise HTTPException(status_code=404)
    path = config.CACHE_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")

    size = path.stat().st_size
    range_header = request.headers.get("range")
    base_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000, immutable",
    }

    if not range_header:
        return FileResponse(path, media_type="audio/wav", headers=base_headers)

    match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not match:
        raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    start = int(match.group(1)) if match.group(1) else 0
    end = int(match.group(2)) if match.group(2) else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    length = end - start + 1

    def stream():
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        **base_headers,
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Content-Length": str(length),
    }
    return StreamingResponse(stream(), status_code=206, media_type="audio/wav", headers=headers)


# ---------------------------------------------------------------- event bus
_subscribers: set[asyncio.Queue] = set()


def broadcast(message: dict) -> bool:
    """Push a message to every open tab. Returns whether any tab got it."""
    delivered = False
    for queue in list(_subscribers):
        try:
            queue.put_nowait(message)
            delivered = True
        except Exception:
            _subscribers.discard(queue)
    return delivered


@router.get("/api/events")
async def events():
    """Server-sent events so an open tab can pick up quick-read requests."""
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.add(queue)

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _subscribers.discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- clipboard and quick read
@router.post("/api/quick-read")
async def quick_read(request: Request) -> dict:
    raw = (await request.body()).decode("utf-8", errors="replace").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="No text was provided.")
    lines = [line for line in raw.splitlines() if line.strip()]
    first = re.sub(r"\s+", " ", lines[0])[:70] if lines else "Quick read"
    title = first or "Quick read"
    if len(raw) > 80:
        title += "…"
    try:
        doc = store.create_doc(title, raw, kind="quick")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    search.note_document(doc["id"])
    return {"id": doc["id"], "delivered": broadcast({"type": "quick", "id": doc["id"]})}


@router.post("/api/ocr-clipboard")
async def ocr_clipboard() -> dict:
    """OCR the image on the clipboard and queue it for reading."""
    if not ocr.available():
        raise HTTPException(
            status_code=503,
            detail="OCR is unavailable — on macOS install pyobjc-framework-Vision; on Linux/Windows install Tesseract.",
        )
    data = ocr.clipboard_image()
    if not data:
        raise HTTPException(status_code=400, detail="There is no image on the clipboard.")
    try:
        raw = ocr.recognize_bytes(data)
    except ocr.OCRUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    text = extract.clean_ocr(raw)
    if not text.strip():
        raise HTTPException(status_code=400, detail="No readable text was found in that image.")

    first = next(
        (re.sub(r"\s+", " ", line).strip() for line in raw.splitlines() if line.strip()), ""
    )
    title = f"Screenshot — {first[:50]}…" if first else "Screenshot"
    try:
        doc = store.create_doc(title[:120], text, kind="ocr")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    search.note_document(doc["id"])
    return {
        "id": doc["id"],
        "chars": len(text),
        "delivered": broadcast({"type": "quick", "id": doc["id"]}),
    }
