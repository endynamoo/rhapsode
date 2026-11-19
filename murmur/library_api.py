"""Document routes for Murmur.

Split out of `server.py`: everything that reads a document, which is also the
group that touches stored files, the shared PDFium lock and the search index.
"""
from __future__ import annotations

import html
import json
import mimetypes
import posixpath
import re
import shutil
import threading
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import config, extract, film, ocr, search, store

router = APIRouter()

# PDFium is not thread-safe; the page image and word-box endpoints share one lock.
PDF_LOCK = threading.Lock()

MAX_UPLOAD = 120 * 1024 * 1024


class TextIn(BaseModel):
    title: str = ""
    text: str


class UrlIn(BaseModel):
    url: str
    title: str = ""


class DocPatch(BaseModel):
    title: str | None = None
    position: int | None = None
    voice: str | None = None
    speed: float | None = None


@router.get("/api/docs")
def list_docs() -> dict:
    return {"docs": store.list_docs()}


@router.post("/api/docs")
def create_doc(payload: TextIn) -> dict:
    try:
        doc = store.create_doc(payload.title, payload.text, kind="text")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    search.note_document(doc["id"])
    return doc


@router.post("/api/docs/upload")
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


@router.post("/api/docs/url")
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


@router.get("/api/docs/{doc_id}")
def get_doc(doc_id: str) -> dict:
    doc = store.get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # Opening a book is reading activity: this is what "recently read" means.
    search.record_read(doc_id, doc.get("position") or 0)
    return doc


def _normalise_boxes(width: float, height: float, words: list[dict]) -> list[dict]:
    """Word boxes as fractions of the page, which is what the reader draws with."""
    if not width or not height:
        return []
    out: list[dict] = []
    for word in words:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "text": text,
                "x": round(float(word["x"]) / width, 6),
                "y": round(float(word["y"]) / height, 6),
                "w": round(max(0.0, float(word["w"])) / width, 6),
                "h": round(max(0.0, float(word["h"])) / height, 6),
            }
        )
    return out


def _text_layer_boxes(source: Path, page: int) -> tuple[float, float, str, list[dict]]:
    """The page's own word boxes in PDF points, as `(width, height, text, words)`.

    Empty for a scanned page: there is text on the image, but nothing selectable
    to take positions from.
    """
    with PDF_LOCK:
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
            finally:
                textpage.close()
                page_obj.close()
                pdf.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not read page words: {exc}") from exc

    # PDF points put the origin at the bottom; the reader draws from the top.
    boxes = [
        {
            "text": word["text"],
            "x": word["left"],
            "y": height - word["top"],
            "w": word["right"] - word["left"],
            "h": word["top"] - word["bottom"],
        }
        for word in words
    ]
    return width, height, text, boxes


def _render_page_png(doc_id: str, page: int, dpi: int) -> Path | None:
    """The rendered page, from cache or freshly rendered under the PDFium lock."""
    cached = config.PAGES_DIR / f"{doc_id}_{page}_{dpi}.png"
    if cached.exists():
        return cached
    source = store.doc_file(doc_id)
    if not source or not source.exists():
        return None
    with PDF_LOCK:
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(source))
            if page >= len(pdf):
                pdf.close()
                return None
            bitmap = pdf[page].render(scale=dpi / 72.0)
            image = bitmap.to_pil().convert("RGB")
            pdf.close()
        except Exception:
            return None
    tmp = cached.with_suffix(".tmp")
    image.save(tmp, format="PNG")
    tmp.replace(cached)
    return cached


def _page_words_from_ocr(doc_id: str, page: int) -> list[dict]:
    """Word boxes for a page with no text layer, or [] when OCR cannot say.

    A scanned page renders as an image with nothing selectable on it, so the
    reader had no word to mark even though the caption underneath had one. The
    recogniser already runs on these pages at import time; asking it for boxes
    the first time a page is shown is what lets the mark land on the page.
    """
    if not ocr.available():
        return []
    png = _render_page_png(doc_id, page, 140)
    if png is None:
        return []
    try:
        boxes = ocr.word_boxes_bytes(png.read_bytes())
    except Exception:
        return []
    if not boxes:
        return []
    return _normalise_boxes(boxes["width"], boxes["height"], boxes.get("words", []))


@router.get("/api/docs/{doc_id}/page/{page}/words")
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
            # A file written before the OCR fallback existed says "no words" for a
            # scanned page without ever having asked the recogniser; treat it as
            # absent so the page is read again once.
            attempted_ocr = cached.get("origin") == "ocr"
            if cached.get("source") == fingerprint and (cached.get("words") or attempted_ocr):
                return cached
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    width, height, text, words = _text_layer_boxes(source, page)
    payload = {
        "page": page,
        "width": width,
        "height": height,
        "text": text,
        "source": fingerprint,
        "origin": "text",
        "words": _normalise_boxes(width, height, words),
    }

    if not payload["words"]:
        # A scanned page: no text layer to take boxes from, so ask the recogniser
        # where each word sits. Cached like any other page, so only the first view
        # of a page pays for it.
        boxes = _page_words_from_ocr(doc_id, page)
        if boxes:
            payload["words"] = boxes
            payload["origin"] = "ocr"

    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    tmp.replace(cache)
    return payload


@router.get("/api/docs/{doc_id}/page/{page}")
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
        if not store.doc_file(doc_id):
            raise HTTPException(status_code=404, detail="The original file is missing")
        if _render_page_png(doc_id, page, dpi) is None:
            raise HTTPException(status_code=500, detail="Could not render that page")

    return FileResponse(
        cached,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/docs/{doc_id}/image")
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


@router.get("/api/docs/{doc_id}/epub/{asset:path}")
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


@router.get("/api/docs/{doc_id}/chapter/{chapter}")
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

    title = re.sub(r"[<>&]", "", doc.get("title", "Rhapsode"))
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


@router.patch("/api/docs/{doc_id}")
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


@router.delete("/api/docs/{doc_id}")
def remove_doc(doc_id: str) -> dict:
    if not store.delete_doc(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    search.forget(doc_id)
    film.clear_plan(doc_id)
    film.painter.clear(doc_id)
    shutil.rmtree(config.FILM_DIR / doc_id, ignore_errors=True)
    return {"ok": True}


