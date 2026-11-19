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
    content_updated REAL NOT NULL DEFAULT 0,
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
    # The spoken word is marked as the voice reaches it. On by default; the
    # switch exists because some readers find any movement in the text
    # distracting, and Manga never highlights anyway (there is nothing to follow).
    "highlightWords": True,
    "filmQuality": "high",
    "polishStills": True,
    # Face repair is a separate switch because it can redraw a face into someone
    # else: off until the before/after is on the record, and opt-in per install.
    "polishFaces": False,
    # Auto Generate has two halves because they cost wildly different amounts:
    # a look-ahead still is a few seconds on a GPU, a chapter film is an hour.
    # Scenes on, films off — reading a book quietly paints ahead, and nothing
    # long-running starts without a gesture.
    "autoScenes": True,
    "autoFilms": False,
    # Load the stills engine when Cinema is opened, not when the first scene is
    # clicked: the weights take about twenty seconds and the reader is looking at
    # the plan for longer than that.
    "warmStills": True,
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
            ("content_updated", "REAL NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE docs ADD COLUMN {name} {ddl}")
        # `updated` moves on every position save; `content_updated` only moves
        # when the text does, so search can tell "you turned a page" from "this
        # book changed" without reading the body.
        conn.execute("UPDATE docs SET content_updated = created WHERE content_updated = 0")


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
        "contentUpdated": row["content_updated"],
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
            "file,page_count,created,updated,content_updated,position,voice,speed,chars,words)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'',1.0,?,?)",
            (
                doc_id, title, source, kind, body,
                json.dumps(units, ensure_ascii=False),
                json.dumps(unit_pages),
                json.dumps(toc or [], ensure_ascii=False),
                json.dumps(chapter_files or [], ensure_ascii=False),
                file_name, page_count,
                now, now, now,
                sum(len(u) for u in units), words,
            ),
        )
    return get_doc(doc_id)  # type: ignore[return-value]


def update_doc(doc_id: str, **fields: Any) -> dict | None:
    allowed = {k: v for k, v in fields.items() if k in {
        "title", "position", "voice", "speed", "body", "units", "words", "chars",
        "file", "unit_pages", "page_count", "toc", "chapter_files", "content_updated",
    }}
    if not allowed:
        return get_doc(doc_id)
    if "units" in allowed and isinstance(allowed["units"], list):
        allowed["units"] = json.dumps(allowed["units"], ensure_ascii=False)
    # Only a text change moves the content stamp; a new position or voice must
    # not look like an edit to the search index.
    if "content_updated" not in allowed and ("body" in allowed or "units" in allowed):
        allowed["content_updated"] = time.time()
    allowed["updated"] = time.time()
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with _db() as conn:
        conn.execute(f"UPDATE docs SET {sets} WHERE id = ?", (*allowed.values(), doc_id))
    return get_doc(doc_id)


def touch(doc_id: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE docs SET updated = ? WHERE id = ?", (time.time(), doc_id))


def doc_stats() -> list[dict]:
    """One row per document without its body or units.

    The search index needs to notice a changed book cheaply; parsing every
    ``units`` JSON array just to compare signatures would make a no-op index
    pass as expensive as the first one.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, title, updated, content_updated, chars, words, page_count FROM docs"
        ).fetchall()
    return [dict(row) for row in rows]


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
    loaded: dict[str, Any] = {}
    with _db() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            try:
                loaded[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                loaded[row["key"]] = row["value"]
    settings.update(loaded)
    # One switch became two. An install that had the old single switch on keeps
    # the half it was actually about (chapter films); scenes follow the default.
    if "autoGenerate" in loaded and "autoFilms" not in loaded:
        settings["autoFilms"] = bool(loaded["autoGenerate"])
    settings.pop("autoGenerate", None)
    return settings


# Settings that are numbers also get a sane range: a zero speed would never
# finish a sentence, which is worse for the reader than ignoring the write.
_SETTING_RANGES = {"speed": (0.25, 4.0), "fontSize": (8, 72)}


def _coerce_setting(default: Any, value: Any) -> Any:
    """The value only when it has the type its default says it should have."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else None
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return type(default)(value)
    if isinstance(default, str):
        return value if isinstance(value, str) else None
    return None


def set_settings(patch: dict) -> dict:
    """Store the settings that make sense; a wrong type leaves the old value.

    The reader's own UI always sends the right shape, so anything else is a
    caller's mistake - a script, an old client. Storing it anyway would hand the
    front end a value it cannot use (a playback rate of "fast" throws in the
    browser), so it is dropped rather than saved.
    """
    clean: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            continue
        coerced = _coerce_setting(DEFAULT_SETTINGS[key], value)
        if coerced is None:
            continue
        low, high = _SETTING_RANGES.get(key, (None, None))
        if low is not None:
            coerced = min(high, max(low, coerced))
        clean[key] = coerced
    with _db() as conn:
        for key, value in clean.items():
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
