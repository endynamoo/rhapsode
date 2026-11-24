"""HTTP surface for library search.

Kept separate from `server.py` for the same reason `film_api.py` is: the routes
are about one feature, and the shapes are checked by their own tests. All
handlers return plain JSON and follow the app's convention of a 400 with a
human `detail` for a bad request and 202 for work that continues in the
background.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import search, semantic, store

router = APIRouter()


class IndexIn(BaseModel):
    force: bool = False
    doc: str | None = None


class ModelIn(BaseModel):
    download: bool = True


@router.get("/api/search")
def search_library(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    recent: str = "",
    doc: str = "",
) -> dict:
    """Ranked hits across the library, with a jump target for each one."""
    try:
        return search.search(
            q,
            limit=limit,
            offset=offset,
            recent=recent or None,
            doc_id=doc or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/search/status")
def search_status() -> dict:
    """Index coverage, pending books, and whether meaning search is available."""
    return search.status()


@router.post("/api/search/index", status_code=202)
def rebuild_index(payload: IndexIn | None = None) -> dict:
    """Start indexing in the background; returns immediately with the status."""
    data = payload or IndexIn()
    if data.doc:
        if not store.get_doc(data.doc):
            raise HTTPException(status_code=404, detail="Document not found")
        search.note_document(data.doc)
        # note_document only queues - by design, so an import cannot spawn a thread that
        # outlives its request. Here the caller asked for indexing, so the worker has to
        # be running or the queued book is never read; the endpoint answered "started".
        search.start_background()
        return {"started": True, "status": search.status()}
    search.start_background(force=data.force)
    return {"started": True, "status": search.status()}


@router.post("/api/search/model", status_code=202)
def download_model(payload: ModelIn | None = None) -> dict:
    """Download the optional embedding model (~23 MB) in the background.

    Lexical search needs none of this; the button exists so the UI can offer
    meaning search without hiding what it costs.
    """
    data = payload or ModelIn()
    if not data.download:
        raise HTTPException(status_code=400, detail="download must be true")
    if not semantic.enabled():
        raise HTTPException(
            status_code=400,
            detail="Semantic search is switched off (MURMUR_SEMANTIC=off).",
        )
    already = semantic.available()
    try:
        state = semantic.start_download()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"started": not already, "semantic": state}
