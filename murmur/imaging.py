"""Local text-to-image for the stills pipeline: FLUX.2 [klein] 4B only.

One model family, two presets — High paints 1920x1080 in four distilled steps,
Fast paints 1280x720 in two. Two ways to run it: mflux on Apple Silicon (the
MLX build), and diffusers in a separate interpreter (`image-venv`) everywhere
else, so the reader never imports torch. The rest of the code only asks for "an
image at this path for this prompt".
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from . import config, proc

MODELS = {
    "flux2-klein-4b": {
        "id": config.FLUX_WEIGHTS,
        "steps": 4,
        "width": 1920,
        "height": 1080,
        "label": "FLUX.2 [klein] 4B — High",
    },
    "flux2-klein-4b-fast": {
        "id": config.FLUX_WEIGHTS,
        "steps": 2,
        "width": 1280,
        "height": 720,
        "label": "FLUX.2 [klein] 4B — Fast",
    },
}

DEFAULT_MODEL = "flux2-klein-4b"


class ImageError(Exception):
    pass


class ImageBackend:
    name = "base"

    def available(self) -> bool:
        return False

    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path,
                 init_image: Path | None = None, strength: float = 0.5,
                 reference_image: Path | None = None) -> None:
        raise NotImplementedError


class PlaceholderBackend(ImageBackend):
    """Deterministic coloured card, so the UI works before models are installed."""

    name = "placeholder"

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path,
                 init_image: Path | None = None, strength: float = 0.5,
                 reference_image: Path | None = None) -> None:
        import hashlib

        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        digest = hashlib.sha1(f"{seed}|{prompt}".encode("utf-8")).digest()
        base = np.array([0.10 + (b / 255.0) * 0.28 for b in digest[:3]], dtype=np.float32)
        accent = np.clip(base + np.array([0.28, 0.18, 0.34], dtype=np.float32), 0, 1)

        rows = np.linspace(1.0, 0.62, height, dtype=np.float32)[:, None, None]
        canvas = (base[None, None, :] * rows) + (accent[None, None, :] * (1.0 - rows) * 0.55)
        canvas = (np.clip(canvas, 0, 1) * 255).astype(np.uint8)
        image = Image.fromarray(np.repeat(canvas, width, axis=1), "RGB")

        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        for i in range(10):
            t = i / 9
            radius = int(width * (0.55 - 0.03 * i))
            center = (int(width * (0.3 + 0.4 * t)), int(height * (0.55 - 0.2 * t)))
            gdraw.ellipse(
                [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
                fill=tuple(int(c * 255) for c in accent) + (12,),
            )
        image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")

        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", max(12, width // 42))
        except OSError:
            font = ImageFont.load_default()
        draw.multiline_text(
            (int(width * 0.06), int(height * 0.80)),
            prompt[:180],
            fill=(242, 238, 230),
            font=font,
            spacing=6,
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(out_path, format="PNG")


class FluxPythonBackend(ImageBackend):
    """FLUX.2 [klein] through diffusers, in the image interpreter.

    The whole run happens in a child process (`python -m murmur.fluxgen`) so the
    server never imports torch: an 8 GB model load cannot take the reader down,
    and the GPU stack lives in image-venv where it can be replaced.
    """

    name = "flux2-klein-diffusers"

    _probe: bool | None = None
    _probe_lock = threading.Lock()

    def __init__(self, model_key: str = DEFAULT_MODEL) -> None:
        self.model_key = model_key if model_key in MODELS else DEFAULT_MODEL

    @property
    def python(self) -> Path:
        return config.IMAGE_PYTHON

    @property
    def script(self) -> Path | None:
        """fluxgen.py as a plain file, so a frozen build can hand it to image-venv.

        The server cannot run `-m murmur.fluxgen` once it is a bundle: there is no
        interpreter, and the package lives inside the archive.
        """
        for candidate in (
            config.ASSET_ROOT / "fluxgen.py",
            Path(__file__).with_name("fluxgen.py"),
        ):
            if candidate.exists():
                return candidate
        return None

    def available(self) -> bool:
        if not self.python.exists():
            return False
        if self._probe is not None:
            return self._probe
        with self._probe_lock:
            if self._probe is not None:
                return self._probe
            try:
                result = proc.run(
                    [str(self.python), "-c", "import torch, diffusers"],
                    capture_output=True, text=True, timeout=180,
                )
                self._probe = result.returncode == 0
            except Exception:
                self._probe = False
            return bool(self._probe)

    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path,
                 init_image: Path | None = None, strength: float = 0.5,
                 reference_image: Path | None = None) -> None:
        spec = MODELS[self.model_key]
        script = self.script
        if script is None:
            raise ImageError("fluxgen.py is missing from the installation.")
        env = dict(os.environ)
        env.setdefault("HF_HOME", str(config.HOME / "models" / "hf"))
        command = [
            str(self.python), str(script),
            "--prompt", prompt,
            "--width", str(width), "--height", str(height),
            "--seed", str(int(seed)),
            "--steps", str(spec["steps"]),
            "--out", str(out_path),
        ]
        source = reference_image if reference_image and Path(reference_image).exists() else init_image
        if source is not None and Path(source).exists():
            command += ["--reference", str(source), "--strength", str(float(strength))]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.unlink(missing_ok=True)
        try:
            result = proc.run(
                command, capture_output=True, text=True, timeout=900,
                cwd=str(config.APP_ROOT), env=env, errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise ImageError(
                "FLUX stopped answering after 15 minutes; the still was skipped."
            ) from exc
        except subprocess.SubprocessError as exc:
            raise ImageError(f"FLUX run failed to start: {exc}") from exc
        if result.returncode != 0 or not out_path.exists():
            tail = (result.stderr or result.stdout or "FLUX generation failed")[-900:]
            raise ImageError(tail.strip())


class FluxKleinBackend(ImageBackend):
    """FLUX.2 [klein] 4B through mflux on Apple MLX (the macOS build)."""

    name = "flux2-klein-mlx"

    def __init__(self, model_key: str = DEFAULT_MODEL) -> None:
        self.model_key = model_key if model_key in MODELS else DEFAULT_MODEL

    def available(self) -> bool:
        return config.FLUX_GENERATE.exists()

    @staticmethod
    def _run(cmd: list[str], out_path: Path) -> None:
        env = dict(os.environ)
        env.setdefault("HF_HOME", str(config.HOME / "models" / "hf"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.unlink(missing_ok=True)
        result = proc.run(
            cmd, capture_output=True, text=True, timeout=1800, env=env,
            errors="replace",
        )
        if result.returncode != 0 or not out_path.exists():
            tail = (result.stderr or result.stdout or "FLUX generation failed")[-900:]
            raise ImageError(tail.strip())

    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path,
                 init_image: Path | None = None, strength: float = 0.5,
                 reference_image: Path | None = None) -> None:
        steps = str(MODELS[self.model_key]["steps"])
        if reference_image is not None and Path(reference_image).exists():
            cmd = [
                str(config.FLUX_EDIT),
                "--model", config.FLUX_MODEL_ID,
                "--base-model", config.FLUX_BASE_MODEL,
                "--image-paths", str(reference_image),
                "--prompt", prompt,
                "--steps", steps,
                "--width", str(width), "--height", str(height),
                "--seed", str(int(seed)),
                "--output", str(out_path),
            ]
        else:
            cmd = [
                str(config.FLUX_GENERATE),
                "--model", config.FLUX_MODEL_ID,
                "--base-model", config.FLUX_BASE_MODEL,
                "--prompt", prompt,
                "--steps", steps,
                "--width", str(width), "--height", str(height),
                "--seed", str(int(seed)),
                "--output", str(out_path),
            ]
            if init_image is not None and Path(init_image).exists():
                cmd += ["--image", str(init_image), str(float(strength))]
        self._run(cmd, out_path)


class FilmImages:
    """Serialises generation so the GPU is not oversubscribed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backend_lock = threading.Lock()
        self._backend: ImageBackend | None = None
        self._model_key: str | None = None

    def backend(self, model_key: str | None = None) -> ImageBackend:
        key = model_key or DEFAULT_MODEL
        if key not in MODELS:
            key = DEFAULT_MODEL
        with self._backend_lock:
            if self._backend is not None and self._model_key == key:
                return self._backend

            # mflux on Apple Silicon, diffusers in image-venv everywhere else.
            for candidate in (FluxKleinBackend(key), FluxPythonBackend(key)):
                if candidate.available():
                    self._backend = candidate
                    self._model_key = key
                    return self._backend
            if self._backend is None:
                self._backend = PlaceholderBackend()
                self._model_key = key
            return self._backend

    def generate(self, prompt: str, out_path: Path, seed: int,
                 width: int = config.FILM_WIDTH, height: int = config.FILM_HEIGHT,
                 init_image: Path | None = None, strength: float = 0.5,
                 model: str | None = None,
                 reference_image: Path | None = None) -> str:
        marker = out_path.with_suffix(out_path.suffix + ".placeholder")
        cached = out_path.exists() and out_path.stat().st_size > 1024
        with self._lock:
            backend = self.backend(model)
            # A placeholder is not a still: once a real engine exists, repaint it.
            if cached and marker.exists() and backend.name != "placeholder":
                cached = False
            if cached:
                return "cached"
            backend.generate(prompt, width, height, seed, out_path,
                             init_image=init_image, strength=strength,
                             reference_image=reference_image)
            if backend.name == "placeholder":
                marker.write_text("placeholder\n", "utf-8")
            else:
                marker.unlink(missing_ok=True)
            return backend.name


image_gen = FilmImages()


def _match_color(image, reference, amount: float = 0.4):
    """Pull the palette of a new frame toward the previous one."""
    import numpy as np
    from PIL import Image

    reference = reference.convert("RGB").resize(image.size)
    a = np.asarray(image.convert("RGB"), dtype=np.float32)
    b = np.asarray(reference, dtype=np.float32)
    original = a.copy()
    for channel in range(3):
        a_mean, a_std = a[..., channel].mean(), a[..., channel].std() + 1e-6
        b_mean, b_std = b[..., channel].mean(), b[..., channel].std() + 1e-6
        a[..., channel] = (a[..., channel] - a_mean) * (b_std / a_std) + b_mean
    blended = (a * amount + original * (1 - amount)).clip(0, 255).astype(np.uint8)
    return Image.fromarray(blended)
