"""Local text-to-image for the stills pipeline: FLUX.2 [klein] 4B only.

One model family, two presets â€” High paints 1920x1080 in four distilled steps,
Fast paints 1280x720 in two. Two ways to run it: mflux on Apple Silicon (the
MLX build), and diffusers in a separate interpreter (`image-venv`) everywhere
else, so the reader never imports torch. The rest of the code only asks for "an
image at this path for this prompt".
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from . import config, proc
from .activity import activity

MODELS = {
    "flux2-klein-4b": {
        "id": config.FLUX_WEIGHTS,
        "steps": 4,
        "width": 1920,
        "height": 1080,
        "label": "FLUX.2 [klein] 4B â€” High",
    },
    "flux2-klein-4b-fast": {
        "id": config.FLUX_WEIGHTS,
        "steps": 2,
        "width": 1280,
        "height": 720,
        "label": "FLUX.2 [klein] 4B â€” Fast",
    },
}

DEFAULT_MODEL = "flux2-klein-4b"

# A real still at 1080p is hundreds of kilobytes. The placeholder painter writes a
# prompt card of roughly 8 KB, so anything smaller is not art and must be repainted.
MIN_STILL_BYTES = 24_000


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


_SHARED_WORKER: "FluxWorker | None" = None


def _painter_command(python: Path, script: Path, *args: str) -> list[str]:
    """The painter's command line, always with -I.

    `-I` makes the child isolated: it implies -P (no script directory on
    sys.path) and -s (no user site-packages). Without it, `import profile`
    resolves to `murmur/profile.py` because sys.path[0] is the script's own
    folder, and torch's import of cProfile then dies with "module 'profile' has
    no attribute 'run'". A frozen build ships fluxgen.py where no sibling
    profile.py exists, so only a source install could ever hit it - which is
    why it survived this long.
    """
    return [str(python), "-I", str(script), *args]


class FluxWorker:
    """A resident `fluxgen --serve` painter: one model load per app session.

    Loading the weights costs ~30 s of transfer plus a ROCm warm-up; paying that
    once instead of once per still is the single biggest saving in the pipeline.
    A second worker would not help: one image's steps are a chain, so two painters
    only time-slice the card.
    """

    READY_TIMEOUT = 1200.0

    def __init__(self, python: Path, script: Path | None) -> None:
        self.python = python
        self.script = script
        self._proc = None
        self._lock = threading.Lock()
        self._counter = 0
        self.ready_info: dict = {}
        self.last_job: dict = {}
        atexit.register(self.stop)

    def _env(self) -> dict:
        env = dict(os.environ)
        env.setdefault("HF_HOME", str(config.HOME / "models" / "hf"))
        return env

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure(self) -> None:
        if self.alive:
            return
        self.stop()
        if self.script is None:
            raise ImageError("fluxgen.py is missing from the installation.")
        # Capture the child's stderr: a painter that dies on import or runs out of
        # memory is otherwise invisible, because its parent is a windowed process.
        try:
            err = open(config.HOME / "painter.log", "a", encoding="utf-8", buffering=1)
        except OSError:
            err = subprocess.DEVNULL
        # -I runs the painter isolated: no script-directory on sys.path and no
        # user site-packages. Without it `import profile` resolves to
        # murmur/profile.py instead of the stdlib (sys.path[0] is the script's
        # folder), and torch's own import of cProfile then dies with
        # "module 'profile' has no attribute 'run'" - a source install could
        # never paint a still, while a frozen build never saw it.
        process = proc.popen(
            _painter_command(self.python, self.script, "--serve"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
            bufsize=1,
            cwd=str(config.APP_ROOT),
            env=self._env(),
        )
        ready = self._read_line(process.stdout, self.READY_TIMEOUT)
        self.ready_info = ready or {}
        if not ready or not ready.get("ready"):
            process.kill()
            detail = (ready or {}).get("error", "no answer from the painter")
            raise ImageError(f"the painter did not start: {detail}")
        self._proc = process

    @staticmethod
    def _read_line(stream, timeout: float) -> dict | None:
        box: queue.Queue = queue.Queue(maxsize=1)

        def reader() -> None:
            try:
                box.put(stream.readline())
            except Exception:  # noqa: BLE001 - a closed pipe is a dead painter
                box.put("")

        threading.Thread(target=reader, name="flux-reader", daemon=True).start()
        try:
            line = box.get(timeout=timeout)
        except queue.Empty:
            return None
        if not line:
            return None
        try:
            return json.loads(line)
        except ValueError:
            return None

    def warm(self) -> dict:
        """Load the model without a job, so the first request is only a paint."""
        with self._lock:
            self._ensure()
            return dict(self.ready_info)

    def run(self, job: dict, timeout: float = 900.0) -> None:
        with self._lock:
            self._ensure()
            self._counter += 1
            payload = dict(job, id=str(self._counter))
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
            answer = self._read_line(self._proc.stdout, timeout)
            if answer is None:
                self.stop()
                raise ImageError("the painter stopped answering")
            if not answer.get("ok"):
                raise ImageError(str(answer.get("error") or "painting failed")[-900:])

    def stop(self) -> None:
        process, self._proc = self._proc, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write('{"op": "quit"}\n')
                process.stdin.flush()
            process.wait(timeout=5)
            return
        except Exception:  # noqa: BLE001 - fall through to a hard kill
            pass
        try:
            process.kill()
        except Exception:  # noqa: BLE001 - already gone
            pass


_PACKAGES = ("torch", "diffusers")


def _site_packages(python: Path) -> list[Path]:
    """Where a venv keeps its packages, without asking the interpreter."""
    venv = python.parent.parent
    roots = [venv / "Lib" / "site-packages"]
    try:
        roots += sorted((venv / "lib").glob("python3*/site-packages"))
    except OSError:
        pass
    return [root for root in roots if root.is_dir()]


def _pth_paths(root: Path) -> list[Path]:
    """Directories injected by `.pth` files.

    A path line is a directory to add; a line that runs code (`import ...`) is
    skipped rather than trusted, so the answer here can only ever be a reason to
    say "yes", never a reason to say "no".
    """
    extra: list[Path] = []
    try:
        pth_files = sorted(root.glob("*.pth"))
    except OSError:
        return extra
    for pth in pth_files:
        try:
            lines = pth.read_text("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("import "):
                continue
            candidate = Path(line)
            if candidate.is_absolute() and candidate.is_dir():
                extra.append(candidate)
    return extra


def _has_packages(python: Path, names: tuple[str, ...] = _PACKAGES) -> bool:
    """Whether the interpreter can see these packages, by inspection.

    Only ever returns True for a package it can actually see, because a `.pth`
    file can point at a directory anywhere on disk - this machine's image venv
    gets torch from an unrelated studio environment exactly that way. "Not found"
    means "ask the interpreter", not "not installed".
    """
    search = _site_packages(python)
    for root in list(search):
        search += _pth_paths(root)
    for name in names:
        found = any(
            (root / name / "__init__.py").exists() or (root / f"{name}.py").exists()
            for root in search
        )
        if not found:
            return False
    return True


class FluxPythonBackend(ImageBackend):
    """FLUX.2 [klein] through diffusers, in the image interpreter.

    The whole run happens in a child process (`python -m murmur.fluxgen`) so the
    server never imports torch: an 8 GB model load cannot take the reader down,
    and the GPU stack lives in image-venv where it can be replaced.
    """

    name = "flux2-klein-diffusers"

    _probe: bool | None = None
    _probe_lock = threading.Lock()
    _worker: "FluxWorker | None" = None
    _last_error: str | None = None

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
            # Reading the environment costs a stat; importing torch in the image
            # interpreter costs about six seconds, on every first still, to answer
            # the same question. Only a positive answer is trusted: "not found"
            # could be a .pth that runs code, so the interpreter decides.
            if _has_packages(self.python):
                self._probe = True
                return True
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
        source = reference_image if reference_image and Path(reference_image).exists() else init_image
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.unlink(missing_ok=True)

        job = {
            "prompt": prompt,
            "out": str(out_path),
            "width": width,
            "height": height,
            "seed": int(seed),
            "steps": spec["steps"],
            "reference": str(source) if source is not None and Path(source).exists() else None,
            "strength": float(strength) if source is not None else None,
        }

        worker = self.worker()
        try:
            worker.run(job, timeout=900)
            if out_path.exists() and out_path.stat().st_size > 1024:
                return
            raise ImageError("the painter produced no image")
        except Exception as exc:  # noqa: BLE001 - fall back to a one-shot run
            self._last_error = str(exc)
        self._run_once(script, job)

    def _run_once(self, script: Path, job: dict) -> None:
        """Slow path: a fresh interpreter, used when the resident painter fails."""
        env = dict(os.environ)
        env.setdefault("HF_HOME", str(config.HOME / "models" / "hf"))
        command = [
            *_painter_command(self.python, script),
            "--prompt", job["prompt"],
            "--width", str(job["width"]), "--height", str(job["height"]),
            "--seed", str(job["seed"]),
            "--steps", str(job["steps"]),
            "--out", job["out"],
        ]
        if job.get("reference"):
            command += ["--reference", job["reference"]]
        if job.get("mask"):
            command += ["--mask", job["mask"]]
        if job.get("strength") is not None:
            command += ["--strength", str(job["strength"])]
        out_path = Path(job["out"])
        try:
            # Popen + adopt: a one-shot painter left running after the app exits
            # pins ~13 GiB of VRAM, and atexit only covers the resident worker.
            child = proc.spawn(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, text=True, cwd=str(config.APP_ROOT),
                env=env, errors="replace",
            )
            try:
                out_text, err_text = child.communicate(timeout=900)
            except subprocess.TimeoutExpired as exc:
                child.kill()
                child.communicate()
                raise ImageError(
                    "FLUX stopped answering after 15 minutes; the still was skipped."
                ) from exc
            if child.returncode != 0 or not out_path.exists():
                tail = (err_text or out_text or "FLUX generation failed")[-900:]
                raise ImageError(tail.strip())
        except subprocess.SubprocessError as exc:
            raise ImageError(f"FLUX run failed to start: {exc}") from exc

    def worker(self) -> "FluxWorker":
        """The process-wide painter.

        FilmImages and the polish pass both ask for it, and a second FluxWorker
        would try to load the weights again â€” or sit waiting for a lock it can
        never get â€” so the instance is shared rather than per-backend.
        """
        global _SHARED_WORKER
        with self._probe_lock:
            if _SHARED_WORKER is None:
                _SHARED_WORKER = FluxWorker(self.python, self.script)
            return _SHARED_WORKER


def restart_painter() -> dict:
    """Drop the resident painter so the next job loads the current profile.

    The child inherits its precision from the environment at spawn, so changing the
    profile only takes effect on a fresh process; this is what the Restart Engine
    button calls.
    """
    global _SHARED_WORKER
    with FluxPythonBackend._probe_lock:  # noqa: SLF001 - module-level intent
        worker, _SHARED_WORKER = _SHARED_WORKER, None
    if worker is not None:
        worker.stop()
    return painter_info()

def ram_info() -> dict:
    """Free and total system memory, in GiB, for the engine panel.

    The painter refuses to load below MURMUR_FLUX_RAM_FLOOR_MB, so the panel needs
    the same number to explain why a still will not start.
    """
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {}
        return {
            "free_gib": round(status.ullAvailPhys / 2**30, 2),
            "total_gib": round(status.ullTotalPhys / 2**30, 2),
            "floor_mib": int(float(os.environ.get("MURMUR_FLUX_RAM_FLOOR_MB", "6000"))),
        }
    except Exception:
        return {}


_PLACEHOLDER_WHY: str | None = None


def placeholder_reason() -> str:
    """Why stills are placeholders, in one sentence a reader can act on.

    Issue #40: the placeholder used to blame "this machine" without saying which
    of the three causes it was, so an install that simply skipped the stills
    engine read as a hardware limitation. The answer is computed once per
    process: it costs a subprocess probe, and it cannot change under a running
    server without a restart anyway.
    """
    global _PLACEHOLDER_WHY
    if _PLACEHOLDER_WHY is None:
        _PLACEHOLDER_WHY = _compute_placeholder_reason()
    return _PLACEHOLDER_WHY


def _compute_placeholder_reason() -> str:
    python = config.IMAGE_PYTHON
    if not python.exists():
        return (
            f"Scene left as a placeholder: there is no stills engine in this data folder, "
            f"because {python} does not exist. "
            "Run scripts\\setup_image.ps1 to build it — it downloads the model once — or "
            "reinstall and tick the stills engine task. The reader works without it."
        )
    if not FluxPythonBackend().available():
        return (
            f"Scene left as a placeholder: the stills engine at {python} exists but cannot "
            "import torch and diffusers, so it will not start. Rebuild it with "
            "scripts\\setup_image.ps1."
        )
    if sys.platform == "darwin" and not config.FLUX_GENERATE.exists():
        return (
            f"Scene left as a placeholder: this Mac build has no stills helper at "
            f"{config.FLUX_GENERATE}. Rebuild the app with ./build_app.sh."
        )
    return (
        "Scene left as a placeholder: the stills engine is unavailable for a reason this "
        "build does not recognise. See docs/STILLS.md, or Settings → Stills engine for what "
        "the painter last reported."
    )


def painter_info() -> dict:
    """What the resident painter decided (precision, VRAM headroom) for the UI."""
    backend = FluxPythonBackend()
    worker = backend.worker()
    info = dict(worker.ready_info)
    info["running"] = worker.alive
    info["python"] = str(config.IMAGE_PYTHON)
    ram = ram_info()
    if ram:
        info["ram"] = ram
    if worker.last_job:
        info["last_job"] = {
            key: worker.last_job.get(key)
            for key in ("mode", "seconds", "width", "height", "steps",
                        "peak_gib", "resident_gib", "free_gib")
            if key in worker.last_job
        }
    return info


def paint_job(job: dict, timeout: float = 900.0) -> None:
    """Send one raw job to the resident painter, falling back to a one-shot run.

    A second painter is refused by the machine-wide lock, so the fallback keeps a
    hand repair working while another server on this box owns the painter.
    """
    backend = FluxPythonBackend()
    try:
        backend.worker().run(job, timeout=timeout)
        return
    except Exception as exc:  # noqa: BLE001 - try the slow path instead
        if not config.IMAGE_PYTHON.exists():
            raise
        script = backend.script
        if script is None:
            raise
        backend._run_once(script, job)  # noqa: SLF001 - same module's backend
        return
    finally:
        pass


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
        # A cold painter pays the model load inside the first generate(), which
        # is why that call is reported as loading and not as painting.
        self._warm = False
        self._warming = False

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

    def warm(self) -> bool:
        """Load the backend's model without a job, so the first still is a paint.

        Shares the worker with `generate()`, which holds the same lock: a scene
        asked for while this is still loading joins the load instead of starting a
        second one.
        """
        backend = self.backend()
        worker = getattr(backend, "worker", None)
        if worker is None:
            return False
        painter = worker()
        self._warming = True
        try:
            if not getattr(painter, "alive", False):
                painter.warm()
            self._warm = True
            return True
        finally:
            self._warming = False

    def generate(self, prompt: str, out_path: Path, seed: int,
                 width: int = config.FILM_WIDTH, height: int = config.FILM_HEIGHT,
                 init_image: Path | None = None, strength: float = 0.5,
                 model: str | None = None,
                 reference_image: Path | None = None,
                 label: str = "") -> str:
        marker = out_path.with_suffix(out_path.suffix + ".placeholder")
        cached = out_path.exists() and out_path.stat().st_size >= MIN_STILL_BYTES
        with self._lock:
            backend = self.backend(model)
            # A placeholder is not a still: once a real engine exists, repaint it.
            if cached and marker.exists() and backend.name != "placeholder":
                cached = False
            if cached:
                return "cached"
            if backend.name == "placeholder":
                activity.phase("placeholder", placeholder_reason())
            elif not self._warm and self._warming:
                # The engine is already loading because the plan was opened first;
                # this still waits for that load, not for a new one.
                activity.phase(
                    "loading",
                    "Finishing the stills engine load",
                    coldStart=True, backend=backend.name, model=model or self._model_key,
                )
            elif not self._warm:
                # The load happens inside backend.generate() below, so this is
                # the honest label for the whole first call of the session.
                activity.phase(
                    "loading",
                    "Loading the stills engine - first still this session",
                    coldStart=True, backend=backend.name, model=model or self._model_key,
                )
            else:
                activity.phase(
                    "painting", label or "Painting the still",
                    coldStart=False,
                    backend=backend.name, model=model or self._model_key,
                )
            backend.generate(prompt, width, height, seed, out_path,
                             init_image=init_image, strength=strength,
                             reference_image=reference_image)
            if backend.name == "placeholder":
                marker.write_text("placeholder\n", "utf-8")
            else:
                marker.unlink(missing_ok=True)
                self._warm = True
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

