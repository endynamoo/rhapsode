"""FLUX.2 [klein] stills, painted by diffusers in a separate interpreter.

The app never imports torch: this module runs in the image virtual environment
(`image-venv`, or any interpreter with torch + diffusers) and writes one PNG.

Two ways in:

* one shot — ``python fluxgen.py --prompt ... --out still.png``
* resident — ``python fluxgen.py --serve`` keeps the pipeline loaded and reads
  JSON jobs from stdin, one per line, answering ``{"id": ..., "ok": true}``. This
  is what the app uses: the 15 GiB transfer and the ROCm warm-up happen once per
  session instead of once per still.

A second process would not make one image faster — denoising is a chain of steps
for a single sample, so two painters only time-slice the same card (and the
weights leave no headroom for a batch of two).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MODEL_ID = os.environ.get("MURMUR_FLUX_MODEL", "black-forest-labs/FLUX.2-klein-4B")


def _device(torch) -> str:
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _vram(torch) -> tuple[float, float]:
    """(free MiB, total MiB) on this device; (0, 0) when the backend cannot say."""
    try:
        free, total = torch.cuda.mem_get_info()
        return free / 2**20, total / 2**20
    except Exception:
        return 0.0, 0.0


def _reserve_mib(torch) -> float:
    """Headroom left for the desktop, the remote-desktop encoder and the browser.

    This machine's card is 16 GB and undeclared overcommit does not degrade
    gracefully: Windows starts paging the GPU, the whole session stalls, and a
    remote session (Sunshine) loses the desktop with it. Reserve generously.
    """
    try:
        return float(os.environ.get("MURMUR_FLUX_RESERVE_MB", "2500"))
    except ValueError:
        return 2500.0


def _gguf_path() -> Path | None:
    """The q4 transformer, if the machine has one downloaded."""
    explicit = os.environ.get("MURMUR_FLUX_GGUF")
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    roots = [Path(os.environ.get("HF_HOME", ""))] if os.environ.get("HF_HOME") else []
    roots += [Path.home() / ".cache" / "huggingface"]
    for root in roots:
        hub = root / "hub"
        if not hub.exists():
            continue
        for path in hub.rglob("*klein*Q4_K_M*.gguf"):
            return path
        for path in hub.rglob("*klein*Q4*.gguf"):
            return path
    return None


def _plan(torch, device: str) -> dict:
    """Choose precision and where the text encoder lives, from free VRAM.

    8 GB cards are the target (5060/4060/9060 class): the q4 transformer is 2.6 GiB
    and the VAE 0.3 GiB, so the card keeps its headroom — but the text encoder
    (Qwen3, ~8 GiB in bf16) only fits on bigger cards and stays on the CPU
    elsewhere. Encoding a prompt on the CPU costs seconds, once per prompt.
    """
    reserve = _reserve_mib(torch)
    free, total = _vram(torch)
    requested = (os.environ.get("MURMUR_FLUX_PRECISION") or "").strip().lower()
    gguf = _gguf_path()
    offload = os.environ.get("MURMUR_FLUX_OFFLOAD") == "1"

    if device != "cuda":
        return {"precision": "bf16", "offload": offload, "text_device": device, "why": "non-cuda device"}
    if free <= 0:
        return {"precision": "bf16", "offload": offload, "text_device": device, "why": "VRAM unknown"}

    def q4(with_text_on_gpu: bool) -> dict:
        return {
            "precision": "q4", "gguf": gguf, "offload": False,
            "text_device": "cuda" if with_text_on_gpu else "cpu",
            "why": (
                f"{free:.0f} MiB free of {total:.0f} ({reserve:.0f} reserved): q4 transformer"
                + (", text encoder on the GPU" if with_text_on_gpu else ", text encoder on the CPU")
            ),
        }

    if requested == "q4" or (requested != "bf16" and gguf and free < 18_500):
        if gguf is None:
            if free >= 9_000 + reserve:
                return {"precision": "bf16", "offload": True, "text_device": "cuda",
                        "why": "no q4 transformer available: bf16 with CPU offload"}
            raise SystemExit(
                f"only {free:.0f} MiB free and no q4 transformer on disk; "
                "run scripts\\setup_image.ps1 to fetch it."
            )
        if free >= 11_500 + reserve:
            return q4(True)
        if free >= 3_500 + reserve:
            return q4(False)
        raise SystemExit(
            f"refusing to paint: {free:.0f} MiB of {total:.0f} MiB free, "
            f"{reserve:.0f} MiB reserved for the desktop. Close the other GPU job."
        )

    if free >= 15_500 + reserve:
        return {"precision": "bf16", "offload": False, "text_device": "cuda",
                "why": f"{free:.0f} MiB free: full bf16 residency"}
    if free >= 9_000 + reserve:
        return {"precision": "bf16", "offload": True, "text_device": "cuda",
                "why": f"{free:.0f} MiB free: bf16 with CPU offload"}
    raise SystemExit(
        f"refusing to load: {free:.0f} MiB of {total:.0f} MiB is free and "
        f"{reserve:.0f} MiB is reserved for the desktop and the remote session."
    )


def _load(torch, device: str, plan: dict):
    from diffusers import Flux2KleinPipeline

    dtype = torch.bfloat16 if device != "cpu" else torch.float32
    if plan.get("precision") == "q4" and plan.get("gguf"):
        from diffusers import Flux2Transformer2DModel, GGUFQuantizationConfig

        transformer = Flux2Transformer2DModel.from_single_file(
            str(plan["gguf"]),
            config=MODEL_ID,
            subfolder="transformer",
            quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
            torch_dtype=torch.bfloat16,
        )
        pipe = Flux2KleinPipeline.from_pretrained(
            MODEL_ID, transformer=transformer, torch_dtype=torch.bfloat16
        )
    else:
        pipe = Flux2KleinPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16)

    if plan.get("offload"):
        pipe.enable_model_cpu_offload()
        pipe.set_progress_bar_config(disable=True)
        return pipe

    pipe.transformer.to(device)
    pipe.vae.to(device)
    text_device = plan.get("text_device") or device
    if hasattr(pipe, "text_encoder") and pipe.text_encoder is not None:
        pipe.text_encoder.to(text_device)
    pipe.set_progress_bar_config(disable=True)
    return pipe


def _inpaint_pipeline(pipe, torch, device: str, cache: dict):
    """Share the text2img components instead of loading 15 GiB a second time."""
    if "inpaint" not in cache:
        from diffusers import Flux2KleinInpaintPipeline

        inpaint = Flux2KleinInpaintPipeline.from_pipe(pipe)
        inpaint.set_progress_bar_config(disable=True)
        cache["inpaint"] = inpaint
    return cache["inpaint"]


def _paint(pipe, torch, device: str, job: dict, cache: dict | None = None) -> Path:
    from PIL import Image

    cache = cache if cache is not None else {}
    width = max(256, int(round(int(job.get("width", 1280)) / 16)) * 16)
    height = max(256, int(round(int(job.get("height", 720)) / 16)) * 16)
    out_path = Path(job["out"])
    mask_path = job.get("mask")
    reference = job.get("reference")

    if mask_path and reference and Path(mask_path).exists() and Path(reference).exists():
        pipeline = _inpaint_pipeline(pipe, torch, device, cache)
        image = Image.open(reference).convert("RGB").resize((width, height))
        mask = Image.open(mask_path).convert("L").resize((width, height))
        kwargs = {
            "prompt": job["prompt"],
            "image": image,
            "mask_image": mask,
            "strength": float(job.get("strength", 0.75)),
            "num_inference_steps": int(job.get("steps", 2)),
            "guidance_scale": float(job.get("guidance", 1.0)),
            "generator": torch.Generator(device=device).manual_seed(int(job.get("seed", 1))),
        }
        try:
            result = pipeline(**kwargs).images[0]
        except TypeError:
            kwargs.pop("strength", None)
            result = pipeline(**kwargs).images[0]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(out_path, format="PNG")
        return out_path

    kwargs = {
        "prompt": job["prompt"],
        "width": width,
        "height": height,
        "num_inference_steps": int(job.get("steps", 2)),
        "guidance_scale": float(job.get("guidance", 1.0)),
        "generator": torch.Generator(device=device).manual_seed(int(job.get("seed", 1))),
    }
    if reference and Path(reference).exists():
        kwargs["image"] = Image.open(reference).convert("RGB").resize((width, height))
        if job.get("strength") is not None:
            # Only inpaint-style pipelines accept this; text2img ignores it.
            kwargs["strength"] = float(job["strength"])

    try:
        image = pipe(**kwargs).images[0]
    except TypeError:
        kwargs.pop("strength", None)
        image = pipe(**kwargs).images[0]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, format="PNG")
    return out_path


def generate(
    prompt: str,
    out_path: Path,
    width: int,
    height: int,
    seed: int,
    steps: int = 4,
    reference: Path | None = None,
    strength: float | None = None,
    guidance: float = 1.0,
    mask: Path | None = None,
) -> None:
    """One-shot paint: load, paint, exit."""
    import torch

    device = _device(torch)
    if device == "cpu" and not os.environ.get("MURMUR_ALLOW_CPU_FILM"):
        raise SystemExit("no GPU is visible to torch; refusing to paint on the CPU")

    plan = _plan(torch, device)
    print(f"fluxgen: {plan['why']}", file=sys.stderr, flush=True)
    try:
        pipe = _load(torch, device, plan)
    except RuntimeError as exc:
        if "out of memory" not in str(exc).lower():
            raise
        torch.cuda.empty_cache()
        pipe = _load(torch, device, dict(plan, offload=True))

    _paint(
        pipe, torch, device,
        {
            "prompt": prompt, "out": str(out_path), "width": width, "height": height,
            "seed": seed, "steps": steps, "guidance": guidance,
            "reference": str(reference) if reference else None, "strength": strength,
            "mask": str(mask) if mask else None,
        },
    )


def _parent_alive(pid: int) -> bool:
    """True while the process that spawned us is still there."""
    if pid <= 0:
        return False
    try:
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return True  # never take the painter down over a broken probe


def _watch_parent(parent: int) -> None:
    """Exit when the app dies: a 15 GiB orphan is what wedges this box."""
    import threading
    import time

    def watch() -> None:
        while True:
            time.sleep(5.0)
            if not _parent_alive(parent):
                os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def _claim_lock() -> Path | None:
    """Only one painter per machine: two 15 GiB loads cannot share a 16 GB card.

    The lock records the painter's pid *and* its parent's, so a stale file left by a
    killed server is not mistaken for a live painter.
    """
    root = Path(os.environ.get("MURMUR_HOME", str(Path.home())))
    lock = root / "flux-painter.lock"
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists():
            entries = lock.read_text("utf-8", errors="replace").split()
            other = int(entries[0]) if entries and entries[0].isdigit() else 0
            parent = int(entries[1]) if len(entries) > 1 and entries[1].isdigit() else 0
            if other and _parent_alive(other) and (parent == 0 or _parent_alive(parent)):
                print(json.dumps({
                    "ready": False,
                    "error": f"another painter is running (pid {other})",
                }), flush=True)
                return None
        lock.write_text(f"{os.getpid()} {os.getppid()}", "utf-8")
        return lock
    except OSError:
        return None


def serve() -> int:
    """Resident mode: load once, then answer jobs on stdin."""
    import torch

    _watch_parent(os.getppid())
    device = _device(torch)
    if device == "cpu" and not os.environ.get("MURMUR_ALLOW_CPU_FILM"):
        print(json.dumps({"ready": False, "error": "no GPU visible to torch"}), flush=True)
        return 2

    lock = _claim_lock()
    if lock is None:
        return 2
    try:
        return _serve_loop(torch, device)
    finally:
        try:
            if lock.exists() and lock.read_text("utf-8", errors="replace").split()[:1] == [str(os.getpid())]:
                lock.unlink()
        except OSError:
            pass


def _serve_loop(torch, device: str) -> int:
    try:
        plan = _plan(torch, device)
    except SystemExit as exc:
        print(json.dumps({"ready": False, "error": str(exc)}), flush=True)
        return 2
    try:
        pipe = _load(torch, device, plan)
    except RuntimeError as exc:
        if "out of memory" not in str(exc).lower():
            raise
        torch.cuda.empty_cache()
        pipe = _load(torch, device, dict(plan, offload=True))
        plan["why"] += "; retried with CPU offload after an OOM"
    print(json.dumps({"ready": True, "model": MODEL_ID, "vram": plan["why"], "precision": plan.get("precision")}), flush=True)

    cache: dict = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": f"bad job: {exc}"}), flush=True)
            continue
        if job.get("op") == "quit":
            break
        try:
            _paint(pipe, torch, device, job, cache)
            print(json.dumps({"id": job.get("id"), "ok": True}), flush=True)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            print(json.dumps({"id": job.get("id"), "ok": False, "error": str(exc)[-500:]}), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="murmur.fluxgen")
    parser.add_argument("--serve", action="store_true", help="stay resident, read jobs on stdin")
    parser.add_argument("--prompt")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--guidance", type=float, default=1.0)
    parser.add_argument("--reference", type=Path, default=None)
    parser.add_argument("--strength", type=float, default=None)
    parser.add_argument("--mask", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.serve:
        return serve()
    if not args.prompt or not args.out:
        parser.error("--prompt and --out are required unless --serve is used")

    generate(
        args.prompt, args.out, args.width, args.height, args.seed,
        steps=args.steps, reference=args.reference, strength=args.strength,
        guidance=args.guidance, mask=args.mask,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
