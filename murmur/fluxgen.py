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


def _load(torch, device: str, offload: bool):
    from diffusers import Flux2KleinPipeline

    dtype = torch.bfloat16 if device != "cpu" else torch.float32
    pipe = Flux2KleinPipeline.from_pretrained(MODEL_ID, torch_dtype=dtype)
    if offload:
        # Text encoder and transformer take turns on the card; slower, but it
        # fits where a full residency does not.
        pipe.enable_model_cpu_offload()
    else:
        pipe = pipe.to(device)
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
) -> None:
    """One-shot paint: load, paint, exit."""
    import torch

    device = _device(torch)
    if device == "cpu" and not os.environ.get("MURMUR_ALLOW_CPU_FILM"):
        raise SystemExit("no GPU is visible to torch; refusing to paint on the CPU")

    offload = os.environ.get("MURMUR_FLUX_OFFLOAD") == "1"
    try:
        pipe = _load(torch, device, offload)
    except RuntimeError as exc:
        if "out of memory" not in str(exc).lower():
            raise
        torch.cuda.empty_cache()
        pipe = _load(torch, device, True)

    _paint(
        pipe, torch, device,
        {
            "prompt": prompt, "out": str(out_path), "width": width, "height": height,
            "seed": seed, "steps": steps, "guidance": guidance,
            "reference": str(reference) if reference else None, "strength": strength,
        },
    )


def serve() -> int:
    """Resident mode: load once, then answer jobs on stdin."""
    import torch

    device = _device(torch)
    if device == "cpu" and not os.environ.get("MURMUR_ALLOW_CPU_FILM"):
        print(json.dumps({"ready": False, "error": "no GPU visible to torch"}), flush=True)
        return 2

    offload = os.environ.get("MURMUR_FLUX_OFFLOAD") == "1"
    try:
        pipe = _load(torch, device, offload)
    except RuntimeError as exc:
        if "out of memory" not in str(exc).lower():
            raise
        torch.cuda.empty_cache()
        pipe = _load(torch, device, True)
    print(json.dumps({"ready": True, "model": MODEL_ID}), flush=True)

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
    args = parser.parse_args(argv)

    if args.serve:
        return serve()
    if not args.prompt or not args.out:
        parser.error("--prompt and --out are required unless --serve is used")

    generate(
        args.prompt, args.out, args.width, args.height, args.seed,
        steps=args.steps, reference=args.reference, strength=args.strength,
        guidance=args.guidance,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
