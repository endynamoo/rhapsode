"""FastAPI backend for Murmur."""
from __future__ import annotations

import asyncio
import hmac
import html
import ipaddress
import json
import mimetypes
import posixpath
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, extract, film, fonts, imaging, netinfo, ocr, polish, proc, search, speak, store, translate
from .backends import detect
from .engine import cache_stats, clip_cache_stats, engine, prune_cache
from .film_api import router as film_router
from .search_api import router as search_router
from .voices import DEFAULT_VOICE, VOICES, voice_groups

app = FastAPI(title="Murmur", docs_url=None, redoc_url=None)
# PDFium is not thread-safe; the page image and word-box endpoints share one lock.
_PDF_LOCK = threading.Lock()
app.include_router(film_router)
app.include_router(search_router)

MAX_UPLOAD = 120 * 1024 * 1024

# Network exposure. Loopback requests (the desktop window, Quick Read) are always
# allowed; other devices must present the token once.
PUBLIC = config.PUBLIC if config.PUBLIC is not None else (config.HOST not in config.LOOPBACK)
TOKEN = config.access_token() if PUBLIC else ""


def _client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def _is_local(request: Request) -> bool:
    host = _client_host(request)
    return host in config.LOOPBACK or host.startswith("127.")


def _local_host_ok(request: Request) -> bool:
    """Reject DNS-rebinding requests that reach the loopback server by name."""
    raw = request.headers.get("host", "")
    try:
        host = (urlsplit(f"//{raw}").hostname or "").lower()
    except ValueError:
        return False
    if host in config.LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _matches(value: str, expected: str) -> bool:
    try:
        return bool(expected) and hmac.compare_digest(value, expected)
    except TypeError:
        return False


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
        {"detail": "Murmur is private. Open the link containing ?token=… to continue."},
        status_code=401,
    )


# ---------------------------------------------------------------- models
class TextIn(BaseModel):
    title: str = ""
    text: str


class UrlIn(BaseModel):
    url: str
    title: str = ""


class TTSIn(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE


class DocPatch(BaseModel):
    title: str | None = None
    position: int | None = None
    voice: str | None = None
    speed: float | None = None


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


@app.on_event("startup")
def _startup() -> None:
    store.init()
    engine.warm_async()
    threading.Thread(target=_maintenance, name="cache-prune", daemon=True).start()
    # Search indexing is a background nicety: the reader must be usable before
    # the first chunk is written, so this only wakes a worker after startup.
    search.start_background(delay=2.0)
    if PUBLIC:
        print("Murmur is available on the network:")
        for item in netinfo.access_urls(config.PORT, TOKEN):
            if item["label"] != "This Mac":
                print(f"  {item['label']}: {item['url']}")
        print(f"  token file: {config.HOME / 'access_token'}", flush=True)


# ---------------------------------------------------------------- basics
@app.get("/api/health")
def health() -> dict:
    engine.recheck()
    return {
        "ok": True,
        "modelReady": engine.ready,
        "modelError": engine.last_error,
        "voices": len(VOICES),
        "ocr": ocr.available(),
        "ocrBackend": ocr.backend_name(),
        "translate": translate.translator.available(),
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


@app.get("/api/engine")
def read_engine() -> dict:
    """Which profile is active, what it means, and what the painter is doing."""
    return _engine_state()


@app.post("/api/engine/profile")
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


@app.post("/api/engine/restart")
def restart_engine() -> dict:
    """Drop the resident painter so the next still reloads the active profile."""
    return {"painter": imaging.restart_painter()}


@app.get("/api/network")
def network_info(request: Request, response: Response) -> dict:
    """Connection details, including the token — only for local callers."""
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="Local access only")
    response.headers["Cache-Control"] = "no-store"
    settings = store.get_settings()
    urls = netinfo.access_urls(config.PORT, TOKEN if PUBLIC else None)
    if not PUBLIC:
        # Avoid advertising LAN/Tailscale addresses while remote access is off.
        urls = [item for item in urls if item["label"] == "This Mac"]
    return {
        "enabled": PUBLIC,
        "host": config.HOST,
        "port": config.PORT,
        "token": TOKEN if PUBLIC else "",
        "remoteAccess": bool(settings.get("remoteAccess")),
        "tailscale": netinfo.tailscale_state(),
        "urls": urls,
    }


@app.get("/api/voices")
def voices() -> dict:
    return {"groups": voice_groups(), "default": DEFAULT_VOICE}


@app.get("/api/fonts")
def font_list() -> dict:
    """Font families installed on this machine (cached for a week)."""
    return {"fonts": fonts.system_fonts()}


@app.get("/api/settings")
def read_settings() -> dict:
    return store.get_settings()


@app.put("/api/settings")
def write_settings(patch: dict) -> dict:
    return store.set_settings(patch)


@app.post("/api/cache/prune")
def prune() -> dict:
    removed = prune_cache()
    return {"removed": removed, **cache_stats()}


# ---------------------------------------------------------------- speech
@app.post("/api/tts")
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


@app.get("/api/audio/{name}")
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


# ---------------------------------------------------------------- documents
@app.get("/api/docs")
def list_docs() -> dict:
    return {"docs": store.list_docs()}


@app.post("/api/docs")
def create_doc(payload: TextIn) -> dict:
    try:
        doc = store.create_doc(payload.title, payload.text, kind="text")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    search.note_document(doc["id"])
    return doc


@app.post("/api/docs/upload")
async def upload_doc(file: UploadFile = File(...)) -> dict:
    if file.size and file.size > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="That file is too large (120 MB limit).")
    chunks = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="That file is too large (120 MB limit).")
    data = bytes(chunks)

    filename = file.filename or "document"
    suffix = Path(filename).suffix.lower()
    try:
        title, body, meta = extract.extract_upload(filename, data)
    except extract.ExtractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ocr.OCRUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    units = meta.pop("units", None)
    unit_pages = meta.pop("unitPages", None)
    page_count = meta.pop("pageCount", 0)
    toc = meta.pop("toc", None)
    chapter_files = meta.pop("chapters", None)

    try:
        doc = store.create_doc(
            title, body,
            source=filename,
            kind=suffix.lstrip("."),
            units=units,
            unit_pages=unit_pages,
            page_count=page_count,
            toc=toc,
            chapter_files=chapter_files,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Keep the original so the reader can render the actual page or chapter.
    if suffix in {".pdf", ".epub"} or suffix in extract.IMAGE_SUFFIXES:
        stored = store.save_source(doc["id"], suffix, data)
        if stored:
            doc = store.update_doc(doc["id"], file=stored) or doc

    doc["ocr"] = meta
    search.note_document(doc["id"])
    return doc


@app.post("/api/docs/url")
def import_url(payload: UrlIn) -> dict:
    try:
        title, body, meta = extract.extract_url(payload.url)
    except extract.ExtractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for key in ("units", "unitPages", "pageCount", "toc", "chapters"):
        meta.pop(key, None)
    try:
        doc = store.create_doc(payload.title or title, body, source=payload.url, kind="url")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc["ocr"] = meta
    search.note_document(doc["id"])
    return doc


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: str) -> dict:
    doc = store.get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # Opening a book is reading activity: this is what "recently read" means.
    search.record_read(doc_id, doc.get("position") or 0)
    return doc


@app.get("/api/docs/{doc_id}/page/{page}/words")
def doc_page_words(doc_id: str, page: int):
    """Word boxes for a PDF page, normalized to the rendered page."""
    doc = store.get_doc(doc_id)
    if not doc or doc.get("viewer") != "pdf":
        raise HTTPException(status_code=404, detail="This document has no text boxes")
    total = doc.get("pageCount") or 0
    if page < 0 or page >= total:
        raise HTTPException(status_code=404, detail="Page out of range")

    source = store.doc_file(doc_id)
    if not source or not source.exists():
        raise HTTPException(status_code=404, detail="The original file is missing")

    fingerprint = {"size": source.stat().st_size, "mtime": int(source.stat().st_mtime)}
    cache = config.PAGES_DIR / f"{doc_id}_{page}_words.json"
    if cache.exists():
        try:
            cached = json.loads(cache.read_text("utf-8"))
            if cached.get("source") == fingerprint:
                return cached
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    with _PDF_LOCK:
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(source))
            if page >= len(pdf):
                pdf.close()
                raise HTTPException(status_code=404, detail="Page out of range")
            page_obj = pdf[page]
            width, height = page_obj.get_size()
            textpage = page_obj.get_textpage()
            try:
                text = textpage.get_text_range() or ""
                count = textpage.count_chars()
                words: list[dict] = []
                current: dict | None = None
                for index in range(count):
                    char = textpage.get_text_range(index, 1)
                    if not char:
                        continue
                    if char.isspace():
                        if current:
                            words.append(current)
                            current = None
                        continue
                    box = textpage.get_charbox(index)
                    if current is None:
                        current = {
                            "text": char,
                            "left": box[0],
                            "bottom": box[1],
                            "right": box[2],
                            "top": box[3],
                        }
                    else:
                        current["text"] += char
                        current["left"] = min(current["left"], box[0])
                        current["bottom"] = min(current["bottom"], box[1])
                        current["right"] = max(current["right"], box[2])
                        current["top"] = max(current["top"], box[3])
                if current:
                    words.append(current)
                payload = {
                    "page": page,
                    "width": width,
                    "height": height,
                    "text": text,
                    "source": fingerprint,
                    "words": [
                        {
                            "text": word["text"],
                            "x": round(word["left"] / width, 6),
                            "y": round((height - word["top"]) / height, 6),
                            "w": round((word["right"] - word["left"]) / width, 6),
                            "h": round((word["top"] - word["bottom"]) / height, 6),
                        }
                        for word in words
                    ],
                }
            finally:
                textpage.close()
                page_obj.close()
                pdf.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not read page words: {exc}") from exc

    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    tmp.replace(cache)
    return payload


@app.get("/api/docs/{doc_id}/page/{page}")
def doc_page(doc_id: str, page: int, dpi: int = 140):
    """Render one PDF page as PNG, cached on disk."""
    doc = store.get_doc(doc_id)
    if not doc or doc.get("viewer") != "pdf":
        raise HTTPException(status_code=404, detail="This document has no rendered pages")
    total = doc.get("pageCount") or 0
    if page < 0 or page >= total:
        raise HTTPException(status_code=404, detail="Page out of range")

    dpi = max(96, min(int(dpi), 240))
    cached = config.PAGES_DIR / f"{doc_id}_{page}_{dpi}.png"
    if not cached.exists():
        source = store.doc_file(doc_id)
        if not source or not source.exists():
            raise HTTPException(status_code=404, detail="The original file is missing")
        with _PDF_LOCK:
            try:
                import pypdfium2 as pdfium

                pdf = pdfium.PdfDocument(str(source))
                if page >= len(pdf):
                    pdf.close()
                    raise HTTPException(status_code=404, detail="Page out of range")
                bitmap = pdf[page].render(scale=dpi / 72.0)
                image = bitmap.to_pil().convert("RGB")
                pdf.close()
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Could not render that page: {exc}") from exc
        tmp = cached.with_suffix(".tmp")
        image.save(tmp, format="PNG")
        tmp.replace(cached)

    return FileResponse(
        cached,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/docs/{doc_id}/image")
def doc_image(doc_id: str):
    doc = store.get_doc(doc_id)
    if not doc or doc.get("viewer") != "image":
        raise HTTPException(status_code=404, detail="This document has no image")
    source = store.doc_file(doc_id)
    if not source or not source.exists():
        raise HTTPException(status_code=404, detail="The original file is missing")
    media = mimetypes.guess_type(source.name)[0] or "image/png"
    return FileResponse(
        source,
        media_type=media,
        headers={"Cache-Control": "public, max-age=86400"},
    )


CHAPTER_PALETTES = {
    "dark": {"text": "#ece9f8", "accent": "#8b7bff", "hl": "#8b7bff", "hl_ink": "#0d0724", "scheme": "dark"},
    "light": {"text": "#17151f", "accent": "#5a45d6", "hl": "#8b7bff", "hl_ink": "#0d0724", "scheme": "light"},
}

CHAPTER_FONTS = {
    "sans": '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif',
    "serif": '"New York", ui-serif, "Iowan Old Style", Palatino, Georgia, serif',
}


def _font_stack(font: str) -> str:
    if font in CHAPTER_FONTS:
        return CHAPTER_FONTS[font]
    family = re.sub(r"[^A-Za-z0-9 ._-]", "", font or "")[:80].strip()
    if not family:
        return CHAPTER_FONTS["sans"]
    return (
        f'"{family}", ui-sans-serif, -apple-system, BlinkMacSystemFont, '
        '"Segoe UI", Roboto, sans-serif'
    )


def _color(value: str, fallback: str) -> str:
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value or "") else fallback


@app.get("/api/docs/{doc_id}/epub/{asset:path}")
def epub_asset(doc_id: str, asset: str):
    """Serve images, CSS and fonts from inside the EPUB archive."""
    if ".." in asset or asset.startswith("/"):
        raise HTTPException(status_code=404)
    doc = store.get_doc(doc_id)
    if not doc or doc.get("viewer") != "epub":
        raise HTTPException(status_code=404)
    source = store.doc_file(doc_id)
    if not source or not source.exists():
        raise HTTPException(status_code=404, detail="The original file is missing")
    try:
        with zipfile.ZipFile(source) as archive:
            data = archive.read(asset)
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(status_code=404, detail="Asset not found") from exc
    media = mimetypes.guess_type(asset)[0] or "application/octet-stream"
    return Response(
        content=data,
        media_type=media,
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/docs/{doc_id}/chapter/{chapter}")
def doc_chapter(
    doc_id: str,
    chapter: int,
    theme: str = "dark",
    font: str = "sans",
    size: int = 19,
    ink: str = "",
    accent: str = "",
    hl: str = "",
    hl_ink: str = "",
):
    """Render one EPUB spine item as themed HTML for the document view."""
    doc = store.get_doc(doc_id)
    if not doc or doc.get("viewer") != "epub":
        raise HTTPException(status_code=404, detail="This document has no chapters")
    files = doc.get("chapterFiles") or []
    if chapter < 0 or chapter >= len(files):
        raise HTTPException(status_code=404, detail="Chapter out of range")

    source = store.doc_file(doc_id)
    if not source or not source.exists():
        raise HTTPException(status_code=404, detail="The original file is missing")

    from bs4 import BeautifulSoup

    href = files[chapter]
    try:
        with zipfile.ZipFile(source) as archive:
            raw = archive.read(href)
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(status_code=404, detail="Chapter not found") from exc

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "iframe", "object", "embed", "audio", "video", "base", "form", "meta"]):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            name = attr.lower()
            value = str(tag.get(attr) or "").strip()
            if name.startswith("on"):
                del tag[attr]
            elif name in {"href", "src", "xlink:href"} and value.lower().startswith(
                ("javascript:", "vbscript:", "data:text/html")
            ):
                del tag[attr]
            elif name in {"src", "poster"} and re.match(r"^(https?:)?//", value, re.I):
                # Do not let an EPUB report back to a remote server while reading.
                del tag[attr]

    def safe_css(text: str) -> str:
        text = re.sub(r"@import[^;]*;?", "", text, flags=re.I)
        text = re.sub(r"url\(\s*['\"]?https?://[^)]*\)", "none", text, flags=re.I)
        return text.replace("</style", "<\\/style")

    head_bits: list[str] = []
    for tag in soup.find_all("style"):
        head_bits.append(f"<style>{safe_css(tag.get_text())}</style>")
        tag.decompose()
    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel") or []).lower()
        href_attr = str(link.get("href") or "").strip()
        if "stylesheet" in rel and href_attr and not re.match(r"^[a-z]+:", href_attr, re.I):
            head_bits.append(
                f'<link rel="stylesheet" href="{html.escape(href_attr, quote=True)}">'
            )
        link.decompose()

    body = soup.body or soup
    inner = "".join(str(child) for child in body.contents)

    palette = CHAPTER_PALETTES.get(theme, CHAPTER_PALETTES["dark"])
    ink = _color(ink, palette["text"])
    accent = _color(accent, palette["accent"])
    hl = _color(hl, palette["hl"])
    hl_ink = _color(hl_ink, palette["hl_ink"])
    font_stack = _font_stack(font)
    size = max(15, min(int(size), 30))
    base = posixpath.dirname(href)
    base_url = f"/api/docs/{doc_id}/epub/{base}/" if base else f"/api/docs/{doc_id}/epub/"

    css = f"""
html {{ color-scheme: {palette['scheme']}; background: transparent !important; }}
html, body {{ background: transparent !important; }}
body {{
  margin: 0 auto !important;
  max-width: 66ch;
  padding: 26px 30px 110px !important;
  color: {ink} !important;
  font-family: {font_stack} !important;
  font-size: {size}px !important;
  line-height: 1.72 !important;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
}}
body p, body div, body span, body li, body td, body th, body blockquote {{ color: inherit; }}
body a {{ color: {accent} !important; }}
h1, h2, h3, h4, h5, h6 {{ color: {ink} !important; line-height: 1.25 !important; }}
h1 {{ font-size: 1.6em !important; margin: 1.1em 0 .6em !important; }}
h2 {{ font-size: 1.35em !important; margin: 1.2em 0 .5em !important; }}
p {{ margin: 0 0 1em !important; }}
img, svg, video {{ max-width: 100% !important; height: auto !important; }}
table {{ max-width: 100% !important; }}
::selection {{ background: {hl}; color: {hl_ink}; }}
"""

    title = re.sub(r"[<>&]", "", doc.get("title", "Murmur"))
    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="{base_url}">
<title>{title}</title>
{''.join(head_bits)}
<style>{css}</style>
</head><body>{inner}</body></html>"""

    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
                "base-uri 'self'; form-action 'none'; frame-ancestors 'self'"
            ),
        },
    )


@app.patch("/api/docs/{doc_id}")
def patch_doc(doc_id: str, patch: DocPatch) -> dict:
    fields = patch.model_dump(exclude_none=True)
    if not store.get_doc(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    if "position" in fields:
        doc = store.get_doc(doc_id)
        fields["position"] = max(0, min(int(fields["position"]), len(doc["units"]) - 1))
    if "speed" in fields:
        fields["speed"] = max(0.5, min(float(fields["speed"]), 3.0))
    doc = store.update_doc(doc_id, **fields)
    if doc and "position" in fields:
        search.record_read(doc_id, doc.get("position") or 0)
    return doc or {}


@app.delete("/api/docs/{doc_id}")
def remove_doc(doc_id: str) -> dict:
    if not store.delete_doc(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    search.forget(doc_id)
    film.clear_plan(doc_id)
    film.painter.clear(doc_id)
    shutil.rmtree(config.FILM_DIR / doc_id, ignore_errors=True)
    return {"ok": True}


_subscribers: set[asyncio.Queue] = set()


def _broadcast(message: dict) -> bool:
    """Push a message to every open Murmur tab. Returns whether any tab got it."""
    delivered = False
    for queue in list(_subscribers):
        try:
            queue.put_nowait(message)
            delivered = True
        except Exception:
            _subscribers.discard(queue)
    return delivered


@app.post("/api/quick-read")
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
    return {"id": doc["id"], "delivered": _broadcast({"type": "quick", "id": doc["id"]})}


@app.post("/api/ocr-clipboard")
async def ocr_clipboard() -> dict:
    """OCR the image on the macOS clipboard and queue it for reading."""
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
        "delivered": _broadcast({"type": "quick", "id": doc["id"]}),
    }


@app.get("/api/events")
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


@app.get("/api/export/{job_id}/file")
def export_file(job_id: str):
    job = JOBS.get(job_id)
    if not job or job.get("state") != "done":
        raise HTTPException(status_code=404, detail="Export is not ready")
    path = Path(job["file"])
    if not path.exists():
        raise HTTPException(status_code=404)
    safe = re.sub(r"[^\w\s-]", "", job.get("title", "murmur"))[:80].strip() or "murmur"
    return FileResponse(path, media_type=job.get("mime", "audio/mpeg"), filename=f"{safe}.{job.get('ext', 'mp3')}")


@app.delete("/api/export/{job_id}")
def cancel_export(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job:
        job["cancelled"] = True
    return {"ok": True}


# ---------------------------------------------------------------- static
app.mount("/assets", StaticFiles(directory=str(config.WEB_DIR)), name="assets")


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
