"""Paths and runtime configuration for Murmur."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))

# In a packaged .app, data files live next to the executable (_MEIPASS).
ASSET_ROOT = Path(getattr(sys, "_MEIPASS", APP_ROOT)) if FROZEN else APP_ROOT
WEB_DIR = ASSET_ROOT / "web"
BUNDLED_MODELS = ASSET_ROOT / "models"

def _default_home() -> Path:
    """Per-platform data directory.

    macOS keeps the historical ``~/.murmur`` path so existing libraries,
    weights and the image runtime stay where installs expect them. Windows and
    Linux use their platform conventions; ``MURMUR_HOME`` overrides everything.
    """
    if sys.platform == "darwin":
        return Path.home() / ".murmur"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home()
        return root / "Rhapsode"
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "rhapsode"


HOME = Path(os.environ.get("MURMUR_HOME", _default_home())).expanduser().resolve()
MODELS_DIR = HOME / "models"
CACHE_DIR = HOME / "cache"
PAGES_DIR = CACHE_DIR / "pages"
FILES_DIR = HOME / "files"
EXPORT_DIR = HOME / "exports"
FILM_DIR = HOME / "film"
DB_PATH = HOME / "murmur.db"

# The engine profile (data dir/engine.json) decides precision, where the text
# encoder runs and how much VRAM the painter must leave. It is read here so the
# child interpreter inherits the decision through the environment.
from . import profile as _profile  # noqa: E402  (after HOME exists)

PROFILE = _profile.load(HOME)
_profile.apply_env(PROFILE)

# Keep downloaded image models with the rest of Murmur, not in ~/.cache.
os.environ.setdefault("HF_HOME", str(HOME / "models" / "hf"))

FILM_STYLE = os.environ.get(
    "MURMUR_FILM_STYLE",
    "monochrome manga illustration, black ink linework, screentone shading, "
    "dramatic composition, high contrast, no text, no speech bubbles, no captions",
)
FILM_WIDTH = 1920
FILM_HEIGHT = 1080

# High-quality image backend: FLUX.2 [klein] 4B.
# macOS runs the MLX build through mflux; everything else runs the same weights
# through diffusers inside image-venv (see scripts/setup_image.ps1 / setup_flux.sh).
FLUX_MODEL_ID = "mflux-community/flux2-klein-4b-mflux-q4"
FLUX_BASE_MODEL = "flux2-klein-4b"
FLUX_WEIGHTS = os.environ.get("MURMUR_FLUX_MODEL", "black-forest-labs/FLUX.2-klein-4B")
FLUX_VENV = HOME / "image-venv"
FLUX_GENERATE = FLUX_VENV / "bin" / "mflux-generate-flux2"
FLUX_EDIT = FLUX_VENV / "bin" / "mflux-generate-flux2-edit"
IMAGE_PYTHON = Path(
    os.environ.get("MURMUR_IMAGE_PYTHON")
    or FLUX_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
)

if FROZEN:
    TRANSLATE_BIN = ASSET_ROOT / "bin" / "murmur-translate"
    PROMPTS_BIN = ASSET_ROOT / "bin" / "murmur-prompts"
else:
    TRANSLATE_BIN = APP_ROOT / "bin" / "murmur-translate"
    PROMPTS_BIN = APP_ROOT / "bin" / "murmur-prompts"

# Prefer weights shipped inside the app, then the user's ~/.murmur/models.
if (BUNDLED_MODELS / "kokoro-v1.0.onnx").exists():
    MODEL_PATH = BUNDLED_MODELS / "kokoro-v1.0.onnx"
    VOICES_PATH = BUNDLED_MODELS / "voices-v1.0.bin"
else:
    MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
    VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"

HOST = os.environ.get("MURMUR_HOST", "127.0.0.1")
PORT = int(os.environ.get("MURMUR_PORT", "8765"))

# Translation off macOS: an OpenAI-compatible endpoint on this machine.
TRANSLATE_URL = os.environ.get("MURMUR_TRANSLATE_URL", "").strip()
TRANSLATE_MODEL = os.environ.get("MURMUR_TRANSLATE_MODEL", "local-model").strip() or "local-model"

# Set by the launcher once the real bind address is known. None means "derive
# from HOST" (which keeps `uvicorn murmur.server:app` working).
PUBLIC: bool | None = None

LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# Audio cache budget (bytes). Roughly 3 MB per minute of speech.
CACHE_LIMIT = int(os.environ.get("MURMUR_CACHE_MB", "2048")) * 1024 * 1024

SAMPLE_RATE = 24_000

# OCR
OCR_DPI = int(os.environ.get("MURMUR_OCR_DPI", "200"))
OCR_MAX_PAGES = int(os.environ.get("MURMUR_OCR_MAX_PAGES", "200"))
OCR_MIN_CHARS = int(os.environ.get("MURMUR_OCR_MIN_CHARS", "40"))
OCR_LANGS = os.environ.get("MURMUR_OCR_LANGS", "")

# A file smaller than this cannot be a working weight file: the pair is about
# 340 MB, and a truncated or placeholder file of a few bytes used to pass every
# "does the model exist" check and then throw inside ONNX Runtime.
MODEL_MIN_BYTES = 100_000_000
VOICES_MIN_BYTES = 1_000_000


def weight_problem(path, minimum):
    """Why `path` cannot serve as a weight file, or None when it can."""
    if not path.exists():
        return f"{path.name} is missing"
    size = path.stat().st_size
    if size < minimum:
        return f"{path.name} is {size} bytes, not the {minimum // 1_000_000} MB it should be"
    return None


def voice_model_problem():
    """One sentence naming what is wrong with the voice weights, or None."""
    for path, minimum in ((MODEL_PATH, MODEL_MIN_BYTES), (VOICES_PATH, VOICES_MIN_BYTES)):
        problem = weight_problem(path, minimum)
        if problem:
            return problem
    return None


def voice_model_ready():
    return voice_model_problem() is None


MODEL_URLS = {
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
}


def ensure_dirs() -> None:
    for p in (HOME, MODELS_DIR, CACHE_DIR, PAGES_DIR, FILES_DIR, EXPORT_DIR,
              FILM_DIR):
        p.mkdir(parents=True, exist_ok=True)


def find_tool(name: str) -> str | None:
    """Find a Homebrew/CLI tool even when Finder gave the app a tiny PATH."""
    import shutil

    found = shutil.which(name)
    if found:
        return found
    for base in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"):
        candidate = Path(base) / name
        if candidate.exists():
            return str(candidate)
    return None


def access_token() -> str:
    """Shared secret for non-loopback devices (created on first use)."""
    configured = os.environ.get("MURMUR_TOKEN", "").strip()
    if configured:
        return configured

    path = HOME / "access_token"
    try:
        if path.exists():
            existing = path.read_text("utf-8").strip()
            if existing:
                return existing
    except OSError:
        pass

    import secrets

    token = secrets.token_urlsafe(24)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n", "utf-8")
        path.chmod(0o600)
    except OSError:
        pass
    return token
