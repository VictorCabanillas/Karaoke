"""Pick the best available compute device: CUDA -> Apple MPS -> CPU."""

from __future__ import annotations

import importlib.util
from functools import lru_cache

# Modules needed for the processing pipeline (installed with `uv sync --extra processing`).
REQUIRED_MODULES = ("numpy", "soundfile", "librosa", "torch", "demucs", "faster_whisper")


@lru_cache(maxsize=1)
def detect_device() -> str:
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def processing_available() -> bool:
    return not missing_modules()


def has_torchcrepe() -> bool:
    return importlib.util.find_spec("torchcrepe") is not None
