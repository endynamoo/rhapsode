"""System routes: health, engine profile, settings, speech, and the event bus.

These are the routes that describe or configure the server itself rather than
touch a document. They were the first `/api` block in `server.py`; moving them
here leaves that module as the app factory, the access guard, the document
routes and the static mounts.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import (
    access,
    bootstrap,
    config,
    enginesetup,
    extract,
    ffmpeg,
    fonts,
    imaging,
    langs,
    lifecycle,
    localmt,
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
class TranslateIn(BaseModel):
    url: str | None = None


class TranslateModelIn(BaseModel):
    variant: str | None = None
    download: bool = False
    stop: bool = False


@router.post("/api/translate/model", status_code=202)
def translation_model(payload: TranslateModelIn | None = None) -> dict:
    """Fetch the on-device translator (llama.cpp + a GGUF model), or report its state.

    A reader should not have to install a model server to hear a foreign book. This is the
    one call that fetches what that needs - the runtime, the weights and the model's
    licence - and it is only made when someone asked (the first-run checkbox, or Settings).
    """
    data = payload or TranslateModelIn()
    if data.stop:
        localmt.stop()
        return localmt.status()
    if data.variant:
        try:
            store.set_settings({"translateVariant": data.variant})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data.download:
        try:
            localmt.start_download(data.variant)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return localmt.status()


@router.get("/api/translate")
def translate_status(probe: bool = False) -> dict:
    """What translation can do here: which endpoint, whether it answers, what it offers.

    `?probe=1` scans the usual local ports instead of trusting the discovery cache.
    """
    return translate.translator.status(probe=probe)


@router.post("/api/translate")
def connect_translation(payload: TranslateIn) -> dict:
    """Save the endpoint the reader picked, after checking that it actually answers.

    Storing an address that does not work is how a reader ends up with a promise the app
    cannot keep, so the check happens here rather than at the first foreign sentence.
    """
    url = (payload.url or "").strip()
    if not url:
        store.set_settings({"translateUrl": ""})
        translate.reset_discovery()
        return translate.translator.status(probe=True)
    checked = translate.check_url(url)
    if not checked["ok"]:
        raise HTTPException(status_code=400, detail=checked["reason"])
    store.set_settings({"translateUrl": checked["url"]})
    translate.reset_discovery()
    status = translate.translator.status(probe=True)
    status["connected"] = {"url": checked["url"], "model": checked["model"]}
    return status


@router.get("/api/voice-model")
def voice_model_status() -> dict:
    """Cheap poll for the first-run screen: is the voice ready, and how far is the download.

    `/api/health` answers a dozen subsystems and re-checks the engine; a progress bar that
    polls once a second should not do that.
    """
    return {"ready": config.voice_model_ready(), "state": bootstrap.state()}


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
        # A stranger's install has no weights. The UI uses this to prepare the voice before
        # offering a play button; MURMUR_FIRST_RUN=0 keeps the old behaviour (tests, and
        # anybody who would rather meet it in Settings).
        "firstRun": {
            # "Are the weights on disk" - engine.ready only means "loaded into memory", and
            # is false on a healthy install until the first sentence is spoken.
            "needed": bool(config.voice_model_problem()),
            "auto": os.environ.get("MURMUR_FIRST_RUN", "1") != "0",
        },
        "voices": len(VOICES),
        "ocr": ocr.available(),
        "ocrBackend": ocr.backend_name(),
        # MP3 export falls back to WAV and chapter films need it, so say whether this machine
        # has one instead of letting the failure surface only when someone presses Export.
        "ffmpeg": bool(config.find_tool("ffmpeg")),
        "translate": translate.translator.available(),
        # Which voices can actually be spoken here, and why not when they cannot.
        "languages": {code: langs.support(code) for code in langs.SPECIAL},
        "profile": {
            "name": config.PROFILE.get("profile"),
            # Set once, on the start after a saved profile this build could not honour:
            # the UI can say "still images were off because of an old CPU profile".
            "reconciled": dict(__import__("murmur.profile", fromlist=["RECONCILED"]).RECONCILED),
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
        "reconciled": dict(engine_profile.RECONCILED),
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
        "setup": enginesetup.status(),
    }


@router.get("/api/engine")
def read_engine() -> dict:
    """Which profile is active, what it means, and what the painter is doing."""
    return _engine_state()


@router.post("/api/engine/setup", status_code=202)
def setup_engine() -> dict:
    """Install the stills engine now, from inside the app.

    Runs the same script the installer's optional task runs: torch for this card's vendor,
    diffusers, the q4 transformer, the text encoder, then the profile. Only started when
    someone asked (the first-run card or Settings), and never on a machine with no GPU
    profile - a placeholder is the correct answer there, not a 16 GB download.
    """
    active = config.PROFILE or {}
    if active.get("profile") in {None, "", "cpu"}:
        raise HTTPException(
            status_code=400,
            detail="This machine has no usable GPU for stills; the placeholder painter is "
                   "what Cinema uses here (CPU painting is planned for 0.4).",
        )
    return enginesetup.start()


@router.get("/api/ffmpeg")
def ffmpeg_status() -> dict:
    """Whether this machine can cut MP3 exports and chapter films, and what it is using."""
    return ffmpeg.state()


@router.post("/api/ffmpeg", status_code=202)
def ffmpeg_download() -> dict:
    """Fetch the pinned GPL ffmpeg build into the data directory.

    gyan.dev's release essentials build: libmp3lame for the MP3 export, libass for the
    burned-in subtitles, libx264 for the H.264 chapter film. Its licence travels with it
    (licenses/SOURCES.md carries the offer and the source commit).
    """
    return ffmpeg.start_download()


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
    if match.group(1):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
    else:
        # `bytes=-500` asks for the LAST 500 bytes. An empty start used to become 0, so
        # the client got the first 501 bytes - a different part of the file, silently.
        suffix = int(match.group(2) or 0)
        if suffix <= 0:
            raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        start = max(0, size - suffix)
        end = size - 1
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


def _ocr_advice() -> str:
    """What to install here. Tesseract is the Linux answer, not the Windows one."""
    if sys.platform.startswith("win"):
        return (
            "OCR is unavailable: Windows needs the OCR language pack and the winsdk "
            "binding. Add a language in Settings > Time & language, then restart Rhapsode."
        )
    if sys.platform == "darwin":
        return "OCR is unavailable: install pyobjc-framework-Vision (uv pip install pyobjc-framework-Vision)."
    return "OCR is unavailable: install Tesseract (apt install tesseract-ocr)."


@router.post("/api/ocr-clipboard")
async def ocr_clipboard() -> dict:
    """OCR the image on the clipboard and queue it for reading."""
    if not ocr.available():
        raise HTTPException(status_code=503, detail=_ocr_advice())
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
