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


def _decide(torch, device: str, requested_offload: bool) -> tuple[bool, str]:
    """Pick full residency or CPU offload, and refuse when neither fits.

    Full residency of klein-4B is ~15.5 GiB of a 16 GB card, which leaves nothing
    for the desktop or a remote-desktop encoder — that is how this machine locks
    up. Offload keeps one component resident (~9 GiB) and streams the rest, which
    costs time but not the session.
    """
    if device != "cuda":
        return requested_offload, "non-cuda device"
    free, total = _vram(torch)
    reserve = _reserve_mib(torch)
    full = float(os.environ.get("MURMUR_FLUX_FULL_VRAM_MIB", "15500"))
    offload_need = float(os.environ.get("MURMUR_FLUX_OFFLOAD_MIB", "9000"))
    if free <= 0:
        return requested_offload, "VRAM unknown"
    if requested_offload:
        return True, "offload requested"
    if free >= full + reserve:
        return False, f"{free:.0f} MiB free: full residency with {reserve:.0f} MiB spare"
    if free >= offload_need + reserve:
        return True, (
            f"only {free:.0f} MiB free ({total:.0f} total, {reserve:.0f} reserved): "
            "CPU offload, so the desktop keeps its memory"
        )
    raise SystemExit(
        f"refusing to load: {free:.0f} MiB of {total:.0f} MiB is free and "
        f"{reserve:.0f} MiB is reserved for the desktop and the remote session. "
        "Close the other GPU job (or lower MURMUR_FLUX_RESERVE_MB knowingly)."
    )


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

    offload, why = _decide(torch, device, os.environ.get("MURMUR_FLUX_OFFLOAD") == "1")
    print(f"fluxgen: {why}", file=sys.stderr, flush=True)
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
        offload, why = _decide(torch, device, os.environ.get("MURMUR_FLUX_OFFLOAD") == "1")
    except SystemExit as exc:
        print(json.dumps({"ready": False, "error": str(exc)}), flush=True)
        return 2
    try:
        pipe = _load(torch, device, offload)
    except RuntimeError as exc:
        if "out of memory" not in str(exc).lower():
            raise
        torch.cuda.empty_cache()
        pipe = _load(torch, device, True)
        why += "; retried with CPU offload after an OOM"
    print(json.dumps({"ready": True, "model": MODEL_ID, "vram": why}), flush=True)

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
