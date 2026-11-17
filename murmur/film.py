"""Turn a chapter into a film: scenes, stills, narration, subtitles, MP4."""
from __future__ import annotations

import bisect
import hashlib
import json
import queue
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config, engine, imaging, ocr, polish, power, proc, prompts, speak, store

SCENE_SECONDS = 75.0
SCENE_MAX_UNITS = 20
SCENE_GAP = 0.10
XFADE = 1.2
SCENE_VERSION = "v9"

# Bump when clip/compose output would change for the same plan and images.
RENDER_VERSION = "v3"
FPS = 30
OUT_W = 1920
OUT_H = 1080
CLIP_W = 2560
CLIP_H = 1440


def still_ready(path) -> bool:
    """True when a still exists and is real art, not a placeholder prompt card."""
    try:
        return path.exists() and path.stat().st_size >= imaging.MIN_STILL_BYTES
    except OSError:
        return False


@dataclass
class Scene:
    index: int
    units: list[int]
    text: str
    prompt: str
    seed: int
    duration: float = 0.0
    start: float = 0.0

    def public(self) -> dict:
        return {
            "index": self.index,
            "first": self.units[0] if self.units else 0,
            "last": self.units[-1] if self.units else 0,
            "duration": round(self.duration, 2),
            "text": self.text[:160],
        }


_PLANS: dict[tuple[str, int, str], list[Scene]] = {}
_PLAN_LOCK = threading.Lock()
_JOB_LOCK = threading.Lock()
JOBS: dict[str, dict] = {}
_FILM_QUEUE: queue.Queue = queue.Queue()
_FILM_WORKER_LOCK = threading.Lock()
_FILM_WORKER: threading.Thread | None = None


def _estimate(text: str) -> float:
    return max(1.2, len(text) / 15.5)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:220]


def _clip_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


SHOT_TYPES = (
    "establishing wide shot",
    "medium two-shot",
    "close-up",
    "close-up",
    "low-angle medium shot",
    "detail insert",
)


def shot_for_scene(scene: Scene) -> str:
    """Rotate framing deliberately instead of defaulting to wide low-angle."""
    text = scene.text or ""
    base = SHOT_TYPES[scene.index % len(SHOT_TYPES)]
    if '"' in text or "“" in text or "”" in text:
        if base == "establishing wide shot":
            return "over-the-shoulder shot"
        if base == "detail insert":
            return "close-up"
    return base


def _seed(doc_id: str, chapter: int, index: int) -> int:
    digest = hashlib.sha1(f"{doc_id}|{chapter}|{index}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (2**31 - 1)


def film_model() -> tuple[str, int, int]:
    """Quality preset: model key, width, height. Both tiers are FLUX.2 klein."""
    quality = store.get_settings().get("filmQuality", "high")
    if quality == "fast":
        return "flux2-klein-4b-fast", 1280, 720
    return "flux2-klein-4b", 1920, 1080


def chapter_unit_indices(doc: dict, chapter: int) -> list[int]:
    pages = doc.get("unitPages") or []
    if not pages or (doc.get("pageCount") or 0) <= 1:
        return list(range(len(doc["units"])))
    return [i for i, page in enumerate(pages) if page == chapter]


def chapter_count(doc: dict) -> int:
    return max(1, int(doc.get("pageCount") or 1))


def plan_chapter(doc: dict, chapter: int) -> list[Scene]:
    key = (doc["id"], chapter)
    with _PLAN_LOCK:
        if key in _PLANS:
            return _PLANS[key]

    indices = chapter_unit_indices(doc, chapter)
    units = doc["units"]
    if not indices:
        return []

    durations = [_estimate(units[i]) for i in indices]
    total = sum(durations)
    target = max(1, min(len(indices), round(total / SCENE_SECONDS)))
    if total >= 240 and len(indices) >= 4:
        target = max(3, target)

    # Split on estimated time boundaries so each image covers a similar slice.
    cumulative: list[float] = []
    running = 0.0
    for length in durations:
        running += length
        cumulative.append(running)

    splits: list[int] = []
    for part in range(1, target):
        desired = total * part / target
        position = bisect.bisect_left(cumulative, desired)
        lower = splits[-1] + 1 if splits else 1
        upper = len(indices) - (target - part)
        splits.append(max(lower, min(position, upper)))

    bounds = [0, *splits, len(indices)]
    groups = [indices[bounds[i]:bounds[i + 1]] for i in range(target)]

    # Keep any single prompt/beat from swallowing a whole chapter.
    packed: list[list[int]] = []
    for group in groups:
        if not group:
            continue
        for start in range(0, len(group), SCENE_MAX_UNITS):
            packed.append(group[start:start + SCENE_MAX_UNITS])

    scenes: list[Scene] = []
    for group in packed:
        text = " ".join(units[i] for i in group)
        scenes.append(
            Scene(
                index=len(scenes),
                units=list(group),
                text=text,
                prompt=f"{config.FILM_STYLE}. {_clean(text)}",
                seed=_seed(doc["id"], chapter, len(scenes)),
            )
        )

    with _PLAN_LOCK:
        _PLANS[key] = scenes
    return scenes


def scene_for_unit(scenes: list[Scene], unit: int) -> Scene | None:
    for scene in scenes:
        if scene.units and scene.units[0] <= unit <= scene.units[-1]:
            return scene
    return None


def scene_image_path(doc_id: str, chapter: int, index: int,
                     model: str | None = None) -> Path:
    model = model or film_model()[0]
    return (
        config.FILM_DIR / doc_id / "scenes"
        / f"c{chapter}_s{index:05d}_{SCENE_VERSION}_{model}.png"
    )


def _fallback_prompt(scene: Scene, prev_text: str = "", next_text: str = "",
                     bible: str = "", shot: str = "") -> str:
    parts = [config.FILM_STYLE]
    if shot:
        parts.append(shot)
    parts.append(_clip_text(scene.text, 320))
    return ". ".join(part for part in parts if part)


def _enforce_shot(text: str, shot: str) -> str:
    prefix = re.compile(
        r"^(?:an?\s+)?(?:(?:establishing\s+)?"
        r"(?:wide|medium(?:\s+two[- ]shot)?|close[- ]up|two[- ]shot|"
        r"over[- ]the[- ]shoulder|low[- ]angle|high[- ]angle)"
        r"(?:\s+shot)?(?:\s+of)?\s*,?\s*)+",
        re.I,
    )
    stripped = prefix.sub("", text, count=1).strip(" ,.;:")
    return f"{shot}, {stripped}" if stripped else shot


_PROMPT_LOCK = threading.RLock()


def _prompt_path(doc_id: str, chapter: int) -> Path:
    return config.FILM_DIR / doc_id / f"prompts_c{chapter}_v6.json"


def _read_cache(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                data.setdefault("prompts", {})
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"prompts": {}}


def _write_cache(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    except OSError:
        pass


def doc_bible(doc: dict) -> str:
    path = config.FILM_DIR / doc["id"] / "bible_v1.txt"
    try:
        if path.exists():
            return path.read_text("utf-8").strip()
    except OSError:
        pass

    sample = (doc.get("text") or "")[:3000]
    if not sample:
        sample = " ".join(doc.get("units", [])[:15])[:3000]
    try:
        bible = prompts.writer.bible(sample).strip()
    except Exception:
        bible = ""
    if bible:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(bible + "\n", "utf-8")
        except OSError:
            pass
    return bible


def compose_prompt(doc: dict, chapter: int, scene: Scene) -> str:
    with _PROMPT_LOCK:
        path = _prompt_path(doc["id"], chapter)
        cache = _read_cache(path)
        key = str(scene.index)
        if key in cache["prompts"]:
            return cache["prompts"][key]

        bible = doc_bible(doc)
        cast = doc_cast(doc, chapter)
        cast_text = "; ".join(
            f"{member['name']}: {member['description']}" for member in cast[:2]
        )
        context = bible + (f"\nCast: {cast_text}" if cast_text else "")
        scenes = plan_chapter(doc, chapter)
        index = scene.index
        prev_text = scenes[index - 1].text if index > 0 else ""
        next_text = scenes[index + 1].text if index + 1 < len(scenes) else ""

        text = ""
        shot = shot_for_scene(scene)
        try:
            text = prompts.writer.prompt(
                scene.text, prev_text, next_text, context, shot=shot,
            ).strip()
        except TypeError:
            try:
                text = prompts.writer.prompt(scene.text, prev_text, next_text, context).strip()
            except Exception:
                text = ""
        except Exception:
            text = ""
        if not text:
            text = _fallback_prompt(scene, prev_text, next_text, context, shot)
        else:
            text = re.sub(
                r"\b(glyphs?|lettering|lettered|writing|written|signs?|labels?|"
                r"captions?|speech bubbles?|text)\b", "", text, flags=re.I,
            )
            text = re.sub(r"\s{2,}", " ", text).strip(" ,.;")
            text = _enforce_shot(text, shot)
            if cast_text:
                text += f". Character reference: {cast_text}. Keep these exact designs"
            if "manga" not in text.lower():
                text += ", monochrome manga panel"
            text += (
                ", single continuous full-bleed illustration, no panels, no gutters, "
                "no split screen, no collage, consistent recurring character design, "
                "black ink linework, screentone shading, plain surfaces, no writing, "
                "no lettering, no signs, no glyphs, no captions, no speech bubbles."
            )

        cache["prompts"][key] = text
        _write_cache(path, cache)
        return text


def character_sheet_path(doc_id: str) -> Path:
    return config.FILM_DIR / doc_id / "character_sheet_v2.png"


def character_reference_path(doc_id: str) -> Path:
    return config.FILM_DIR / doc_id / "character_ref_v2.png"


def cast_path(doc_id: str) -> Path:
    return config.FILM_DIR / doc_id / "cast_v1.json"


def character_portrait_path(doc_id: str, slug: str) -> Path:
    return config.FILM_DIR / doc_id / f"character_{slug}_v1.png"


def character_face_path(doc_id: str, slug: str) -> Path:
    return config.FILM_DIR / doc_id / f"character_{slug}_face_v1.png"


def character_body_path(doc_id: str, slug: str) -> Path:
    return config.FILM_DIR / doc_id / f"character_{slug}_body_v1.png"


def cast_group_path(doc_id: str) -> Path:
    return config.FILM_DIR / doc_id / "cast_group_v1.png"


def _cast_sample(doc: dict, chapter: int) -> str:
    parts = []
    text = doc.get("text") or ""
    if text:
        parts.append(text[:3000])
    indices = chapter_unit_indices(doc, chapter)
    if indices:
        parts.append(" ".join(doc["units"][i] for i in indices)[:3000])
    return "\n\n".join(parts)[:6000]


def doc_cast(doc: dict, chapter: int = 0) -> list[dict]:
    """Recurring cast for the book, cached so the prompt writer runs once."""
    path = cast_path(doc["id"])
    try:
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, list) and data:
                return data
    except (OSError, json.JSONDecodeError):
        pass

    raw = ""
    try:
        raw = prompts.writer.cast(_cast_sample(doc, chapter))
    except Exception:
        raw = ""

    cast: list[dict] = []
    for line in raw.splitlines():
        parts = [part.strip() for part in line.split("|") if part.strip()]
        if len(parts) < 3:
            continue
        name, role = parts[0], parts[1]
        description = " ".join(parts[2:])
        if name.lower().startswith("narrator") or role.lower() in {"observer", "narrator"}:
            role = "protagonist"
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "narrator"
        if any(member["slug"] == slug for member in cast):
            continue
        cast.append(
            {
                "name": name,
                "role": role,
                "description": description,
                "slug": slug,
            }
        )
        if len(cast) >= 2:
            break

    if not cast:
        bible = doc_bible(doc)
        cast = [{
            "name": "Protagonist",
            "role": "protagonist",
            "description": _clip_text(bible, 400),
            "slug": "protagonist",
        }]

    if not any("protagonist" in member["role"].lower() for member in cast):
        cast[0]["role"] = "protagonist"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cast, ensure_ascii=False, indent=1), "utf-8")
    except OSError:
        pass
    return cast


def ensure_cast_portraits(doc: dict, chapter: int = 0) -> list[dict]:
    cast = doc_cast(doc, chapter)
    if not imaging.image_gen.backend("flux2-klein-4b").available():
        return []
    ready = []
    for member in cast:
        path = character_portrait_path(doc["id"], member["slug"])
        if not still_ready(path):
            prompt = (
                "Monochrome manga character reference, one character only: "
                f"{member['name']}, {member['description']}. Full-body front view, "
                "standing straight, neutral pose, arms at sides, fully clothed, "
                "plain pure white background, no environment, no ground, no shadows, "
                "no panels, no borders, no text, no labels, no other people. "
                "Black ink linework, screentone shading, consistent design."
            )
            try:
                imaging.image_gen.generate(
                    prompt, path, _seed(doc["id"], -2, len(ready)),
                    1024, 1280, model="flux2-klein-4b",
                )
                _finish_still(path)
            except Exception:
                path.unlink(missing_ok=True)
        if still_ready(path):
            ready.append({**member, "path": path})
    return ready


def ensure_cast_group(doc: dict, chapter: int = 0) -> Path | None:
    """One composite reference containing the recurring cast side by side."""
    path = cast_group_path(doc["id"])
    if still_ready(path):
        return path
    cast = ensure_cast_portraits(doc, chapter)
    if not cast:
        return ensure_character_sheet(doc)
    if len(cast) == 1:
        return cast[0]["path"]
    try:
        from PIL import Image

        images = [Image.open(member["path"]).convert("L") for member in cast[:2]]
        height = min(image.height for image in images)
        images = [
            image.resize((max(1, int(image.width * height / image.height)), height), Image.LANCZOS)
            for image in images
        ]
        gap = max(8, int(height * 0.04))
        canvas = Image.new("L", (sum(image.width for image in images) + gap, height), 255)
        left = 0
        for image in images:
            canvas.paste(image, (left, 0))
            left += image.width + gap
        canvas.save(path)
        return path if path.exists() else None
    except Exception:
        return cast[0]["path"]


def _reference_crop(member: dict, doc_id: str, close: bool) -> Path:
    """A 16:9 reference matching the frame, so FLUX does not tile two views."""
    path = (
        character_face_path(doc_id, member["slug"]) if close
        else character_body_path(doc_id, member["slug"])
    )
    if still_ready(path):
        return path
    try:
        from PIL import Image

        image = Image.open(member["path"]).convert("L")
        width, height = image.size
        if close:
            image = image.crop((0, 0, width, max(2, int(height * 0.45))))
        width, height = image.size
        ratio = config.FILM_WIDTH / config.FILM_HEIGHT
        if width / height > ratio:
            canvas = Image.new("L", (width, max(2, int(width / ratio))), 255)
            canvas.paste(image, (0, (canvas.height - height) // 2))
        else:
            canvas = Image.new("L", (max(2, int(height * ratio)), height), 255)
            canvas.paste(image, ((canvas.width - width) // 2, 0))
        canvas = canvas.resize((config.FILM_WIDTH, config.FILM_HEIGHT), Image.LANCZOS)
        canvas.save(path)
        return path
    except Exception:
        return member["path"]


def ensure_character_reference(doc: dict, scene: Scene | None = None,
                               chapter: int = 0, group: bool = False,
                               close: bool = False) -> Path | None:
    """Prefer a frame-shaped portrait of the character in this beat."""
    if group:
        return ensure_cast_group(doc, chapter)
    cast = ensure_cast_portraits(doc, chapter)
    if cast:
        text = scene.text.lower() if scene is not None else ""
        for member in cast:
            name = member["name"].lower()
            tokens = [
                token for token in re.split(r"[^a-z0-9]+", name)
                if len(token) > 3 and token not in {"deputy", "sheriff", "narrator", "protagonist"}
            ]
            if name in text or any(token in text for token in tokens):
                return _reference_crop(member, doc["id"], close)
        she = len(re.findall(r"\b(she|her|hers)\b", text))
        he = len(re.findall(r"\b(he|him|his)\b", text))
        hint = "woman" if she > he else "man" if he > she else ""
        if hint:
            for member in cast:
                description = member["description"].lower()
                if hint in description or (hint == "woman" and "female" in description) or (
                    hint == "man" and "male" in description
                ):
                    return _reference_crop(member, doc["id"], close)
        for member in cast:
            if "protagonist" in member["role"].lower():
                return _reference_crop(member, doc["id"], close)
        return _reference_crop(cast[0], doc["id"], close)
    return ensure_character_sheet(doc)


def ensure_character_sheet(doc: dict) -> Path | None:
    """One consistent manga character sheet per book, used as a FLUX reference."""
    path = character_sheet_path(doc["id"])
    if still_ready(path):
        return path
    if not imaging.image_gen.backend("flux2-klein-4b").available():
        return None
    bible = doc_bible(doc)
    title = doc.get("title") or "the book"
    prompt = (
        "Monochrome manga character turnaround model sheet, black ink linework and "
        f"screentone shading, the recurring main characters from {title}. "
        f"{_clip_text(bible, 450)} "
        "Draw only the people; ignore vehicles, locations and objects. "
        "One row of figures on a plain white background: front view, side view, "
        "three-quarter view, neutral standing poses, arms at sides, full body, "
        "same costume in every view, fully clothed, no action, no environment, "
        "no ground, no shadows, no panels, no borders, no numbers, no text, "
        "no labels, no other people."
    )
    try:
        imaging.image_gen.generate(
            prompt, path, _seed(doc["id"], -1, 0),
            config.FILM_WIDTH, config.FILM_HEIGHT,
            model="flux2-klein-4b",
        )
        if still_ready(path):
            _finish_still(path)
            return path
    except Exception:
        path.unlink(missing_ok=True)
    return None


def _finish_still(path: Path) -> None:
    """Force true monochrome and trim blank panel edges."""
    try:
        from PIL import Image, ImageOps

        image = ImageOps.grayscale(Image.open(path).convert("RGB"))
        width, height = image.size
        inset_x = max(1, int(width * 0.012))
        inset_y = max(1, int(height * 0.012))
        if width > inset_x * 4 and height > inset_y * 4:
            image = image.crop((inset_x, inset_y, width - inset_x, height - inset_y))
        data = np.asarray(image)
        height, width = data.shape
        limit_y = max(1, int(height * 0.06))
        limit_x = max(1, int(width * 0.06))

        def blank(line: np.ndarray) -> bool:
            return bool(line.std() < 2.0 and (line.mean() > 244 or line.mean() < 12))

        top = 0
        while top < limit_y and blank(data[top]):
            top += 1
        bottom = height
        while height - bottom < limit_y and blank(data[bottom - 1]):
            bottom -= 1
        left = 0
        while left < limit_x and blank(data[:, left]):
            left += 1
        right = width
        while width - right < limit_x and blank(data[:, right - 1]):
            right -= 1

        if (left, top, right, bottom) != (0, 0, width, height):
            image = image.crop((left, top, right, bottom))
        image.save(path)
    except Exception:
        pass


def _has_lettering(path: Path) -> bool:
    try:
        return ocr.lettering(path.read_bytes())
    except Exception:
        return False


def _has_bubble(path: Path, max_width: int = 480) -> bool:
    """Detect a large enclosed white speech bubble, even when OCR fails."""
    try:
        from PIL import Image

        image = Image.open(path).convert("L")
        width, height = image.size
        scale = min(1.0, max_width / width)
        image = image.resize(
            (max(16, int(width * scale)), max(9, int(height * scale))),
            Image.BILINEAR,
        )
        data = np.asarray(image)
        height, width = data.shape
        white = data > 235
        seen = np.zeros_like(white, dtype=bool)

        for y in range(height):
            for x in range(width):
                if not white[y, x] or seen[y, x]:
                    continue
                todo = deque([(y, x)])
                seen[y, x] = True
                area = 0
                x0 = x1 = x
                y0 = y1 = y
                while todo:
                    cy, cx = todo.popleft()
                    area += 1
                    x0, x1 = min(x0, cx), max(x1, cx)
                    y0, y1 = min(y0, cy), max(y1, cy)
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < height and 0 <= nx < width and white[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            todo.append((ny, nx))
                if area < 0.004 * height * width:
                    continue
                if x0 < 1 or y0 < 1 or x1 > width - 2 or y1 > height - 2:
                    continue
                box_width, box_height = x1 - x0 + 1, y1 - y0 + 1
                if box_width < 0.06 * width or box_height < 0.04 * height:
                    continue
                if box_width > 0.9 * width or box_height > 0.8 * height:
                    continue
                if area / (box_width * box_height) < 0.35:
                    continue
                patch = data[y0:y1 + 1, x0:x1 + 1]
                if (patch < 80).mean() < 0.02:
                    continue
                ring = data[max(0, y0 - 2):min(height, y1 + 3),
                            max(0, x0 - 2):min(width, x1 + 3)]
                border = np.concatenate([ring[0], ring[-1], ring[:, 0], ring[:, -1]])
                if (border < 100).mean() >= 0.15:
                    return True
    except Exception:
        pass
    return False


def _clean_lettering(path: Path, scene: Scene, model: str,
                     width: int, height: int) -> None:
    if not model.startswith("flux"):
        return
    prompt = (
        "Edit this monochrome manga panel to remove every speech bubble, caption, "
        "lettering, sign, logo and written mark. Rebuild the artwork underneath with "
        "clean black ink linework and screentone. Keep the exact same characters, "
        "faces, poses, costumes, framing, lighting and composition. One continuous "
        "full-bleed illustration, no panels, no gutters, no split screen. No text, "
        "no bubbles, no borders."
    )
    temp = path.with_name(f"{path.stem}_clean.png")
    try:
        imaging.image_gen.generate(
            prompt, temp, scene.seed + 1009, width, height,
            reference_image=path, model=model,
        )
        if still_ready(temp):
            temp.replace(path)
    except Exception:
        pass
    finally:
        temp.unlink(missing_ok=True)


def _find_gutter(data: np.ndarray) -> tuple[str, int] | None:
    """Locate a narrow full-height/width band that separates comic panels."""
    height, width = data.shape

    def runs(flags: list[bool]):
        start = None
        for index, flag in enumerate(flags):
            if flag and start is None:
                start = index
            elif not flag and start is not None:
                yield start, index
                start = None
        if start is not None:
            yield start, len(flags)

    def candidate(axis: str, flags: list[bool], offset: int, span: int) -> tuple[str, int] | None:
        best: tuple[int, int] | None = None
        for start, end in runs(flags):
            length = end - start
            if length < 2 or length > max(12, int(span * 0.06)):
                continue
            center = offset + (start + end) // 2
            if axis == "v":
                before = data[:, max(0, offset + start - 10):offset + start]
                after = data[:, offset + end:min(width, offset + end + 10)]
                middle = width / 2
            else:
                before = data[max(0, offset + start - 10):offset + start, :]
                after = data[offset + end:min(height, offset + end + 10), :]
                middle = height / 2
            if before.size == 0 or after.size == 0:
                continue
            if before.std() < 8 or after.std() < 8:
                continue
            distance = abs(center - middle)
            if best is None or distance < best[0]:
                best = (distance, center)
        return (axis, best[1]) if best else None

    def central(axis: str) -> tuple[str, int] | None:
        span = height if axis == "h" else width
        offset = int(span * 0.25)
        flags: list[bool] = []
        for position in range(offset, int(span * 0.75)):
            line = data[position, :] if axis == "h" else data[:, position]
            flags.append(bool((line > 232).mean() >= 0.5))
        for start, end in runs(flags):
            length = end - start
            if length < 2 or length > 40:
                continue
            before = data[offset + start - 2:offset + start, :] if axis == "h" else data[:, offset + start - 2:offset + start]
            after = data[offset + end:offset + end + 2, :] if axis == "h" else data[:, offset + end:offset + end + 2]
            if before.size == 0 or after.size == 0:
                continue
            if before.mean() >= 180 or after.mean() >= 180:
                continue
            return (axis, offset + (start + end) // 2)
        return None

    found = central("h")
    if found:
        return found

    vertical: list[bool] = []
    for x in range(int(width * 0.2), int(width * 0.8)):
        column = data[:, x]
        vertical.append(
            bool((column < 18).mean() > 0.985 or (column > 245).mean() > 0.985)
        )
    found = candidate("v", vertical, int(width * 0.2), width)
    if found:
        return found

    horizontal: list[bool] = []
    for y in range(int(height * 0.2), int(height * 0.8)):
        row = data[y, :]
        horizontal.append(
            bool((row < 18).mean() > 0.985 or (row > 245).mean() > 0.985)
        )
    return candidate("h", horizontal, int(height * 0.2), height)


def _crop_gutter(path: Path) -> None:
    """Keep the stronger half when the model still draws a split spread."""
    try:
        from PIL import Image

        image = Image.open(path).convert("L")
        found = _find_gutter(np.asarray(image))
        if not found:
            return
        axis, position = found
        width, height = image.size
        if axis == "v":
            left = image.crop((0, 0, position, height))
            right = image.crop((position, 0, width, height))
            if left.width < 80 or right.width < 80:
                return
            if abs(left.width - right.width) < width * 0.05:
                panel = left if np.asarray(left).std() >= np.asarray(right).std() else right
            else:
                panel = left if left.width > right.width else right
        else:
            top = image.crop((0, 0, width, position))
            bottom = image.crop((0, position, width, height))
            if top.height < 80 or bottom.height < 80:
                return
            if abs(top.height - bottom.height) < height * 0.05:
                panel = top if np.asarray(top).std() >= np.asarray(bottom).std() else bottom
            else:
                panel = top if top.height > bottom.height else bottom

        ratio = config.FILM_WIDTH / config.FILM_HEIGHT
        panel_width, panel_height = panel.size
        if panel_width / panel_height > ratio:
            new_width = max(2, int(round(panel_height * ratio)))
            left = (panel_width - new_width) // 2
            panel = panel.crop((left, 0, left + new_width, panel_height))
        else:
            new_height = max(2, int(round(panel_width / ratio)))
            top = (panel_height - new_height) // 2
            panel = panel.crop((0, top, panel_width, top + new_height))
        panel = panel.resize((config.FILM_WIDTH, config.FILM_HEIGHT), Image.LANCZOS)
        panel.save(path)
        _finish_still(path)
    except Exception:
        pass


def _has_gutter(path: Path) -> bool:
    try:
        from PIL import Image

        return _find_gutter(np.asarray(Image.open(path).convert("L"))) is not None
    except Exception:
        return False


def ensure_scene_image(doc: dict, chapter: int, scene: Scene,
                       before_path: Path | None = None,
                       prev_text: str = "", next_text: str = "",
                       anchor: bool = False,
                       prompt_override: str | None = None,
                       seed: int | None = None) -> Path:
    model, width, height = film_model()
    path = scene_image_path(doc["id"], chapter, scene.index, model)
    try:
        if still_ready(path):
            if _has_gutter(path):
                if model.startswith("flux"):
                    _clean_lettering(path, scene, model, width, height)
                    _finish_still(path)
                if _has_gutter(path):
                    _crop_gutter(path)
            if model.startswith("flux") and _has_lettering(path):
                _clean_lettering(path, scene, model, width, height)
                _finish_still(path)
            return path
    except OSError:
        pass
    if prompt_override:
        cast = doc_cast(doc, chapter)
        cast_text = "; ".join(
            f"{member['name']}: {member['description']}" for member in cast[:2]
        )
        prompt = (
            f"{prompt_override.strip(' .')}. {config.FILM_STYLE}, monochrome manga "
            "panel, continuous full-bleed illustration, no text, no speech bubbles, "
            "no panel borders, no gutters"
            + (f", exact character designs: {cast_text}" if cast_text else "")
            + "."
        )
    else:
        prompt = compose_prompt(doc, chapter, scene)
    use_seed = scene.seed if seed is None else int(seed)
    reference = before_path if before_path and Path(before_path).exists() else None
    sheet = None
    strength = 0.55
    if model.startswith("flux"):
        shot = shot_for_scene(scene)
        # Each key image is a deliberate illustration. Re-using the previous
        # frame as img2img made consecutive shots nearly identical, so anchor
        # every character shot to a clean portrait and let scenery stand alone.
        reference = None
        if shot in {"establishing wide shot", "detail insert"}:
            sheet = None
        else:
            sheet = ensure_character_reference(
                doc, scene, chapter, close=shot == "close-up",
            )

    def generate(text: str, use: int) -> None:
        imaging.image_gen.generate(
            text, path, use, width, height,
            init_image=reference if not sheet else None, strength=strength, model=model,
            reference_image=sheet,
        )
        _finish_still(path)

    generate(prompt, use_seed)
    if _has_gutter(path):
        generate(
            "ONE single continuous full-bleed manga illustration, one moment, one "
            "camera angle, no split panels, no comic page. " + prompt,
            use_seed + 501,
        )
    if _has_gutter(path):
        if model.startswith("flux"):
            _clean_lettering(path, scene, model, width, height)
            _finish_still(path)
        if _has_gutter(path):
            _crop_gutter(path)
    if model.startswith("flux") and _has_lettering(path):
        _clean_lettering(path, scene, model, width, height)
        _finish_still(path)
        if _has_gutter(path):
            _crop_gutter(path)
    if still_ready(path):
        try:
            polish.apply(path, prompt)
        except Exception as exc:  # a polish mistake must never lose the still
            polish.note_failure(exc)
    return path


def _regenerate_region(doc: dict, chapter: int, scene: Scene, path: Path,
                       rect: list[float], prompt: str, seed: int | None,
                       model: str) -> Path:
    from PIL import Image, ImageDraw, ImageFilter

    image = Image.open(path).convert("L")
    width, height = image.size
    x, y, box_width, box_height = (float(value) for value in rect[:4])
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    box_width = max(0.02, min(1.0 - x, box_width))
    box_height = max(0.02, min(1.0 - y, box_height))
    box = (
        int(round(x * width)),
        int(round(y * height)),
        int(round((x + box_width) * width)),
        int(round((y + box_height) * height)),
    )
    if box[2] - box[0] < 32 or box[3] - box[1] < 32:
        raise ValueError("Select a larger area")
    crop = image.crop(box)
    crop_width, crop_height = crop.size
    source = path.with_name(f"{path.stem}_region_src.png")
    result = path.with_name(f"{path.stem}_region_out.png")
    crop.save(source)
    text = (
        "Regenerate only this selected detail from a monochrome manga panel. "
        f"Scene context: {scene.text[:220]}. "
        f"{prompt.strip() or 'Clean up and redraw this detail clearly.'} "
        "Keep the exact surrounding lighting, black ink density, screentone and "
        "background; do not add a white background, text, borders or panels."
    )
    try:
        imaging.image_gen.generate(
            text, result, (scene.seed + 2003) if seed is None else int(seed),
            crop_width, crop_height, reference_image=source, model=model,
        )
        if not result.exists():
            raise RuntimeError("Region generation failed")
        replacement = Image.open(result).convert("L").resize(
            (crop_width, crop_height), Image.LANCZOS,
        )
        # Match the replacement's tones to the area it replaces so a light
        # generated background does not become a white rectangle in the panel.
        import numpy as np

        original = np.asarray(crop, dtype=np.float32)
        generated = np.asarray(replacement, dtype=np.float32)
        source_mean = float(original.mean())
        source_std = max(float(original.std()), 1.0)
        generated_mean = float(generated.mean())
        generated_std = max(float(generated.std()), 1.0)
        generated = (
            (generated - generated_mean)
            * (source_std / generated_std)
            * 0.75
            + source_mean
        )
        replacement = Image.fromarray(
            np.clip(generated, 0, 255).astype(np.uint8), "L",
        )
        feather = max(4, min(crop_width, crop_height) // 20)
        mask = Image.new("L", (crop_width, crop_height), 0)
        ImageDraw.Draw(mask).rectangle(
            [feather, feather, crop_width - feather, crop_height - feather],
            fill=255,
        )
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
        image.paste(replacement, (box[0], box[1]), mask)
        image.save(path)
    finally:
        source.unlink(missing_ok=True)
        result.unlink(missing_ok=True)
    return path


def regenerate_scene(doc: dict, chapter: int, index: int, mode: str = "whole",
                     rect: list[float] | None = None, prompt: str = "",
                     seed: int | None = None) -> Path:
    scenes = plan_chapter(doc, chapter)
    if index < 0 or index >= len(scenes):
        raise ValueError("Scene out of range")
    scene = scenes[index]
    model, _, _ = film_model()
    path = scene_image_path(doc["id"], chapter, scene.index, model)
    if mode == "region" and rect and path.exists():
        return _regenerate_region(doc, chapter, scene, path, rect, prompt, seed, model)
    path.unlink(missing_ok=True)
    use_seed = scene.seed + 100003 if seed is None else int(seed)
    ensure_scene_image(
        doc, chapter, scene, anchor=True,
        prompt_override=(prompt.strip() or None), seed=use_seed,
    )
    return path


class ScenePainter:
    """Renders scene stills ahead of playback, one at a time, into the cache."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[tuple[str, int, int]] = set()
        self._active: set[tuple[str, int, int]] = set()
        self._failed: set[tuple[str, int, int]] = set()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run, name="scene-painter", daemon=True
                )
                self._worker.start()

    def enqueue(self, doc: dict, chapter: int, scenes: list[Scene],
                start: int = 0, count: int | None = None) -> int:
        """Queue from `start` forward, then wrap, so the current beat paints first."""
        self._ensure_worker()
        start = max(0, min(int(start), max(0, len(scenes) - 1)))
        ordered = scenes[start:] + scenes[:start]
        if count:
            ordered = ordered[: max(1, int(count))]
        model = film_model()[0]
        anchor_every = max(2, round(len(scenes) / 3))
        added = 0
        with self._lock:
            for offset, scene in enumerate(ordered):
                key = (doc["id"], chapter, scene.index)
                if key in self._pending:
                    continue
                if still_ready(scene_image_path(doc["id"], chapter, scene.index, model)):
                    continue
                source_index = (start + offset) % max(1, len(scenes))
                previous = scenes[source_index - 1] if source_index > 0 else None
                following = scenes[source_index + 1] if source_index + 1 < len(scenes) else None
                anchor = source_index == 0 or source_index % anchor_every == 0
                before = (
                    scene_image_path(doc["id"], chapter, previous.index, model)
                    if previous and not anchor else None
                )
                self._pending.add(key)
                self._failed.discard(key)
                self._queue.put((
                    doc, chapter, scene, before,
                    previous.text if previous else "",
                    following.text if following else "",
                    anchor,
                ))
                added += 1
        return added

    def retarget(self, doc: dict, chapter: int, scenes: list[Scene],
                 start: int = 0, count: int = 10) -> int:
        """Drop this document's queued work and focus on a window from `start`."""
        self._ensure_worker()
        doc_id = doc["id"]
        with self._lock:
            self._pending = {
                key for key in self._pending
                if key[0] != doc_id or key in self._active
            }
            # Drain in place: the worker may be blocked in get() on this object,
            # so replacing the queue would strand it forever.
            kept = []
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item[0]["id"] != doc_id:
                    kept.append(item)
                self._queue.task_done()
            for item in kept:
                self._queue.put(item)
        return self.enqueue(doc, chapter, scenes, start, count)

    def _run(self) -> None:
        while True:
            doc, chapter, scene, before, prev_text, next_text, anchor = self._queue.get()
            key = (doc["id"], chapter, scene.index)
            with self._lock:
                self._active.add(key)
            ok = False
            try:
                ensure_scene_image(doc, chapter, scene, before, prev_text, next_text, anchor=anchor)
                ok = True
            except Exception:
                pass
            finally:
                with self._lock:
                    self._pending.discard(key)
                    self._active.discard(key)
                    if ok:
                        self._failed.discard(key)
                    else:
                        self._failed.add(key)
                self._queue.task_done()

    def status(self, doc: dict, chapter: int, scenes: list[Scene]) -> dict:
        model = film_model()[0]
        done = sum(
            1 for scene in scenes
            if still_ready(scene_image_path(doc["id"], chapter, scene.index, model))
        )
        with self._lock:
            pending = sum(
                1 for scene in scenes if (doc["id"], chapter, scene.index) in self._pending
            )
            failed = sum(
                1 for scene in scenes if (doc["id"], chapter, scene.index) in self._failed
            )
        return {"done": done, "total": len(scenes), "pending": pending, "failed": failed}

    def clear(self, doc_id: str) -> None:
        with self._lock:
            self._pending = {
                key for key in self._pending
                if key[0] != doc_id or key in self._active
            }
            self._failed = {key for key in self._failed if key[0] != doc_id}


painter = ScenePainter()


# ── subtitles ───────────────────────────────────────────────────────────────

def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        secs += 1
        centis -= 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(path: Path, timeline: list[dict]) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Helvetica Neue,51,&H00FFFFFF,&H00FF7B8B,&H00140A05,&H96000000,0,0,0,0,100,100,0,0,1,3,1,2,90,90,69,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for entry in timeline:
        words = entry.get("words") or []
        spoken = entry.get("spoken") or ""
        karaoke = []
        if words:
            for i, word in enumerate(words):
                word_end = words[i + 1]["start"] if i + 1 < len(words) else entry["duration"]
                centis = max(1, int(round((word_end - word["start"]) * 100)))
                token = _ass_escape(word["text"].strip())
                if not token:
                    continue
                karaoke.append(f"{{\\k{centis}}}{token} ")
            used = sum(int(round((words[i + 1]["start"] if i + 1 < len(words) else entry["duration"]) - w["start"]))
                       for i, w in enumerate(words))
            remainder = int(round(entry["duration"] * 100)) - used
            if remainder > 0:
                karaoke.append(f"{{\\k{remainder}}}")
        else:
            karaoke = [_ass_escape(spoken)]

        start = entry["start"]
        end = entry["start"] + entry["duration"]
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{''.join(karaoke).strip()}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(path: Path, timeline: list[dict]) -> None:
    lines = []
    for index, entry in enumerate(timeline, 1):
        start = entry["start"]
        end = start + entry["duration"]
        text = _ass_escape(entry.get("spoken") or "")
        lines.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


# ── narration ───────────────────────────────────────────────────────────────

def _narrate(doc: dict, chapter: int, voice: str, job: dict | None = None) -> tuple[list[dict], Path, float]:
    import soundfile as sf

    settings = store.get_settings()
    translate_on = bool(settings.get("translateToVoice", True))
    indices = chapter_unit_indices(doc, chapter)
    pieces: list[np.ndarray] = []
    timeline: list[dict] = []
    sample_rate = config.SAMPLE_RATE
    clock = 0.0

    for step, index in enumerate(indices):
        prepared = speak.prepare(doc["units"][index], voice, translate_on)
        meta = engine.engine.synthesize(prepared["text"], voice)
        data, sample_rate = sf.read(meta["path"], dtype="float32")
        data = np.asarray(data, dtype=np.float32)
        duration = float(len(data)) / float(sample_rate)
        timeline.append(
            {
                "unit": index,
                "start": clock,
                "duration": duration,
                "words": meta["words"],
                "spoken": prepared["text"],
            }
        )
        pieces.append(data)
        gap = np.zeros(int(sample_rate * SCENE_GAP), dtype=np.float32)
        pieces.append(gap)
        clock += duration + SCENE_GAP

        if job is not None:
            job["phase"] = "narration"
            job["done"] = step + 1
            job["total"] = len(indices)

    narration = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    path = config.FILM_DIR / doc["id"] / f"chapter_{chapter}_narration.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, narration, sample_rate, subtype="PCM_16")
    return timeline, path, clock


# ── video ───────────────────────────────────────────────────────────────────

def _run(command: list[str]) -> None:
    result = proc.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg failed")[-1500:])


_ASS_SUPPORT: bool | None = None


def _ffmpeg() -> str:
    path = config.find_tool("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg is required for film rendering (brew install ffmpeg)")
    return path


_H264_ENCODER: str | None = None


def _h264_encoder(ffmpeg: str) -> str:
    """Platform hardware H.264 where available, portable libx264 elsewhere.

    VideoToolbox is macOS/Windows-Build-specific; the ffmpeg that ships with
    most Linux/Windows installs lacks it, so probe once and fall back."""
    global _H264_ENCODER
    if _H264_ENCODER is None:
        try:
            result = proc.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=15,
            )
            _H264_ENCODER = ("h264_videotoolbox" if "h264_videotoolbox" in result.stdout
                             else "libx264")
        except (OSError, subprocess.SubprocessError):
            _H264_ENCODER = "libx264"
    return _H264_ENCODER


def _has_libass(ffmpeg: str) -> bool:
    global _ASS_SUPPORT
    if _ASS_SUPPORT is None:
        try:
            result = proc.run(
                [ffmpeg, "-hide_banner", "-filters"],
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout
            _ASS_SUPPORT = (" subtitles " in output) or (" ass " in output)
        except (OSError, subprocess.SubprocessError):
            _ASS_SUPPORT = False
    return _ASS_SUPPORT


def _scene_clip(image: Path, duration: float, out: Path, index: int) -> None:
    ffmpeg = _ffmpeg()
    total = duration + XFADE
    frames = max(2, int(round(total * FPS)))
    vf = (
        f"scale={CLIP_W}:{CLIP_H}:force_original_aspect_ratio=increase,"
        f"crop={CLIP_W}:{CLIP_H},"
        f"zoompan=z='min(1.0+0.04*on/{frames},1.04)':"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={CLIP_W}x{CLIP_H}:fps={FPS},"
        f"scale={OUT_W}:{OUT_H}:flags=lanczos,format=yuv420p"
    )
    _run([
        ffmpeg, "-y", "-loop", "1", "-i", str(image), "-t", f"{total:.3f}",
        "-vf", vf, "-r", str(FPS),
        "-c:v", _h264_encoder(ffmpeg), "-b:v", "10M",
        str(out),
    ])


def _compose(doc_id: str, chapter: int, scenes: list[Scene], timeline: list[dict],
             narration: Path, total_audio: float, job: dict) -> Path:
    ffmpeg = _ffmpeg()

    work = config.FILM_DIR / doc_id / f"chapter_{chapter}"
    work.mkdir(parents=True, exist_ok=True)

    # Map unit -> timeline entry, then give every scene start/duration.
    by_unit = {entry["unit"]: entry for entry in timeline}
    for scene in scenes:
        entries = [by_unit[u] for u in scene.units if u in by_unit]
        if not entries:
            scene.start, scene.duration = 0.0, 0.0
            continue
        scene.start = entries[0]["start"]
        last = entries[-1]
        scene.duration = last["start"] + last["duration"] - scene.start + SCENE_GAP

    clips: list[Path] = []
    clip_scenes: list[Scene] = []
    for scene in scenes:
        if job.get("cancelled"):
            raise RuntimeError("cancelled")
        if scene.duration <= 0.1:
            continue
        image = scene_image_path(doc_id, chapter, scene.index, film_model()[0])
        if not image.exists():
            document = store.get_doc(doc_id)
            ensure_scene_image(document, chapter, scene)
        clip = work / f"clip_{scene.index:05d}.mp4"
        _scene_clip(image, scene.duration, clip, scene.index)
        clips.append(clip)
        clip_scenes.append(scene)
        job["phase"] = "video"
        job["done"] = len(clips)
        job["total"] = len(scenes)

    if not clips:
        raise RuntimeError("No scenes to render")

    # Crossfade chain.
    filters = []
    label = "[0:v]"
    offset = clip_scenes[0].duration if clip_scenes else 0.0
    for i in range(1, len(clips)):
        out_label = f"[v{i}]"
        filters.append(
            f"{label}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={max(0.0, offset - XFADE):.3f}{out_label}"
        )
        label = out_label
        offset += clip_scenes[i].duration

    burn_subs = _has_libass(ffmpeg)
    srt_path: Path | None = None
    if burn_subs:
        ass_path = work / "subtitles.ass"
        write_ass(ass_path, timeline)
        escaped = str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        filters.append(f"{label}subtitles=filename='{escaped}'[vout]")
    else:
        srt_path = work / "subtitles.srt"
        write_srt(srt_path, timeline)
        filters.append(f"{label}null[vout]")

    if job.get("cancelled"):
        raise RuntimeError("cancelled")

    video_duration = max(0.1, total_audio - XFADE * max(0, len(clips) - 1))
    output = config.FILM_DIR / doc_id / f"chapter_{chapter}.mp4"

    command = [ffmpeg, "-y"]
    for clip in clips:
        command += ["-i", str(clip)]
    command += ["-i", str(narration)]
    if srt_path is not None:
        command += ["-i", str(srt_path)]
    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", f"{len(clips)}:a",
    ]
    if srt_path is not None:
        command += [
            "-map", f"{len(clips) + 1}:s",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=eng",
        ]
    command += [
        "-t", f"{video_duration:.3f}",
        "-c:v", _h264_encoder(ffmpeg), "-b:v", "12M",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ]
    _run(command)

    for clip in clips:
        clip.unlink(missing_ok=True)
    if burn_subs:
        (work / "subtitles.ass").unlink(missing_ok=True)
    if srt_path is not None:
        srt_path.unlink(missing_ok=True)
    return output


def _manifest_path(doc_id: str, chapter: int) -> Path:
    return config.FILM_DIR / doc_id / f"chapter_{chapter}.json"


def _read_manifest(path: Path) -> dict | None:
    try:
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _write_manifest(path: Path, key: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(key, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _render_key(doc: dict, chapter: int, scenes: list[Scene], voice: str,
                burn_subs: bool) -> dict:
    """Everything that would change the produced film."""
    model, width, height = film_model()
    settings = store.get_settings()
    images: list[list[int]] = []
    for scene in scenes:
        path = scene_image_path(doc["id"], chapter, scene.index, model)
        try:
            stat = path.stat()
            images.append([scene.index, stat.st_mtime_ns, stat.st_size])
        except OSError:
            images.append([scene.index, 0, 0])
    return {
        "render": RENDER_VERSION,
        "scene": SCENE_VERSION,
        "doc": doc["id"],
        "chapter": chapter,
        "voice": voice,
        "translate": bool(settings.get("translateToVoice", True)),
        "model": model,
        "size": [width, height],
        "out": [OUT_W, OUT_H, FPS],
        "xfade": XFADE,
        "gap": SCENE_GAP,
        "sampleRate": config.SAMPLE_RATE,
        "burnSubs": bool(burn_subs),
        "scenes": [[s.index, s.units, s.text, s.seed] for s in scenes],
        "images": images,
    }


def render_chapter(doc: dict, chapter: int, voice: str, job: dict) -> None:
    if job.get("cancelled"):
        job["state"] = "cancelled"
        return
    job["state"] = "running"
    job["phase"] = "images"
    try:
        scenes = plan_chapter(doc, chapter)
        output = config.FILM_DIR / doc["id"] / f"chapter_{chapter}.mp4"
        manifest = _manifest_path(doc["id"], chapter)
        burn_subs = _has_libass(_ffmpeg())

        # Nothing changed since the last successful render: serve it as-is.
        key = _render_key(doc, chapter, scenes, voice, burn_subs)
        if output.exists() and _read_manifest(manifest) == key:
            job["state"] = "done"
            job["phase"] = "done"
            job["done"] = len(scenes)
            job["total"] = len(scenes)
            job["file"] = str(output)
            job["title"] = f"{doc.get('title', 'Murmur')} — chapter {chapter + 1}"
            job["reused"] = True
            return

        job["done"] = 0
        job["total"] = len(scenes)
        anchor_every = max(2, round(len(scenes) / 3))
        for step, scene in enumerate(scenes):
            previous = scenes[step - 1] if step > 0 else None
            following = scenes[step + 1] if step + 1 < len(scenes) else None
            anchor = step == 0 or step % anchor_every == 0
            before = (
                scene_image_path(doc["id"], chapter, previous.index, film_model()[0])
                if previous and not anchor else None
            )
            ensure_scene_image(
                doc, chapter, scene, before,
                previous.text if previous else "",
                following.text if following else "",
                anchor=anchor,
            )
            job["done"] = step + 1
            if job.get("cancelled"):
                job["state"] = "cancelled"
                return

        key = _render_key(doc, chapter, scenes, voice, burn_subs)
        timeline, narration, total_audio = _narrate(doc, chapter, voice, job)
        if job.get("cancelled"):
            narration.unlink(missing_ok=True)
            job["state"] = "cancelled"
            return
        output = _compose(doc["id"], chapter, scenes, timeline, narration, total_audio, job)
        narration.unlink(missing_ok=True)
        _write_manifest(manifest, key)
        job["state"] = "done"
        job["phase"] = "done"
        job["file"] = str(output)
        job["title"] = f"{doc.get('title', 'Murmur')} — chapter {chapter + 1}"
    except Exception as exc:  # pragma: no cover
        if job.get("cancelled"):
            job["state"] = "cancelled"
            job["phase"] = "cancelled"
            return
        job["state"] = "error"
        job["error"] = str(exc)


def _ensure_film_worker() -> None:
    global _FILM_WORKER
    with _FILM_WORKER_LOCK:
        if _FILM_WORKER is None or not _FILM_WORKER.is_alive():
            _FILM_WORKER = threading.Thread(
                target=_film_worker, name="film-worker", daemon=True,
            )
            _FILM_WORKER.start()


def _film_worker() -> None:
    while True:
        doc, chapter, voice, job_id = _FILM_QUEUE.get()
        try:
            job = JOBS.get(job_id)
            if not job or job.get("cancelled"):
                if job is not None:
                    job["state"] = "cancelled"
                continue
            # A chapter holds the GPU for half an hour; let Windows sleep during
            # one of these and the power transition can take the app with it.
            with power.keep_awake():
                render_chapter(doc, chapter, voice, job)
        finally:
            _FILM_QUEUE.task_done()


def start_job(doc: dict, chapter: int, voice: str) -> dict:
    chapter = max(0, min(int(chapter), chapter_count(doc) - 1))
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "state": "queued",
        "phase": "queued",
        "done": 0,
        "total": len(plan_chapter(doc, chapter)),
        "docId": doc["id"],
        "chapter": chapter,
        "title": doc.get("title", "Murmur"),
        "created": time.time(),
    }
    JOBS[job_id] = job
    _ensure_film_worker()
    _FILM_QUEUE.put((doc, chapter, voice, job_id))
    return job


def clear_plan(doc_id: str) -> None:
    with _PLAN_LOCK:
        for key in [k for k in _PLANS if k[0] == doc_id]:
            _PLANS.pop(key, None)
