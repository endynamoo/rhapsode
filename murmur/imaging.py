"""Local text-to-image backends for the Film extension.

The pipeline is deliberately pluggable: the rest of the film code only asks for
"an image at this path for this prompt". The default backend uses Sana (small,
fast, Apache-licensed code) through diffusers on Apple's Metal backend.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from . import config

MODELS = {
    "sana-600m": {
        "id": "Efficient-Large-Model/Sana_600M_512px_diffusers",
        "steps": 20,
        "guidance": 4.5,
        "native": 512,
    },
    "sana-1600m": {
        "id": "Efficient-Large-Model/Sana_1600M_1024px_diffusers",
        "steps": 24,
        "guidance": 4.5,
        "native": 1024,
    },
    "sd-turbo": {
        "id": "stabilityai/sd-turbo",
        "steps": 2,
        "guidance": 0.0,
        "native": 512,
    },
    "sana-sprint-600m": {
        "id": "Efficient-Large-Model/Sana_Sprint_0.6B_1024px_diffusers",
        "steps": 2,
        "guidance": 4.5,
        "native": 1024,
        "sprint": True,
    },
}

DEFAULT_MODEL = "sana-600m"

# Keep captions/watermarks and mushy detail out of the frames.
NEGATIVE_PROMPT = (
    "text, letters, watermark, signature, logo, caption, subtitle, "
    "blurry, low quality, low resolution, deformed, extra limbs"
)


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


class DiffusersBackend(ImageBackend):
    """Sana / SD-Turbo through diffusers on MPS, CUDA, or CPU."""

    name = "diffusers"

    def __init__(self, model_key: str = DEFAULT_MODEL) -> None:
        self.model_key = model_key if model_key in MODELS else DEFAULT_MODEL
        self._pipeline = None
        self._img2img = None
        self._lock = threading.Lock()

    @staticmethod
    def _device() -> str:
        import torch

        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def available(self) -> bool:
        try:
            import diffusers  # noqa: F401
            import torch  # noqa: F401

            device = self._device()
        except Exception:
            return False
        # CPU Sana is useful but extremely slow; require an explicit opt-in so a
        # first film build does not spend an hour per frame on a laptop.
        if device == "cpu" and not os.environ.get("MURMUR_ALLOW_CPU_FILM"):
            return False
        return True

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline

            import torch
            from diffusers import SanaPipeline, SanaSprintPipeline, StableDiffusionPipeline

            spec = MODELS[self.model_key]
            device = self._device()
            dtype = torch.float32 if device == "cpu" else torch.float16
            model_id = spec["id"]

            if spec.get("sprint"):
                pipe = SanaSprintPipeline.from_pretrained(model_id, torch_dtype=dtype)
            elif model_id.startswith("Efficient-Large-Model/Sana"):
                pipe = SanaPipeline.from_pretrained(model_id, torch_dtype=dtype)
            else:
                pipe = StableDiffusionPipeline.from_pretrained(
                    model_id, torch_dtype=dtype, safety_checker=None
                )
            pipe = pipe.to(device)
            pipe.set_progress_bar_config(disable=True)
            self._pipeline = pipe
            return pipe

    def _load_img2img(self):
        if self._img2img is not None:
            return self._img2img
        with self._lock:
            if self._img2img is not None:
                return self._img2img
            from diffusers import SanaSprintImg2ImgPipeline

            pipe = self._load()
            self._img2img = SanaSprintImg2ImgPipeline.from_pipe(pipe)
            self._img2img.set_progress_bar_config(disable=True)
            return self._img2img

    def unload(self) -> None:
        """Release the pipelines before loading a different quality preset."""
        self._pipeline = None
        self._img2img = None
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            mps = getattr(torch.backends, "mps", None)
            if mps is not None and mps.is_available():
                torch.mps.empty_cache()
        except Exception:
            pass

    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path,
                 init_image: Path | None = None, strength: float = 0.5,
                 reference_image: Path | None = None) -> None:
        import torch
        from PIL import Image, ImageOps

        spec = MODELS[self.model_key]

        # Sana expects multiples of 32. Generate the nearest valid size, then
        # crop to the requested 16:9 frame so the film is not stretched.
        gen_width = max(256, int(round(width / 32)) * 32)
        gen_height = max(256, int(round(height / 32)) * 32)

        generator = torch.Generator(device=self._device()).manual_seed(int(seed))
        reference = None
        if init_image and Path(init_image).exists():
            try:
                reference = Image.open(init_image).convert("RGB").resize((gen_width, gen_height))
            except Exception:
                reference = None

        if reference is not None and spec.get("sprint"):
            pipe = self._load_img2img()
            image = pipe(
                prompt=prompt,
                image=reference,
                strength=float(strength),
                num_inference_steps=spec["steps"],
                guidance_scale=spec["guidance"],
                negative_prompt=NEGATIVE_PROMPT,
                height=gen_height,
                width=gen_width,
                generator=generator,
            ).images[0]
        else:
            pipe = self._load()
            image = pipe(
                prompt=prompt,
                num_inference_steps=spec["steps"],
                guidance_scale=spec["guidance"],
                negative_prompt=NEGATIVE_PROMPT,
                height=gen_height,
                width=gen_width,
                generator=generator,
            ).images[0]

        if image.size != (width, height):
            image = ImageOps.fit(image, (width, height), Image.LANCZOS, centering=(0.5, 0.5))

        # Carry the previous shot's palette into every frame, including txt2img.
        if reference is not None:
            try:
                image = _match_color(image, reference, 0.4)
            except Exception:
                pass

        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(out_path)


class FluxKleinBackend(ImageBackend):
    """FLUX.2 klein 4B, 4-step distilled, through mflux on Apple MLX."""

    name = "flux2-klein-4b"

    def available(self) -> bool:
        return config.FLUX_GENERATE.exists()

    @staticmethod
    def _run(cmd: list[str], out_path: Path) -> None:
        env = dict(os.environ)
        env.setdefault("HF_HOME", str(config.HOME / "models" / "hf"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.unlink(missing_ok=True)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=900, env=env,
            errors="replace",
        )
        if result.returncode != 0 or not out_path.exists():
            tail = (result.stderr or result.stdout or "FLUX generation failed")[-900:]
            raise ImageError(tail.strip())

    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path,
                 init_image: Path | None = None, strength: float = 0.5,
                 reference_image: Path | None = None) -> None:
        if reference_image is not None and Path(reference_image).exists():
            cmd = [
                str(config.FLUX_EDIT),
                "--model", config.FLUX_MODEL_ID,
                "--base-model", config.FLUX_BASE_MODEL,
                "--image-paths", str(reference_image),
                "--prompt", prompt,
                "--steps", "4",
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
                "--steps", "4",
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
        with self._backend_lock:
            if self._backend is not None and self._model_key == key:
                return self._backend

            candidate = (
                FluxKleinBackend() if key.startswith("flux")
                else DiffusersBackend(key)
            )
            if not candidate.available() and key.startswith("flux"):
                candidate = DiffusersBackend("sana-1600m")
            if candidate.available():
                self._backend = candidate
                self._model_key = key
            elif self._backend is None:
                self._backend = PlaceholderBackend()
                self._model_key = key
            return self._backend

    def generate(self, prompt: str, out_path: Path, seed: int,
                 width: int = config.FILM_WIDTH, height: int = config.FILM_HEIGHT,
                 init_image: Path | None = None, strength: float = 0.5,
                 model: str | None = None,
                 reference_image: Path | None = None) -> str:
        if out_path.exists() and out_path.stat().st_size > 1024:
            return "cached"
        with self._lock:
            if out_path.exists() and out_path.stat().st_size > 1024:
                return "cached"
            backend = self.backend(model)
            backend.generate(prompt, width, height, seed, out_path,
                             init_image=init_image, strength=strength,
                             reference_image=reference_image)
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
