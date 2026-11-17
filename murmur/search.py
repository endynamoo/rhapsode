"""Cross-library search: SQLite FTS5, optionally fused with sentence embeddings.

Everything here is local. The index is a second SQLite database
(`search.db` next to `murmur.db`) so that rebuilding it, or losing it, never
endangers the library itself.

Design
------
* **Chunks.** One speech unit (mostly one sentence) is one chunk. A unit longer
  than :data:`CHUNK_CHARS` is split on a word boundary, so a hit is never a
  paragraph the reader cannot find.
* **Anchors.** Each chunk stores the document id, the unit index (what the
  reader's position is), the page the importer recorded, and the table-of-
  contents entry the unit falls in. That tuple is the jump target the UI uses.
* **Ranking.** Two ordered lists - BM25 from FTS5 and cosine from MiniLM - are
  fused with reciprocal rank fusion (``1 / (60 + rank)``), so neither score has
  to be calibrated against the other. A small recency term then nudges
  documents that were read recently (half-life 30 days, at most 0.002, which is
  about seven places in one list at the top of the ranking).
* **Incremental.** A book is skipped while its cheap signature (updated, chars,
  words, pages) is unchanged; when the signature moves, its body hash decides
  whether the text really changed. Rows are written in batches and the book is
  marked ``partial`` until the last batch commits, so an interrupted run
  resumes at the next unit instead of duplicating rows.
* **Fallback.** Without the embedding model (not downloaded, or switched off
  with ``MURMUR_SEMANTIC=off``) search is lexical-only, and both the API and
  the UI say so. ``books.embedded < books.chunks`` is the queue for embedding
  books that were indexed before the model arrived.

Recency
-------
Reading activity is recorded in a `progress` table by the server whenever a
document is opened or its position is saved. ``recent=7d`` therefore means
"documents whose reading activity is within the last seven days"; ``24h`` and
``2w`` work too, and an absent or ``all`` filter means everything. A book that
is merely imported and never opened has no row here and is not "recent".
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import config, semantic, store

# Ranking knobs (documented in the module docstring and docs/SEARCH.md).
RRF_K = 60
RECENCY_WEIGHT = 0.002
RECENCY_HALF_LIFE_DAYS = 30.0
# A meaning-only hit has to be plausible on its own: MiniLM cosines sit around
# 0.4-0.6 for a real match and below ~0.25 for unrelated sentences, so a floor
# plus "within 60% of the best" keeps a small library from answering every
# query with every sentence it holds.
SEMANTIC_FLOOR = 0.22
SEMANTIC_RELATIVE = 0.6

# Indexing knobs.
CHUNK_CHARS = 600
INSERT_BATCH = 200
EMBED_BATCH = 32
LEXICAL_CANDIDATES = 200
SEMANTIC_CANDIDATES = 200
SEMANTIC_SCAN_BLOCK = 4096
SNIPPET_CHARS = 260

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    doc_id      TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    signature   TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    units       INTEGER NOT NULL DEFAULT 0,
    chunks      INTEGER NOT NULL DEFAULT 0,
    embedded    INTEGER NOT NULL DEFAULT 0,
    chapters    TEXT NOT NULL DEFAULT '[]',
    state       TEXT NOT NULL DEFAULT 'done',
    indexed_at  REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY,
    doc_id  TEXT NOT NULL,
    unit    INTEGER NOT NULL,
    ord     INTEGER NOT NULL DEFAULT 0,
    page    INTEGER NOT NULL DEFAULT 0,
    chapter INTEGER NOT NULL DEFAULT -1,
    start   INTEGER NOT NULL DEFAULT 0,
    stop    INTEGER NOT NULL DEFAULT 0,
    text    TEXT NOT NULL,
    UNIQUE(doc_id, unit, ord)
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id INTEGER PRIMARY KEY,
    dim      INTEGER NOT NULL,
    vec      BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
    doc_id   TEXT PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0,
    first_read REAL,
    last_read  REAL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_db_lock = threading.RLock()
_ready: set[str] = set()
_work_lock = threading.RLock()
_work: dict[str, Any] = {
    "running": False,
    "current": None,
    "done": 0,
    "total": 0,
    "lastError": "",
    "finished": 0.0,
}
_queued: list[str] = []
_worker: threading.Thread | None = None
_force_all = False
_stop = threading.Event()


# ------------------------------------------------------------------ plumbing
def db_path() -> Path:
    """Search index path; derived per call so tests can move the data dir."""
    return Path(config.HOME) / "search.db"


@contextmanager
def _db():
    """One short-lived connection, committed on success and always closed.

    The index is written by a background thread while requests read it, so WAL
    plus a busy timeout is what keeps a search from failing during an index
    pass. Each call gets its own connection, and the module lock only guards
    first-time schema creation, so a long vector scan does not block a write.
    Closing matters: on Windows an open handle keeps the file locked.
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        if key not in _ready:
            with _db_lock:
                if key not in _ready:
                    # WAL is persistent in the file: set it when the database is
                    # created, never on every connection. Re-issuing the pragma
                    # while another connection is mid-write can fail with
                    # "database is locked" even with a busy timeout.
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(SCHEMA)
                    _ready.add(key)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _signature(doc: dict) -> str:
    """The cheap half of the change check: no body is read to compare it."""
    return "|".join(
        str(doc.get(key) or 0)
        for key in ("updated", "chars", "words")
    ) + "|" + str(doc.get("pageCount") or doc.get("page_count") or 0)


def _fingerprint(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:32]


def _terms(query: str) -> list[str]:
    """Words for the FTS5 match and for snippet highlighting.

    Common function words are dropped so that "water coming in through the
    window" does not match every sentence containing "in". If dropping them
    would leave nothing, the original words are used: a one-word query is a
    legitimate search.
    """
    raw: list[str] = []
    for word in re.findall(r"\w+", query or "", flags=re.UNICODE):
        word = word.strip("_")
        if len(word) < 2 or len(word) > 60:
            continue
        if word.lower() not in [w.lower() for w in raw]:
            raw.append(word)
        if len(raw) >= 16:
            break
    words = [word for word in raw if word.lower() not in STOPWORDS]
    return words or raw


_RECENT_UNITS = {"h": 1 / 24, "d": 1.0, "w": 7.0, "m": 30.0}

# Words that carry no lexical signal. Deliberately short: an over-eager stop
# list would stop a book about "The Road" from being findable by its title.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from",
    "had", "has", "have", "he", "her", "him", "his", "i", "if", "in", "into", "is",
    "it", "its", "me", "my", "no", "not", "of", "on", "or", "our", "she", "so",
    "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "to", "up", "us", "was", "we", "were", "what", "when", "where",
    "which", "who", "will", "with", "you", "your",
}


def _recent_cutoff(recent: str | None) -> float | None:
    """Turn ``7d`` / ``24h`` / ``2w`` into an epoch cutoff (``None`` = no filter)."""
    text = (recent or "").strip().lower()
    if not text or text in {"all", "any", "off"}:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdwm]?)", text)
    if not match:
        raise ValueError("recent must look like 7d, 24h, 2w or all")
    amount = float(match.group(1))
    if amount <= 0:
        raise ValueError("recent must be a positive number of days")
    days = amount * _RECENT_UNITS[match.group(2) or "d"]
    return time.time() - days * 86400


# ------------------------------------------------------------------- chunking
def _split_unit(raw: str, limit: int = CHUNK_CHARS) -> list[tuple[int, int, str]]:
    """Split one unit into ``(start, stop, text)`` pieces without breaking words.

    Offsets are into ``raw`` so a chunk can be found again even if the unit text
    is edited around it. A single word longer than the limit is the only case
    that gets cut mid-word.
    """
    if not raw or not raw.strip():
        return []
    pieces: list[tuple[int, int, str]] = []
    position = 0
    length = len(raw)
    while position < length:
        while position < length and raw[position].isspace():
            position += 1
        if position >= length:
            break
        stop = min(length, position + limit)
        if stop < length:
            space = raw.rfind(" ", position, stop)
            if space > position + limit // 3:
                stop = space
            else:
                stop = min(length, position + limit)
        window = raw[position:stop]
        text = window.strip()
        if text:
            lead = len(window) - len(window.lstrip())
            trail = len(window) - len(window.rstrip())
            pieces.append((position + lead, stop - trail, text))
        position = stop
    return pieces


def _chapter_map(toc: list[dict] | None) -> list[tuple[int, int, str]]:
    """``(unit, chapter index, title)`` sorted by the unit each entry starts at."""
    entries: list[tuple[int, int, str]] = []
    for index, entry in enumerate(toc or []):
        try:
            unit = int(entry.get("unit", 0))
        except (TypeError, ValueError, AttributeError):
            continue
        entries.append((max(0, unit), index, str(entry.get("title") or "")))
    entries.sort(key=lambda item: item[0])
    return entries


def _chapter_for(unit: int, chapters: list[tuple[int, int, str]]) -> int:
    current = -1
    for start, index, _ in chapters:
        if start <= unit:
            current = index
        else:
            break
    return current


# -------------------------------------------------------------------- indexing
def _insert_chunks(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    for row in rows:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO chunks"
            " (doc_id, unit, ord, page, chapter, start, stop, text)"
            " VALUES (?,?,?,?,?,?,?,?)",
            row,
        )
        if cursor.rowcount:
            conn.execute(
                "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                (cursor.lastrowid, row[7]),
            )


def _drop_book(conn: sqlite3.Connection, doc_id: str) -> None:
    ids = [row[0] for row in conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))]
    if ids:
        conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(chunk_id,) for chunk_id in ids])
    conn.execute(
        "DELETE FROM vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id = ?)",
        (doc_id,),
    )
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM books WHERE doc_id = ?", (doc_id,))


def _embed_doc(conn: sqlite3.Connection, doc_id: str) -> int:
    """Embed the chunks of one book that have no vector yet. Returns the count."""
    existing = conn.execute(
        "SELECT COUNT(*) FROM vectors v JOIN chunks c ON c.id = v.chunk_id WHERE c.doc_id = ?",
        (doc_id,),
    ).fetchone()[0]
    if not semantic.available():
        return existing
    pending = conn.execute(
        "SELECT c.id, c.text FROM chunks c"
        " LEFT JOIN vectors v ON v.chunk_id = c.id"
        " WHERE c.doc_id = ? AND v.chunk_id IS NULL ORDER BY c.id",
        (doc_id,),
    ).fetchall()
    total = existing
    for start in range(0, len(pending), EMBED_BATCH):
        batch = pending[start : start + EMBED_BATCH]
        try:
            matrix = semantic.embed([row["text"] for row in batch])
        except Exception as exc:
            # Keep the lexical half of the feature working; the next run retries.
            with _work_lock:
                _work["lastError"] = f"Embedding failed: {exc}"
            break
        if matrix.shape[0] != len(batch):
            break
        for row, vector in zip(batch, matrix):
            conn.execute(
                "INSERT OR REPLACE INTO vectors (chunk_id, dim, vec) VALUES (?,?,?)",
                (row["id"], int(vector.shape[0]), vector.astype("<f4").tobytes()),
            )
        total += len(batch)
        conn.execute("UPDATE books SET embedded = ? WHERE doc_id = ?", (total, doc_id))
        conn.commit()
    conn.execute("UPDATE books SET embedded = ? WHERE doc_id = ?", (total, doc_id))
    return total


def index_book(
    doc_id: str,
    *,
    force: bool = False,
    stop_after_units: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Index (or skip) one document. Safe to call from any thread.

    ``stop_after_units`` exists for the interruption test: it leaves the book
    marked ``partial`` after a few units, exactly like a killed process, so the
    resume path is exercised instead of assumed.
    """
    started = time.perf_counter()
    doc = store.get_doc(doc_id)
    if not doc:
        return {"docId": doc_id, "state": "missing", "chunks": 0, "embedded": 0, "seconds": 0.0}

    title = str(doc.get("title") or "")
    signature = _signature(doc)
    fingerprint = _fingerprint(doc.get("text") or "")
    units: list[str] = doc.get("units") or []
    unit_pages: list[int] = doc.get("unitPages") or []
    chapters = _chapter_map(doc.get("toc") or [])

    with _db() as conn:
        row = conn.execute("SELECT * FROM books WHERE doc_id = ?", (doc_id,)).fetchone()
        if (
            row is not None
            and not force
            and row["state"] == "done"
            and row["signature"] == signature
        ):
            return {
                "docId": doc_id,
                "state": "skipped",
                "chunks": row["chunks"],
                "embedded": row["embedded"],
                "seconds": round(time.perf_counter() - started, 3),
            }

        if (
            row is not None
            and not force
            and row["state"] == "done"
            and row["fingerprint"] == fingerprint
        ):
            # Same text: only metadata moved (a rename, a position save). Keep
            # the rows and remember the new signature so the next pass skips.
            conn.execute(
                "UPDATE books SET title = ?, signature = ?, chapters = ?, state = 'done',"
                " indexed_at = ? WHERE doc_id = ?",
                (title, signature, json.dumps(chapters, ensure_ascii=False), time.time(), doc_id),
            )
            embedded = _embed_doc(conn, doc_id)
            return {
                "docId": doc_id,
                "state": "unchanged",
                "chunks": row["chunks"],
                "embedded": embedded,
                "seconds": round(time.perf_counter() - started, 3),
            }

        resume = bool(
            row is not None
            and not force
            and row["state"] == "partial"
            and row["fingerprint"] == fingerprint
        )
        if not resume:
            _drop_book(conn, doc_id)
        start_unit = 0
        if resume:
            last = conn.execute(
                "SELECT MAX(unit) FROM chunks WHERE doc_id = ?", (doc_id,)
            ).fetchone()[0]
            start_unit = 0 if last is None else int(last) + 1

        conn.execute(
            "INSERT INTO books (doc_id, title, signature, fingerprint, units, chunks,"
            " embedded, chapters, state, indexed_at)"
            " VALUES (?,?,?,?,?,0,0,?,'partial',?)"
            " ON CONFLICT(doc_id) DO UPDATE SET title = excluded.title,"
            " signature = excluded.signature, fingerprint = excluded.fingerprint,"
            " units = excluded.units, chapters = excluded.chapters,"
            " state = 'partial', indexed_at = excluded.indexed_at",
            (
                doc_id, title, signature, fingerprint, len(units),
                json.dumps(chapters, ensure_ascii=False), time.time(),
            ),
        )
        conn.commit()

        batch: list[tuple] = []
        processed = 0
        for unit_index in range(start_unit, len(units)):
            if stop_after_units is not None and processed >= stop_after_units:
                break
            page = int(unit_pages[unit_index]) if unit_index < len(unit_pages) else 0
            chapter = _chapter_for(unit_index, chapters)
            for ordinal, (start, stop, text) in enumerate(_split_unit(units[unit_index])):
                batch.append((doc_id, unit_index, ordinal, page, chapter, start, stop, text))
            processed += 1
            if len(batch) >= INSERT_BATCH:
                _insert_chunks(conn, batch)
                batch.clear()
                conn.commit()
                if progress:
                    progress(unit_index + 1, len(units))
        if batch:
            _insert_chunks(conn, batch)
            conn.commit()
        if progress:
            progress(min(start_unit + processed, len(units)), len(units))

        chunk_count = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchone()[0]

        if stop_after_units is not None and start_unit + processed < len(units):
            conn.execute(
                "UPDATE books SET chunks = ?, indexed_at = ? WHERE doc_id = ?",
                (chunk_count, time.time(), doc_id),
            )
            return {
                "docId": doc_id,
                "state": "partial",
                "chunks": chunk_count,
                "embedded": 0,
                "seconds": round(time.perf_counter() - started, 3),
            }

        embedded = _embed_doc(conn, doc_id)
        conn.execute(
            "UPDATE books SET state = 'done', chunks = ?, embedded = ?, signature = ?,"
            " fingerprint = ?, title = ?, units = ?, indexed_at = ? WHERE doc_id = ?",
            (chunk_count, embedded, signature, fingerprint, title, len(units), time.time(), doc_id),
        )
        return {
            "docId": doc_id,
            "state": "indexed",
            "chunks": chunk_count,
            "embedded": embedded,
            "seconds": round(time.perf_counter() - started, 3),
        }


def _stats() -> list[dict] | None:
    """Library rows without bodies, straight from murmur.db.

    ``None`` means "could not read the library", which is different from an
    empty library: pruning the index on a transient database error would throw
    away every chunk.
    """
    try:
        return store.doc_stats()
    except sqlite3.Error:
        return None


def _books(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {row["doc_id"]: row for row in conn.execute("SELECT * FROM books")}


def _prune(conn: sqlite3.Connection) -> int:
    """Forget books whose document is no longer in the library."""
    stats = _stats()
    if stats is None:
        return 0
    known = {row["id"] for row in stats}
    removed = 0
    for doc_id in list(_books(conn)):
        if doc_id not in known:
            _drop_book(conn, doc_id)
            conn.execute("DELETE FROM progress WHERE doc_id = ?", (doc_id,))
            removed += 1
    return removed


def _targets(
    conn: sqlite3.Connection,
    force: bool = False,
    doc_ids: list[str] | None = None,
) -> list[dict]:
    """Books that need indexing, newest first."""
    stats = _stats()
    if stats is None:
        return []
    books = _books(conn)
    wanted = set(doc_ids) if doc_ids else None
    out: list[dict] = []
    for row in stats:
        doc_id = row["id"]
        if wanted is not None and doc_id not in wanted:
            continue
        book = books.get(doc_id)
        if force or book is None or book["state"] != "done" or book["signature"] != _signature(row):
            out.append(row)
    out.sort(key=lambda row: -(row.get("updated") or 0))
    return out


def index_all(
    *,
    force: bool = False,
    doc_ids: list[str] | None = None,
    stop_after_units: int | None = None,
) -> dict:
    """Index synchronously and return a summary. Tests and the API both use it."""
    with _db() as conn:
        _prune(conn)
        targets = _targets(conn, force, doc_ids)
    results = [
        index_book(target["id"], force=force, stop_after_units=stop_after_units)
        for target in targets
    ]
    if semantic.available() and stop_after_units is None:
        backfill_embeddings()
    return {
        "documents": len(results),
        "indexed": sum(1 for r in results if r["state"] in {"indexed", "unchanged"}),
        "skipped": sum(1 for r in results if r["state"] == "skipped"),
        "chunks": sum(r["chunks"] for r in results),
        "embedded": sum(r["embedded"] for r in results),
        "seconds": round(sum(r["seconds"] for r in results), 3),
        "results": results,
    }


def backfill_embeddings() -> int:
    """Embed chunks of books indexed while the model was missing.

    The download cannot be retroactive by itself, so this is the bridge: run it
    after a model download and at the end of every index pass.
    """
    if not semantic.available():
        return 0
    with _db() as conn:
        rows = conn.execute(
            "SELECT doc_id FROM books WHERE embedded < chunks ORDER BY indexed_at"
        ).fetchall()
    total = 0
    for row in rows:
        with _db() as conn:
            total += _embed_doc(conn, row["doc_id"])
    return total


def forget(doc_id: str) -> None:
    """Drop a document's index and its reading history (used on delete)."""
    with _db() as conn:
        _drop_book(conn, doc_id)
        conn.execute("DELETE FROM progress WHERE doc_id = ?", (doc_id,))


def record_read(doc_id: str, position: int = 0) -> None:
    """Remember that a document was opened or its position was saved.

    This is what ``recent=7d`` filters on: activity, not import date.
    """
    if not doc_id:
        return
    now = time.time()
    try:
        with _db() as conn:
            conn.execute(
                "INSERT INTO progress (doc_id, position, first_read, last_read)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(doc_id) DO UPDATE SET position = excluded.position,"
                " last_read = excluded.last_read,"
                " first_read = COALESCE(progress.first_read, excluded.first_read)",
                (doc_id, int(position or 0), now, now),
            )
    except (sqlite3.Error, OSError):
        pass


# ---------------------------------------------------------------- background
def note_document(doc_id: str) -> None:
    """Queue a freshly imported document for the next index pass.

    This deliberately does not start a worker: the HTTP endpoint, the search
    panel and startup are the places that decide *when* to spend CPU, and a
    thread spawned from an import would outlive the request that caused it.
    """
    if not doc_id:
        return
    with _work_lock:
        if doc_id not in _queued:
            _queued.append(doc_id)


def start_background(*, force: bool = False, delay: float = 0.0) -> dict:
    """Start the index worker if it is not already running.

    Called from startup and after imports. It never blocks the caller: the
    database is touched on the worker thread only.
    """
    global _worker, _force_all
    with _work_lock:
        if force:
            _force_all = True
        if _work["running"]:
            return status()
        _stop.clear()
        _work.update(running=True, current=None, done=0, total=0, lastError="")
        _worker = threading.Thread(
            target=_run, kwargs={"delay": delay}, name="search-index", daemon=True
        )
        thread = _worker
    thread.start()
    return status()


def stop(timeout: float = 5.0) -> None:
    """Ask the worker to stop at the next document boundary (tests, shutdown)."""
    _stop.set()
    thread = _worker
    if thread is not None and thread.is_alive():
        thread.join(timeout)
    with _work_lock:
        _queued.clear()


def _scan(force: bool) -> list[dict]:
    """Targets for one pass, retrying a database another writer is holding."""
    last: Exception | None = None
    for attempt in range(4):
        try:
            with _db() as conn:
                _prune(conn)
                return _targets(conn, force)
        except sqlite3.OperationalError as exc:
            last = exc
            text = str(exc).lower()
            if "lock" not in text and "busy" not in text:
                raise
            time.sleep(0.25 * (attempt + 1))
    raise last  # type: ignore[misc]


def _run(delay: float = 0.0) -> None:
    """Index everything that needs it, then backfill embeddings.

    A forced pass requested while the worker is busy is picked up on the next
    loop instead of being dropped.
    """
    global _force_all
    try:
        if delay:
            time.sleep(delay)
        processed = 0
        forced = False
        while not _stop.is_set():
            with _work_lock:
                force = _force_all and not forced
            targets = _scan(force)
            with _work_lock:
                _work["total"] = max(_work["total"], processed + len(targets) + len(_queued))
            cursor = 0
            while not _stop.is_set():
                with _work_lock:
                    if _queued:
                        doc_id = _queued.pop()
                    elif cursor < len(targets):
                        doc_id = targets[cursor]["id"]
                        cursor += 1
                    else:
                        break
                    _work["current"] = {"docId": doc_id}
                processed += 1
                try:
                    result = index_book(doc_id, force=force)
                    with _work_lock:
                        _work["current"]["state"] = result["state"]
                        _work["current"]["chunks"] = result["chunks"]
                except Exception as exc:  # one bad book must not stop the rest
                    with _work_lock:
                        _work["lastError"] = f"{doc_id}: {exc}"
                with _work_lock:
                    _work["done"] = processed
                    _work["total"] = max(_work["total"], processed)
            if force:
                forced = True
            with _work_lock:
                again = bool(_queued) or (_force_all and not forced)
            if not again:
                break
        if not _stop.is_set():
            backfill_embeddings()
    except Exception as exc:
        with _work_lock:
            _work["lastError"] = str(exc)
    finally:
        with _work_lock:
            _work.update(running=False, current=None, finished=time.time())
            _force_all = False


# --------------------------------------------------------------------- status
def status() -> dict:
    """What the index holds, what is pending, and whether meaning search is on."""
    try:
        with _db() as conn:
            totals = conn.execute(
                "SELECT COUNT(*) AS books, COALESCE(SUM(chunks), 0) AS chunks,"
                " COALESCE(SUM(embedded), 0) AS embedded,"
                " COALESCE(MAX(indexed_at), 0) AS updated FROM books"
            ).fetchone()
            pending = _targets(conn)
        size = db_path().stat().st_size if db_path().exists() else 0
    except sqlite3.Error as exc:
        return {
            "books": 0,
            "chunks": 0,
            "embedded": 0,
            "pending": 0,
            "sizeBytes": 0,
            "path": str(db_path()),
            "updated": 0,
            "error": str(exc),
            "indexing": dict(_work),
            "semantic": semantic.status(),
        }
    with _work_lock:
        work = dict(_work)
        work["queued"] = len(_queued)
    return {
        "books": totals["books"],
        "chunks": totals["chunks"],
        "embedded": totals["embedded"],
        "pending": len(pending),
        "pendingDocs": [{"docId": row["id"], "title": row["title"]} for row in pending[:20]],
        "sizeBytes": size,
        "path": str(db_path()),
        "updated": totals["updated"],
        "indexing": work,
        "semantic": semantic.status(),
        "mode": "hybrid" if semantic.available() else "lexical",
    }


# --------------------------------------------------------------------- search
def _lexical_rows(
    conn: sqlite3.Connection,
    terms: list[str],
    cutoff: float | None,
    doc_id: str | None,
    limit: int,
) -> list[int]:
    """Chunk ids in BM25 order. FTS5's bm25() is negative: lower is better."""
    if not terms:
        return []
    expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    sql = [
        "SELECT c.id FROM chunks_fts",
        "JOIN chunks c ON c.id = chunks_fts.rowid",
    ]
    params: list[Any] = []
    if cutoff is not None:
        sql.append("JOIN progress p ON p.doc_id = c.doc_id")
    sql.append("WHERE chunks_fts MATCH ?")
    params.append(expression)
    if cutoff is not None:
        sql.append("AND p.last_read >= ?")
        params.append(cutoff)
    if doc_id:
        sql.append("AND c.doc_id = ?")
        params.append(doc_id)
    sql.append("ORDER BY bm25(chunks_fts) LIMIT ?")
    params.append(limit)
    try:
        return [row[0] for row in conn.execute(" ".join(sql), params)]
    except sqlite3.OperationalError:
        # A query that the FTS tokenizer reduces to nothing.
        return []


def _meta_rows(conn: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    """Chunk rows plus the book title and reading recency, in id order."""
    out: dict[int, sqlite3.Row] = {}
    for start in range(0, len(ids), 400):
        block = ids[start : start + 400]
        placeholders = ",".join("?" for _ in block)
        for row in conn.execute(
            "SELECT c.id, c.doc_id, c.unit, c.ord, c.page, c.chapter, c.text,"
            " bk.title, bk.units AS unit_count, bk.chapters AS chapters,"
            " COALESCE(p.last_read, 0) AS last_read"
            " FROM chunks c JOIN books bk ON bk.doc_id = c.doc_id"
            " LEFT JOIN progress p ON p.doc_id = c.doc_id"
            f" WHERE c.id IN ({placeholders})",
            block,
        ):
            out[row["id"]] = row
    return out


def _semantic_rows(
    conn: sqlite3.Connection,
    query_vector: np.ndarray,
    cutoff: float | None,
    doc_id: str | None,
    limit: int,
) -> list[tuple[int, float]]:
    """Top chunk ids by cosine similarity, scanned in blocks.

    Streaming blocks keeps memory bounded on a large library: only the running
    top-``limit`` and one block of vectors are ever resident.
    """
    sql = ["SELECT v.chunk_id AS id, v.vec AS vec FROM vectors v"]
    params: list[Any] = []
    if cutoff is not None or doc_id:
        sql.append("JOIN chunks c ON c.id = v.chunk_id")
    if cutoff is not None:
        sql.append("JOIN progress p ON p.doc_id = c.doc_id")
    conditions: list[str] = []
    if cutoff is not None:
        conditions.append("p.last_read >= ?")
        params.append(cutoff)
    if doc_id:
        conditions.append("c.doc_id = ?")
        params.append(doc_id)
    if conditions:
        sql.append("WHERE " + " AND ".join(conditions))
    sql.append("ORDER BY v.chunk_id")
    cursor = conn.execute(" ".join(sql), params)

    best_ids = np.zeros(0, dtype=np.int64)
    best_scores = np.zeros(0, dtype=np.float32)
    while True:
        block = cursor.fetchmany(SEMANTIC_SCAN_BLOCK)
        if not block:
            break
        ids = np.fromiter((row["id"] for row in block), dtype=np.int64, count=len(block))
        matrix = np.vstack([np.frombuffer(row["vec"], dtype="<f4") for row in block])
        if matrix.shape[1] != query_vector.shape[0]:
            continue
        scores = matrix @ query_vector
        keep = min(limit, scores.shape[0])
        if keep <= 0:
            continue
        local = np.argpartition(-scores, keep - 1)[:keep]
        best_ids = np.concatenate([best_ids, ids[local]])
        best_scores = np.concatenate([best_scores, scores[local]])
        if best_scores.shape[0] > limit:
            top = np.argpartition(-best_scores, limit - 1)[:limit]
            best_ids, best_scores = best_ids[top], best_scores[top]
    if best_ids.size == 0:
        return []
    order = np.argsort(-best_scores, kind="stable")
    ranked = [(int(best_ids[i]), float(best_scores[i])) for i in order]
    best = ranked[0][1]
    if best < SEMANTIC_FLOOR:
        return []
    cutoff_score = max(SEMANTIC_FLOOR, best * SEMANTIC_RELATIVE)
    return [(chunk_id, score) for chunk_id, score in ranked if score >= cutoff_score]


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, stop in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    return merged


def snippet(text: str, terms: list[str], limit: int = SNIPPET_CHARS) -> dict:
    """A window of a chunk around the first match, with match ranges.

    The page builds the markup from plain text plus ranges, so book text can
    never be injected as HTML. Offsets count the ellipsis prefix when present.
    """
    body_text = " ".join((text or "").split())
    lower = body_text.lower()
    first = None
    for term in terms:
        match = re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", lower)
        if match and (first is None or match.start() < first):
            first = match.start()
    start = 0
    if first is not None and first > limit // 3:
        start = max(0, first - limit // 3)
        space = body_text.find(" ", start)
        if 0 <= space < first:
            start = space + 1
    stop = min(len(body_text), start + limit)
    if stop < len(body_text):
        space = body_text.rfind(" ", start, stop)
        if space > start + limit // 2:
            stop = space
    window = body_text[start:stop]
    truncated_start = start > 0
    truncated_end = stop < len(body_text)
    ranges = []
    for term in terms:
        for match in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", window, re.IGNORECASE):
            ranges.append((match.start(), match.end()))
    shift = 1 if truncated_start else 0
    merged = [[s + shift, e + shift] for s, e in _merge_ranges(ranges)]
    return {
        "text": ("…" if truncated_start else "") + window + ("…" if truncated_end else ""),
        "highlights": merged,
        "truncatedStart": truncated_start,
        "truncatedEnd": truncated_end,
    }


def _recency_factor(last_read: float, now: float) -> float:
    if not last_read:
        return 0.0
    days = max(0.0, now - float(last_read)) / 86400.0
    return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)


def search(
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    recent: str | None = None,
    doc_id: str | None = None,
) -> dict:
    """Hybrid search over the indexed library.

    Returns ranked hits plus enough state for the UI to be honest about what it
    did: ``mode`` is ``hybrid`` only when the query vector was actually produced.
    """
    started = time.perf_counter()
    query = (query or "").strip()
    if not query:
        raise ValueError("Type something to search for.")
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    cutoff = _recent_cutoff(recent)
    terms = _terms(query)

    semantic_status = semantic.status()
    enriched = semantic.available()
    semantic_status["available"] = enriched
    mode = "lexical"
    query_vector: np.ndarray | None = None
    semantic_error = ""
    if enriched:
        try:
            query_vector = semantic.embed([query])[0]
            mode = "hybrid"
        except Exception as exc:
            semantic_error = str(exc)

    scores: dict[int, dict[str, Any]] = {}

    def bump(chunk_id: int, value: float, **extra: Any) -> None:
        entry = scores.setdefault(chunk_id, {"score": 0.0, "lexical": None, "semantic": None})
        entry["score"] += value
        entry.update(extra)

    with _db() as conn:
        lexical_ids = _lexical_rows(conn, terms, cutoff, doc_id, LEXICAL_CANDIDATES)
        for rank, chunk_id in enumerate(lexical_ids, start=1):
            bump(chunk_id, 1.0 / (RRF_K + rank), lexical=rank)

        semantic_ranked = (
            _semantic_rows(conn, query_vector, cutoff, doc_id, SEMANTIC_CANDIDATES)
            if query_vector is not None
            else []
        )
        for rank, (chunk_id, _similarity) in enumerate(semantic_ranked, start=1):
            bump(chunk_id, 1.0 / (RRF_K + rank), semantic=rank)

        if not scores:
            return {
                "query": query,
                "mode": mode,
                "limit": limit,
                "offset": offset,
                "total": 0,
                "tookMs": round((time.perf_counter() - started) * 1000, 1),
                "recent": recent or "",
                "terms": terms,
                "semantic": {
                    "available": semantic_status["available"],
                    "state": semantic_status["state"],
                    "model": semantic_status["model"],
                    "error": semantic_error or semantic_status["error"],
                },
                "hits": [],
            }

        rows = _meta_rows(conn, list(scores))
        now = time.time()
        for chunk_id, entry in list(scores.items()):
            row = rows.get(chunk_id)
            if row is None:  # deleted between the two queries
                scores.pop(chunk_id, None)
                continue
            entry["row"] = row
            entry["recency"] = _recency_factor(row["last_read"], now)
            entry["score"] += RECENCY_WEIGHT * entry["recency"]

    ordered = sorted(
        scores.items(),
        key=lambda item: (
            -item[1]["score"],
            item[1]["row"]["doc_id"],
            item[1]["row"]["unit"],
            item[1]["row"]["ord"],
        ),
    )
    total = len(ordered)
    page = ordered[offset : offset + limit]

    chapter_titles: dict[str, dict[int, str]] = {}

    def chapter_title_for(row: sqlite3.Row) -> str:
        titles = chapter_titles.get(row["doc_id"])
        if titles is None:
            titles = {}
            try:
                for entry in json.loads(row["chapters"] or "[]"):
                    titles[int(entry[1])] = str(entry[2] or "")
            except (TypeError, ValueError, IndexError):
                titles = {}
            chapter_titles[row["doc_id"]] = titles
        try:
            return titles.get(int(row["chapter"]), "")
        except (TypeError, ValueError):
            return ""

    hits: list[dict] = []
    for chunk_id, entry in page:
        row = entry["row"]
        chapter_title = chapter_title_for(row)
        hits.append(
            {
                "chunkId": chunk_id,
                "docId": row["doc_id"],
                "title": row["title"],
                "unit": row["unit"],
                "ord": row["ord"],
                "unitCount": row["unit_count"],
                "page": row["page"],
                "chapter": row["chapter"],
                "chapterTitle": chapter_title,
                "score": round(entry["score"], 6),
                "recency": round(entry["recency"], 3),
                "lexicalRank": entry["lexical"],
                "semanticRank": entry["semantic"],
                "snippet": snippet(row["text"], terms),
                "location": {
                    "unit": row["unit"],
                    "unitCount": row["unit_count"],
                    "page": row["page"],
                    "chapter": row["chapter"],
                    "chapterTitle": chapter_title,
                },
                "jump": {
                    "docId": row["doc_id"],
                    "unit": row["unit"],
                    "page": row["page"],
                    "chunkId": chunk_id,
                    "terms": terms,
                },
            }
        )

    return {
        "query": query,
        "mode": mode,
        "limit": limit,
        "offset": offset,
        "total": total,
        "tookMs": round((time.perf_counter() - started) * 1000, 1),
        "recent": recent or "",
        "terms": terms,
        "semantic": {
            "available": semantic_status["available"],
            "state": semantic_status["state"],
            "model": semantic_status["model"],
            "error": semantic_error or semantic_status["error"],
        },
        "hits": hits,
    }
