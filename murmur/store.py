"""SQLite library of documents and app settings."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import cache, config, text as textmod
from . import proc

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id        TEXT PRIMARY KEY,
    title     TEXT NOT NULL,
    source    TEXT DEFAULT '',
    kind      TEXT DEFAULT 'text',
    body      TEXT NOT NULL,
    units     TEXT NOT NULL,
    unit_pages TEXT NOT NULL DEFAULT '[]',
    toc       TEXT NOT NULL DEFAULT '[]',
    chapter_files TEXT NOT NULL DEFAULT '[]',
    file      TEXT NOT NULL DEFAULT '',
    page_count INTEGER NOT NULL DEFAULT 0,
    created   REAL NOT NULL,
    updated   REAL NOT NULL,
    position  INTEGER DEFAULT 0,
    voice     TEXT DEFAULT '',
    speed     REAL DEFAULT 1.0,
    chars     INTEGER DEFAULT 0,
    words     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS translations (
    key        TEXT PRIMARY KEY,
    source     TEXT,
    target     TEXT,
    original   TEXT,
    translated TEXT,
    created    REAL
);
"""

# Translations are read once per sentence while a chapter plays; keep the
# hot ones in RAM so a repeat costs a dict lookup, not a query.
_translations = cache.LRUCache(maxsize=2048, name="translations")


DEFAULT_SETTINGS: dict[str, Any] = {
    "voice": "af_heart",
    "speed": 1.0,
    "theme": "ink",
    "font": "sans",
    "fontSize": 19,
    "autoScroll": True,
    "remoteAccess": False,
    "translateToVoice": True,
    "filmQuality": "high",
    "polishStills": True,
}


_conn_lock = threading.RLock()
_shared: sqlite3.Connection | None = None
_shared_path: Path | None = None


def _conn() -> sqlite3.Connection:
    """One connection, reused for the life of the process.

    Opening SQLite per call cost a handle setup on every translation lookup, and a
    chapter is hundreds of them. The connection is shared under a lock and reopened
    when the data directory changes (tests point config.DB_PATH elsewhere).
    """
    global _shared, _shared_path
    with _conn_lock:
        if _shared is not None and _shared_path == config.DB_PATH:
            return _shared
        if _shared is not None:
            try:
                _shared.close()
            except sqlite3.Error:
                pass
            _shared = None
        config.HOME.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _shared, _shared_path = conn, config.DB_PATH
        return conn


def close() -> None:
    """Drop the shared connection and the in-memory caches (tests, shutdown)."""
    global _shared, _shared_path
    with _conn_lock:
        if _shared is not None:
            try:
                _shared.close()
            except sqlite3.Error:
                pass
        _shared, _shared_path = None, None
    _translations.clear()


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One short-lived connection per operation.

    `with sqlite3.connect(...)` only commits — it never closes — so every call
    used to leak a handle. On Windows that keeps murmur.db locked and WAL files
    open long after the request that needed them.
    """
    conn = _conn()
    with _conn_lock:
        with conn:
            yield conn


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
BROWSER_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _convert_to_png(source: Path, target: Path) -> bool:
    """Convert TIFF/BMP/HEIC/WebP to PNG with Pillow.

    HEIC/HEIF needs `pillow-heif`; macOS falls back to `sips` when Pillow
    cannot decode the file.
    """
    try:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except ImportError:
            pass
        from PIL import Image

        with Image.open(source) as image:
            has_alpha = image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info
            frame = image.convert("RGBA" if has_alpha else "RGB")
            frame.save(target, format="PNG")
        return target.exists()
    except Exception:
        target.unlink(missing_ok=True)

    if sys.platform == "darwin":
        try:
            result = proc.run(
                ["sips", "-s", "format", "png", str(source), "--out", str(target)],
                capture_output=True,
                timeout=60,
            )
            return result.returncode == 0 and target.exists()
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def save_source(doc_id: str, suffix: str, data: bytes) -> str:
    """Store the uploaded file so pages can be rendered later.

    Formats browsers cannot display (TIFF, BMP, HEIC, …) are converted to PNG.
    """
    config.ensure_dirs()
    name = f"{doc_id}{suffix}"
    path = config.FILES_DIR / name
    path.write_bytes(data)

    if suffix in IMAGE_SUFFIXES and suffix not in BROWSER_IMAGE_SUFFIXES:
        converted = config.FILES_DIR / f"{doc_id}.png"
        if _convert_to_png(path, converted):
            path.unlink(missing_ok=True)
            return converted.name
    return name


def doc_file(doc_id: str) -> Path | None:
    with _db() as conn:
        row = conn.execute("SELECT file FROM docs WHERE id = ?", (doc_id,)).fetchone()
    if not row or not row["file"]:
        return None
    return config.FILES_DIR / row["file"]


def init() -> None:
    config.ensure_dirs()
    with _db() as conn:
        conn.executescript(SCHEMA)
        # Migrate libraries created before page rendering existed.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(docs)")}
        for name, ddl in (
            ("unit_pages", "TEXT NOT NULL DEFAULT '[]'"),
            ("toc", "TEXT NOT NULL DEFAULT '[]'"),
            ("chapter_files", "TEXT NOT NULL DEFAULT '[]'"),
            ("file", "TEXT NOT NULL DEFAULT ''"),
            ("page_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE docs ADD COLUMN {name} {ddl}")


def _row_to_doc(row: sqlite3.Row, include_units: bool = True) -> dict:
    file_name = row["file"] or ""
    suffix = Path(file_name).suffix.lower()
    viewer = None
    if suffix == ".pdf":
        viewer = "pdf"
    elif suffix == ".epub":
        viewer = "epub"
    elif suffix in IMAGE_SUFFIXES:
        viewer = "image"

    doc = {
        "id": row["id"],
        "title": row["title"],
        "source": row["source"],
        "kind": row["kind"],
        "created": row["created"],
        "updated": row["updated"],
        "position": row["position"],
        "voice": row["voice"],
        "speed": row["speed"],
        "chars": row["chars"],
        "words": row["words"],
        "unitCount": len(json.loads(row["units"])),
        "pageCount": row["page_count"] or 0,
        "viewer": viewer,
    }
    if include_units:
        doc["text"] = row["body"]
        doc["units"] = json.loads(row["units"])
        doc["unitPages"] = json.loads(row["unit_pages"] or "[]")
        doc["toc"] = json.loads(row["toc"] or "[]")
        doc["chapterFiles"] = json.loads(row["chapter_files"] or "[]")
    return doc


def list_docs() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM docs ORDER BY updated DESC").fetchall()
    return [_row_to_doc(r, include_units=False) for r in rows]


def get_doc(doc_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM docs WHERE id = ?", (doc_id,)).fetchone()
    return _row_to_doc(row) if row else None


def create_doc(
    title: str,
    body: str,
    source: str = "",
    kind: str = "text",
    units: list[str] | None = None,
    unit_pages: list[int] | None = None,
    page_count: int = 0,
    file_name: str = "",
    toc: list[dict] | None = None,
    chapter_files: list[str] | None = None,
) -> dict:
    units = units if units is not None else textmod.split_units(body)
    if not units:
        raise ValueError("Nothing readable was found in that document.")
    if unit_pages is None or len(unit_pages) != len(units):
        unit_pages = [0] * len(units)
    now = time.time()
    doc_id = uuid.uuid4().hex[:12]
    title = (title or "Untitled").strip()[:200]
    words = textmod.word_count(" ".join(units))
    with _db() as conn:
        conn.execute(
            "INSERT INTO docs (id,title,source,kind,body,units,unit_pages,toc,chapter_files,"
            "file,page_count,created,updated,position,voice,speed,chars,words)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,'',1.0,?,?)",
            (
                doc_id, title, source, kind, body,
                json.dumps(units, ensure_ascii=False),
                json.dumps(unit_pages),
                json.dumps(toc or [], ensure_ascii=False),
                json.dumps(chapter_files or [], ensure_ascii=False),
                file_name, page_count,
                now, now,
                sum(len(u) for u in units), words,
            ),
        )
    return get_doc(doc_id)  # type: ignore[return-value]


def update_doc(doc_id: str, **fields: Any) -> dict | None:
    allowed = {k: v for k, v in fields.items() if k in {
        "title", "position", "voice", "speed", "body", "units", "words", "chars",
        "file", "unit_pages", "page_count", "toc", "chapter_files",
    }}
    if not allowed:
        return get_doc(doc_id)
    if "units" in allowed and isinstance(allowed["units"], list):
        allowed["units"] = json.dumps(allowed["units"], ensure_ascii=False)
    allowed["updated"] = time.time()
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with _db() as conn:
        conn.execute(f"UPDATE docs SET {sets} WHERE id = ?", (*allowed.values(), doc_id))
    return get_doc(doc_id)


def touch(doc_id: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE docs SET updated = ? WHERE id = ?", (time.time(), doc_id))


def delete_doc(doc_id: str) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT file FROM docs WHERE id = ?", (doc_id,)).fetchone()
        cur = conn.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
    if cur.rowcount > 0:
        if row and row["file"]:
            (config.FILES_DIR / row["file"]).unlink(missing_ok=True)
        for page in config.PAGES_DIR.glob(f"{doc_id}_*.png"):
            page.unlink(missing_ok=True)
    return cur.rowcount > 0


def get_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    with _db() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            try:
                settings[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                settings[row["key"]] = row["value"]
    return settings


def set_settings(patch: dict) -> dict:
    with _db() as conn:
        for key, value in patch.items():
            if key in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
    return get_settings()


def translation_get(key: str) -> str | None:
    cached = _translations.get(key)
    if cached is not None:
        return cached
    with _db() as conn:
        row = conn.execute(
            "SELECT translated FROM translations WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    _translations.put(key, row["translated"])
    return row["translated"]


def translation_put(key: str, source: str, target: str, original: str, translated: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO translations(key,source,target,original,translated,created)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET translated=excluded.translated, created=excluded.created",
            (key, source, target, original, translated, time.time()),
        )
    _translations.put(key, translated)
