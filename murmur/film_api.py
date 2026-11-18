"""Shared film/engine HTTP API.

The same router is mounted by the Rhapsode server and by the standalone
Murmur engine sidecar, so both expose an identical contract.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import film, imaging, store
from .voices import DEFAULT_VOICE, VOICES

router = APIRouter()


class FilmSceneIn(BaseModel):
    doc: str
    chapter: int = 0
    index: int = 0
    count: int = 10


class FilmRegenerateIn(BaseModel):
    doc: str
    chapter: int = 0
    index: int = 0
    mode: str = "whole"
    rect: list[float] | None = None
    prompt: str = ""
    seed: int | None = None


def _document(doc_id: str) -> dict:
    document = store.get_doc(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _chapter(document: dict, chapter: int) -> int:
    return max(0, min(int(chapter), film.chapter_count(document) - 1))


def _url(doc_id: str, chapter: int, index: int, path: Path) -> str:
    return (
        f"/api/film/image/{doc_id}/{chapter}/{index}.png"
        f"?v={int(path.stat().st_mtime)}"
    )


@router.get("/api/film/plan")
def film_plan(doc: str, chapter: int = 0) -> dict:
    document = _document(doc)
    chapter = _chapter(document, chapter)
    scenes = film.plan_chapter(document, chapter)
    return {
        "doc": document["id"],
        "chapter": chapter,
        "chapters": film.chapter_count(document),
        "scenes": [scene.public() for scene in scenes],
        "progress": film.painter.status(document, chapter, scenes),
        "activity": film.scene_activity(document, chapter, scenes),
    }


@router.get("/api/film/activity")
def film_activity(doc: str | None = None, chapter: int = 0) -> dict:
    """What the stills engine is doing, for any surface that needs to say so.

    Without a document this is the raw painter state (Settings); with one the
    counts are the chapter's own, so Cinema and the queue cannot disagree.
    """
    if not doc:
        return film.activity.snapshot()
    document = _document(doc)
    chapter = _chapter(document, chapter)
    return film.scene_activity(document, chapter, film.plan_chapter(document, chapter))


@router.get("/api/film/images")
def film_images(doc: str, chapter: int = 0) -> dict:
    document = _document(doc)
    chapter = _chapter(document, chapter)
    scenes = film.plan_chapter(document, chapter)
    model = film.film_model()[0]
    images = []
    for scene in scenes:
        path = film.scene_image_path(document["id"], chapter, scene.index, model)
        images.append(
            {
                "index": scene.index,
                "first": scene.units[0] if scene.units else 0,
                "last": scene.units[-1] if scene.units else 0,
                "text": scene.text[:160],
                "url": _url(document["id"], chapter, scene.index, path) if film.still_delivered(path) else None,
            }
        )
    return {"doc": document["id"], "chapter": chapter, "images": images}


@router.post("/api/film/scene")
def film_scene(payload: FilmSceneIn) -> dict:
    document = _document(payload.doc)
    chapter = _chapter(document, payload.chapter)
    scenes = film.plan_chapter(document, chapter)
    if payload.index < 0 or payload.index >= len(scenes):
        raise HTTPException(status_code=404, detail="Scene out of range")

    scene = scenes[payload.index]
    path = film.scene_image_path(document["id"], chapter, scene.index)
    if not film.still_delivered(path):
        film.painter.enqueue(document, chapter, [scene])
        return {
            "ready": False,
            "index": scene.index,
            "progress": film.painter.status(document, chapter, scenes),
            "activity": film.scene_activity(document, chapter, scenes),
        }

    return {
        "ready": True,
        "url": _url(document["id"], chapter, scene.index, path),
        "index": scene.index,
        "duration": round(scene.duration, 2),
        "backend": imaging.image_gen.backend(film.film_model()[0]).name,
        "model": film.film_model()[0],
        "placeholder": film.placeholder_path(path).exists(),
        "activity": film.scene_activity(document, chapter, scenes),
    }


@router.post("/api/film/scene/regenerate")
def film_regenerate(payload: FilmRegenerateIn) -> dict:
    document = _document(payload.doc)
    chapter = _chapter(document, payload.chapter)
    scenes = film.plan_chapter(document, chapter)
    if payload.index < 0 or payload.index >= len(scenes):
        raise HTTPException(status_code=404, detail="Scene out of range")
    try:
        path = film.regenerate_scene(
            document, chapter, payload.index,
            mode="region" if payload.mode == "region" else "whole",
            rect=payload.rect, prompt=payload.prompt, seed=payload.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ready": True,
        "index": payload.index,
        "url": _url(document["id"], chapter, payload.index, path),
        "mode": "region" if payload.mode == "region" else "whole",
    }


@router.post("/api/film/prefetch")
def film_prefetch(payload: FilmSceneIn) -> dict:
    document = _document(payload.doc)
    chapter = _chapter(document, payload.chapter)
    scenes = film.plan_chapter(document, chapter)
    queued = film.painter.retarget(
        document, chapter, scenes,
        start=payload.index, count=payload.count,
    )
    return {
        "queued": queued,
        "progress": film.painter.status(document, chapter, scenes),
    }


@router.get("/api/film/progress")
def film_progress(doc: str, chapter: int = 0) -> dict:
    document = _document(doc)
    chapter = _chapter(document, chapter)
    scenes = film.plan_chapter(document, chapter)
    return film.painter.status(document, chapter, scenes)


@router.get("/api/film/image/{doc_id}/{chapter}/{index}.png")
def film_image(doc_id: str, chapter: int, index: int):
    path = film.scene_image_path(doc_id, chapter, index)
    # A placeholder prompt card is a delivered scene: serving it is what lets
    # the reader see why the stills are missing instead of watching a spinner.
    if not film.still_delivered(path):
        raise HTTPException(status_code=404, detail="Scene image not rendered yet")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/api/docs/{doc_id}/film")
def start_film(doc_id: str, payload: dict | None = None) -> dict:
    document = _document(doc_id)
    data = payload or {}
    chapter = int(data.get("chapter", 0))
    voice = data.get("voice") or document.get("voice") or DEFAULT_VOICE
    if voice not in VOICES:
        voice = DEFAULT_VOICE
    return film.start_job(document, chapter, voice, auto=bool(data.get("auto")))


@router.post("/api/film/stop-auto")
def stop_auto_films() -> dict:
    """Drain queued auto jobs; the running chapter is left to its own Cancel."""
    return {"stopped": film.stop_auto()}


@router.get("/api/film/{job_id}")
def film_status(job_id: str) -> dict:
    job = film.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Film job not found")
    return job


@router.get("/api/film/{job_id}/video")
def film_video(job_id: str):
    job = film.JOBS.get(job_id)
    if not job or job.get("state") != "done":
        raise HTTPException(status_code=404, detail="Film is not ready")
    path = Path(job["file"])
    if not path.exists():
        raise HTTPException(status_code=404)
    safe = re.sub(r"[^\w\s-]", "", job.get("title", "murmur"))[:80].strip() or "murmur"
    return FileResponse(path, media_type="video/mp4", filename=f"{safe}.mp4")


@router.delete("/api/film/{job_id}")
def cancel_film(job_id: str) -> dict:
    job = film.JOBS.get(job_id)
    if job:
        job["cancelled"] = True
        if job.get("state") == "queued":
            job["state"] = "cancelled"
    return {"ok": True}
