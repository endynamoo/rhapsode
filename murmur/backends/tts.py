"""ONNX Runtime provider selection for Kokoro.

kokoro-onnx honours `ONNX_PROVIDER` when the matching onnxruntime build is
installed. Set `MURMUR_TTS_PROVIDER=CUDAExecutionProvider` (or the DirectML /
CoreML / ROCm equivalent) to pin a provider; otherwise the best available
accelerated build is used, and plain CPU as the final fallback.
"""
from __future__ import annotations

import importlib.metadata
import os

ACCELERATED_DISTRIBUTIONS = (
    "onnxruntime-gpu",
    "onnxruntime-directml",
    "onnxruntime-openvino",
    "onnxruntime-rocm",
    "onnxruntime-qnn",
)

ACCELERATED_PROVIDERS = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "ROCMExecutionProvider",
    "OpenVINOExecutionProvider",
    "CoreMLExecutionProvider",
    "QNNExecutionProvider",
)


def available_providers() -> list[str]:
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception:
        return []


def accelerated_installed() -> bool:
    for distribution in ACCELERATED_DISTRIBUTIONS:
        try:
            importlib.metadata.distribution(distribution)
            return True
        except importlib.metadata.PackageNotFoundError:
            continue
    return False


def active_provider() -> str:
    """The provider kokoro-onnx will use for new sessions."""
    pinned = os.environ.get("ONNX_PROVIDER", "").strip()
    if pinned:
        return pinned
    if not accelerated_installed():
        return "CPUExecutionProvider"
    providers = available_providers()
    for name in ACCELERATED_PROVIDERS:
        if name in providers:
            return name
    return "CPUExecutionProvider"


def configure() -> str:
    """Apply MURMUR_TTS_PROVIDER before the Kokoro session is created."""
    requested = os.environ.get("MURMUR_TTS_PROVIDER", "").strip()
    if requested:
        providers = available_providers()
        if not providers or requested in providers:
            os.environ["ONNX_PROVIDER"] = requested
    return active_provider()
