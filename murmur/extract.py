"""Turn PDFs, EPUBs, Office files, images, HTML and URLs into readable text.

Scanned PDFs and image files go through on-device OCR (Apple Vision or Tesseract).
"""
from __future__ import annotations

import ipaddress
import os
import posixpath
import re
import socket
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from . import config, ocr, proc
from .text import clean_markdown, normalize, split_units

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
SUPPORTED = {".pdf", ".epub", ".txt", ".md", ".markdown", ".html", ".htm", ".docx", ".rtf"} | IMAGE_SUFFIXES


class ExtractError(Exception):
    pass


def _tidy(text: str) -> str:
    text = normalize(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _paragraphs(text: str) -> str:
    """Rejoin wrapped lines, keeping blank-line paragraph breaks."""
    blocks = re.split(r"\n\s*\n", text)
    cleaned = []
    for block in blocks:
        joined = re.sub(r"\s*\n\s*", " ", block).strip()
        if joined:
            cleaned.append(joined)
    return "\n\n".join(cleaned)


def _title_from(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if 3 <= len(line) <= 120:
            return re.sub(r"\s+", " ", line)[:120]
    return fallback


def clean_ocr(text: str) -> str:
    """Normalise OCR output so wrapped lines read as flowing sentences."""
    return _paragraphs(_tidy(text))


def _units_by_page(page_texts: list[str]) -> tuple[list[str], list[int]]:
    """Split per page so every sentence knows which page it came from."""
    units: list[str] = []
    pages: list[int] = []
    for index, page_text in enumerate(page_texts):
        for unit in split_units(page_text):
            units.append(unit)
            pages.append(index)
    return units, pages


HEADING_RE = re.compile(
    r"^(chapter|part|book|volume|prologue|epilogue|introduction|preface|foreword|"
    r"afterword|appendix|interlude|act|scene|contents|dedication|acknowledg)",
    re.I,
)


def _first_unit_on_page(unit_pages: list[int], page: int) -> int | None:
    for index, value in enumerate(unit_pages):
        if value >= page:
            return index
    return None


def _toc_from_bookmarks(bookmarks: list, unit_pages: list[int]) -> list[dict]:
    entries: list[dict] = []
    for row in bookmarks or []:
        if len(row) < 3:
            continue
        level, title, page = row[0], row[1], row[2]
        title = re.sub(r"\s+", " ", str(title or "")).strip()
        if not title:
            continue
        page0 = max(0, int(page) - 1)
        unit = _first_unit_on_page(unit_pages, page0)
        if unit is None:
            continue
        entries.append({
            "title": title[:120],
            "level": max(0, int(level) - 1),
            "unit": unit,
            "page": page0,
        })
    return entries[:400]


def _toc_from_headings(page_texts: list[str], unit_pages: list[int]) -> list[dict]:
    """Fallback for PDFs without bookmarks: chapter-like lines."""
    entries: list[dict] = []
    seen: set[str] = set()
    for page, page_text in enumerate(page_texts):
        for line in page_text.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if not (3 <= len(line) <= 90) or not HEADING_RE.match(line):
                continue
            key = line.lower()
            if key in seen:
                continue
            unit = _first_unit_on_page(unit_pages, page)
            if unit is None:
                continue
            seen.add(key)
            entries.append({"title": line[:100], "level": 0, "unit": unit, "page": page})
            if len(entries) >= 300:
                return entries
    return entries


def _toc_from_units(units: list[str]) -> list[dict]:
    """Fallback for plain text and Markdown."""
    entries: list[dict] = []
    for index, unit in enumerate(units):
        short = unit[:90]
        if len(unit) > 160:
            continue
        if HEADING_RE.match(short) or (short.isupper() and 3 <= len(short) <= 70):
            entries.append({"title": short, "level": 0, "unit": index, "page": 0})
        if len(entries) >= 300:
            break
    return entries


def _pdf(path: Path) -> tuple[str, dict, list[str], list[list]]:
    """Extract text, OCR pages with no text layer, and read the outline."""
    import io

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    total = len(document)
    if total == 0:
        document.close()
        raise ExtractError("That PDF has no pages.")

    bookmarks: list[list] = []
    try:
        for bookmark in list(document.get_toc()):
            try:
                dest = bookmark.get_dest()
                page_index = dest.get_index() if dest else 0
                bookmarks.append([
                    int(bookmark.level) + 1,
                    (bookmark.get_title() or "").strip(),
                    int(page_index) + 1,
                ])
            except Exception:
                continue
    except Exception:
        bookmarks = []

    page_text: list[str] = []
    for index in range(total):
        page = document[index]
        textpage = page.get_textpage()
        try:
            page_text.append((textpage.get_text_range() or "").strip())
        finally:
            textpage.close()
            page.close()

    blank = {i for i, text in enumerate(page_text) if len(text) < config.OCR_MIN_CHARS}
    ocr_supported = ocr.available()

    if blank and not ocr_supported:
        if sum(len(text) for text in page_text) < 100:
            document.close()
            raise ExtractError(
                "This looks like a scanned PDF and OCR is not available. On macOS "
                "install pyobjc-framework-Vision; on Linux/Windows install Tesseract."
            )
        blank = set()

    targets = sorted(blank)[: config.OCR_MAX_PAGES]
    target_set = set(targets)
    truncated = len(blank) > len(targets)

    pages: list[str] = []
    done = 0
    for index in range(total):
        if index in target_set:
            page = document[index]
            bitmap = page.render(scale=config.OCR_DPI / 72.0)
            image = bitmap.to_pil().convert("L")
            page.close()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            text = ocr.recognize_bytes(buffer.getvalue())
            done += 1
            if done == 1 or done % 10 == 0:
                print(f"  OCR: page {done}/{len(targets)}", flush=True)
        else:
            text = page_text[index]
        pages.append(text.strip())

    document.close()
    body = _tidy("\n\n".join(page for page in pages if page))
    meta = {"pages": total, "ocrPages": done, "truncated": truncated}
    return body, meta, pages, bookmarks


def _epub(path: Path) -> tuple[str, str, dict]:
    """Read an EPUB directly from its zip container (no AGPL library)."""
    import posixpath
    import zipfile
    from urllib.parse import unquote

    from bs4 import BeautifulSoup

    def resolve(base: str, href: str) -> str:
        href = unquote(href.split("#")[0])
        return posixpath.normpath(posixpath.join(posixpath.dirname(base), href))

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        container = BeautifulSoup(archive.read("META-INF/container.xml"), "xml")
        rootfile = container.find("rootfile")
        if rootfile is None:
            raise ExtractError("That EPUB has no package document.")
        opf_path = rootfile.get("full-path")
        opf = BeautifulSoup(archive.read(opf_path), "xml")

        title_tag = opf.find("dc:title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        manifest = {item.get("id"): item for item in opf.find_all("item")}
        spine: list[str] = []
        for ref in opf.find_all("itemref"):
            if (ref.get("linear") or "yes").lower() == "no":
                continue
            if ref.get("idref"):
                spine.append(ref.get("idref"))

        toc_titles: dict[str, tuple[str, int]] = {}

        nav_item = next(
            (item for item in manifest.values()
             if "nav" in (item.get("properties") or "").split()),
            None,
        )
        ncx_item = next(
            (item for item in manifest.values()
             if item.get("media-type") == "application/x-dtbncx+xml"),
            None,
        )

        nav_path = ""
        ncx_path = ""

        if nav_item is not None:
            nav_path = resolve(opf_path, nav_item.get("href") or "")
            if nav_path in names:
                nav = BeautifulSoup(archive.read(nav_path), "xml")
                navroot = nav.find("nav", attrs={"epub:type": "toc"}) or nav.find("nav")
                if navroot:
                    for link in navroot.find_all("a"):
                        href = resolve(nav_path, link.get("href") or "")
                        level = max(0, len(link.find_parents("ol")) - 1)
                        toc_titles.setdefault(href, (link.get_text(" ", strip=True), level))
        elif ncx_item is not None:
            ncx_path = resolve(opf_path, ncx_item.get("href") or "")
            if ncx_path in names:
                ncx = BeautifulSoup(archive.read(ncx_path), "xml")
                for point in ncx.find_all("navPoint"):
                    label = point.find("navLabel")
                    content = point.find("content")
                    if label is None or content is None:
                        continue
                    href = resolve(ncx_path, content.get("src") or "")
                    level = len(point.find_parents("navPoint"))
                    toc_titles.setdefault(
                        href, (label.get_text(" ", strip=True), level)
                    )

        units: list[str] = []
        unit_pages: list[int] = []
        chapter_files: list[str] = []
        chunks: list[str] = []
        toc: list[dict] = []

        for item_id in spine:
            item = manifest.get(item_id)
            if item is None:
                continue
            href = resolve(opf_path, item.get("href") or "")
            if href not in names or href in {nav_path, ncx_path}:
                continue

            soup = BeautifulSoup(archive.read(href), "html.parser")
            for tag in soup(["script", "style", "nav"]):
                tag.decompose()

            body = soup.body or soup
            for tag in body(["script", "style", "nav"]):
                tag.decompose()
            text = _paragraphs(_tidy(body.get_text("\n")))
            if not text.strip():
                continue
            # Drop a running head that repeats the book title.
            if title and text.startswith(title):
                text = text[len(title):].lstrip(" \n")
            if not text.strip():
                continue

            links = len(soup.find_all("a"))
            if links >= 6 and links * 25 > len(text):
                continue

            chapter_units = split_units(text)
            if not chapter_units:
                continue

            index = len(chapter_files)
            chapter_files.append(href)
            known_title, level = toc_titles.get(href, ("", 0))
            heading = soup.find(["h1", "h2", "h3"])
            chapter_title = (
                known_title.strip()
                or (heading.get_text(" ", strip=True) if heading else "")
                or _title_from(text, f"Chapter {index + 1}")
            )
            chapter_title = re.sub(r"\s+", " ", chapter_title)[:120]

            first_unit = len(units)
            for unit in chapter_units:
                units.append(unit)
                unit_pages.append(index)
            chunks.append(text)

            toc.append({
                "title": chapter_title,
                "level": level if known_title else 0,
                "unit": first_unit,
                "page": index,
            })

    if not units:
        raise ExtractError("Nothing readable was found in that EPUB.")

    meta = {
        "format": "epub",
        "units": units,
        "unitPages": unit_pages,
        "pageCount": len(chapter_files),
        "toc": toc,
        "chapters": chapter_files,
    }
    return (title.strip() or toc[0]["title"] or "Untitled"), "\n\n".join(chunks), meta


def _docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" ".join(cell.text.strip() for cell in row.cells))
    return _tidy("\n\n".join(p for p in parts if p.strip()))


def _html_string(html: str) -> tuple[str, str]:
    import trafilatura

    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            title = meta.title
    except Exception:
        pass
    body = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
        deduplicate=True,
    )
    if not body:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        body = soup.get_text("\n")
    return title, _paragraphs(_tidy(body))


_RTF_DESTINATION = re.compile(
    r"\{(?:\\\*)?\\(?:fonttbl|colortbl|stylesheet|info|pict|header|footer|object|themedata|datastore)"
    r"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
)
_RTF_HEX = re.compile(r"\\'([0-9a-fA-F]{2})")
_RTF_UNICODE = re.compile(r"\\u(-?\d+)\s?\??")
_RTF_CONTROL = re.compile(r"\\[a-zA-Z]+-?\d*\s?")


def _rtf_to_text(data: bytes) -> str:
    """Strip RTF down to plain text without `textutil` (Windows and Linux)."""
    text = data.decode("latin-1", "replace")
    for _ in range(4):  # tables nest; peel the destination groups off one at a time
        stripped = _RTF_DESTINATION.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = _RTF_HEX.sub(
        lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), text
    )
    text = _RTF_UNICODE.sub(lambda m: chr(int(m.group(1)) % 0x10000), text)
    for name, replacement in (
        ("\\par", "\n"), ("\\line", "\n"), ("\\tab", "\t"),
        ("\\emdash", "—"), ("\\endash", "–"), ("\\lquote", "‘"),
        ("\\rquote", "’"), ("\\ldblquote", "“"), ("\\rdblquote", "”"),
    ):
        text = text.replace(name, replacement)
    text = _RTF_CONTROL.sub("", text)
    text = text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    return text.replace("{", "").replace("}", "")


def _rtf(path: Path) -> str:
    tool = config.find_tool("textutil") if sys.platform == "darwin" else None
    if tool:
        try:
            out = proc.run(
                [tool, "-convert", "txt", "-stdout", str(path)],
                capture_output=True, text=True, timeout=30, check=True,
            )
            text = _tidy(out.stdout)
            # textutil exits 0 on a malformed file: the complaint goes to stderr
            # and stdout stays empty, so only trust it when it produced text.
            if text:
                return text
        except Exception:
            pass  # fall through to the built-in reader
    try:
        return _tidy(_rtf_to_text(path.read_bytes()))
    except OSError as exc:
        raise ExtractError("Could not read that RTF file.") from exc


def _image(path: Path, fallback_title: str | None = None) -> tuple[str, str, dict]:
    if not ocr.available():
        raise ExtractError(
            "Reading images needs an OCR backend. On macOS install "
            "pyobjc-framework-Vision; on Linux/Windows install Tesseract."
        )
    raw = ocr.recognize_bytes(path.read_bytes())
    if not raw.strip():
        raise ExtractError("No readable text was found in that image.")
    text = clean_ocr(raw)
    units = split_units(text)

    # A short first line above a longer body is almost always a heading.
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines() if line.strip()]
    heading = lines[0] if lines and 3 <= len(lines[0]) <= 60 and len(lines) >= 3 else ""
    title = heading or (fallback_title or path.stem)
    meta = {
        "images": 1,
        "ocrPages": 1,
        "units": units,
        "unitPages": [0] * len(units),
        "pageCount": 1,
    }
    return title, text, meta


def extract_file(path: Path, fallback_title: str | None = None) -> tuple[str, str, dict]:
    """Return (title, text, meta) for a local file."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ExtractError(f"Unsupported file type: {suffix or 'unknown'}")
    stem = (fallback_title or path.stem).replace("_", " ").replace("-", " ").strip()

    if suffix in IMAGE_SUFFIXES:
        return _image(path, stem)
    if suffix == ".pdf":
        raw, meta, page_texts, bookmarks = _pdf(path)
        if not raw.strip():
            raise ExtractError(
                "That PDF contained no recognisable text, even after OCR."
            )
        units, unit_pages = _units_by_page(page_texts)
        meta["units"] = units
        meta["unitPages"] = unit_pages
        meta["pageCount"] = len(page_texts)
        meta["toc"] = (
            _toc_from_bookmarks(bookmarks, unit_pages)
            or _toc_from_headings(page_texts, unit_pages)
        )
        return _title_from(raw, stem), _paragraphs(raw), meta
    if suffix == ".epub":
        title, body, meta = _epub(path)
        return (title or stem), body, meta
    if suffix == ".docx":
        body = _docx(path)
        return _title_from(body, stem), body, {"format": "docx"}
    if suffix in {".html", ".htm"}:
        title, body = _html_string(path.read_text("utf-8", errors="replace"))
        return (title or stem), body, {"format": "html"}
    if suffix == ".rtf":
        body = _rtf(path)
        return _title_from(body, stem), body, {"format": "rtf"}

    raw = path.read_text("utf-8", errors="replace")
    body = clean_markdown(raw) if suffix in {".md", ".markdown"} else raw
    body = _tidy(body)
    units = split_units(body)
    meta = {
        "format": "text",
        "units": units,
        "unitPages": [0] * len(units),
        "pageCount": 1,
        "toc": _toc_from_units(units),
    }
    return _title_from(body, stem), body, meta


MAX_URL_BYTES = 12 * 1024 * 1024


def _is_public_host(host: str) -> bool:
    """Only allow hosts that resolve to public internet addresses.

    This keeps the importer from being used to reach the Mac's own services or
    other devices on the local network.
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        if not address.is_global:
            return False
    return bool(infos)


def _check_fetch_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ExtractError("Only http and https addresses can be imported.")
    if not parsed.hostname or not _is_public_host(parsed.hostname):
        raise ExtractError("That address is not reachable from Rhapsode.")


def _fetch_page(client, url: str):
    """Fetch a page, validating every redirect and capping the download."""
    for _ in range(6):
        _check_fetch_url(url)
        with client.stream("GET", url) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ExtractError("That link redirected to an invalid address.")
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if content_type and "html" not in content_type and "text" not in content_type:
                raise ExtractError("That link is not a readable web page.")
            chunks = bytearray()
            for chunk in response.iter_bytes(65536):
                chunks.extend(chunk)
                if len(chunks) > MAX_URL_BYTES:
                    raise ExtractError("That page is too large to import.")
            return response, bytes(chunks)
    raise ExtractError("That link redirects too many times.")


def extract_url(url: str) -> tuple[str, str, dict]:
    import httpx

    if not re.match(r"^https?://", url, re.I):
        if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I) or re.match(r"^[a-z]+:", url, re.I):
            raise ExtractError("Only http and https addresses can be imported.")
        url = "https://" + url
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with httpx.Client(follow_redirects=False, timeout=25, headers=headers) as client:
            response, raw = _fetch_page(client, url)
    except ExtractError:
        raise
    except httpx.HTTPError as exc:
        raise ExtractError(f"Could not fetch that page ({exc}).") from exc

    encoding = response.encoding or "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    title, body = _html_string(text)
    if len(body) < 40:
        raise ExtractError("That page did not contain enough readable text.")
    return (title or url.split("//")[-1][:80]), body, {"format": "url"}


# What PDFium and zipfile raise when the bytes are not the format the name claims.
_PARSE_FAILURES = {"PdfiumError", "BadZipFile", "PdfReadError"}

_KIND = {
    ".pdf": "PDF",
    ".epub": "EPUB",
    ".docx": "Word document",
    ".html": "web page",
    ".htm": "web page",
    ".rtf": "RTF file",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".gif": "image",
}


def _unreadable(suffix: str, exc: Exception) -> ExtractError:
    kind = _KIND.get(suffix, "file")
    return ExtractError(
        f"That {kind} could not be opened: it looks damaged, or its contents are "
        f"not what the file name claims. ({type(exc).__name__})"
    )


def extract_upload(filename: str, data: bytes) -> tuple[str, str, dict]:
    suffix = Path(filename).suffix.lower()
    # Windows refuses to open a file that NamedTemporaryFile still holds with
    # delete-on-close sharing, so the handle is released before anything reads
    # the path, and the file is removed once extraction is done with it.
    handle, name = tempfile.mkstemp(prefix="rhapsode-upload-", suffix=suffix)
    os.close(handle)
    path = Path(name)
    try:
        path.write_bytes(data)
        try:
            return extract_file(path, fallback_title=Path(filename).stem)
        except ExtractError:
            raise
        except Exception as exc:
            # A damaged file is something the reader has to see, not a stack
            # trace: PDFium and zipfile raise their own types for bytes that are
            # not the format the file name claims.
            if type(exc).__name__ in _PARSE_FAILURES:
                raise _unreadable(suffix, exc) from exc
            raise
    finally:
        path.unlink(missing_ok=True)
