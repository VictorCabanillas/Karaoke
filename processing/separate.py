"""Vocal / instrumental separation with Demucs (htdemucs)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

Report = Callable[[float, str], None]

MODEL_NAME = "htdemucs"


def load_audio(path: Path, sample_rate: int, channels: int):
    """Decode any audio file to a float32 array of shape (channels, samples)."""
    import librosa
    import numpy as np

    wav, _ = librosa.load(str(path), sr=sample_rate, mono=False)
    if wav.ndim == 1:
        wav = wav[None, :]
    if wav.shape[0] < channels:
        wav = np.repeat(wav, channels, axis=0)
    return wav[:channels].astype(np.float32)


class _ProgressTqdm:
    """Stand-in for the `tqdm` module inside demucs.apply, to report chunk progress."""

    def __init__(self, report: Report):
        self._report = report

    def tqdm(self, iterable: Iterable, **_kwargs):
        items = list(iterable)
        total = max(1, len(items))
        for i, item in enumerate(items):
            yield item
            self._report((i + 1) / total, "Separando voz e instrumental")


def separate(audio_path: Path, out_dir: Path, device: str, report: Report) -> tuple[Path, Path]:
    """Write vocals.wav and instrumental.wav (= sum of the other stems) into out_dir."""
    import soundfile as sf
    import torch
    from demucs import apply as demucs_apply
    from demucs.pretrained import get_model

    report(0.0, "Cargando modelo de separación")
    model = get_model(MODEL_NAME)
    model.to(device)
    model.eval()

    report(0.0, "Leyendo audio")
    wav = torch.from_numpy(load_audio(audio_path, model.samplerate, model.audio_channels))
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    wav = (wav - mean) / std

    original_tqdm = demucs_apply.tqdm
    demucs_apply.tqdm = _ProgressTqdm(report)  # type: ignore[assignment]
    try:
        with torch.no_grad():
            sources = demucs_apply.apply_model(
                model, wav[None], device=device, shifts=1, split=True, overlap=0.25, progress=True
            )[0]
    finally:
        demucs_apply.tqdm = original_tqdm
    sources = sources * std + mean

    vocals_idx = model.sources.index("vocals")
    vocals = sources[vocals_idx]
    instrumental = sources.sum(0) - vocals

    out_dir.mkdir(parents=True, exist_ok=True)
    vocals_path = out_dir / "vocals.wav"
    instrumental_path = out_dir / "instrumental.wav"
    sf.write(vocals_path, vocals.T.clamp(-1, 1).cpu().numpy(), model.samplerate, subtype="PCM_16")
    sf.write(instrumental_path, instrumental.T.clamp(-1, 1).cpu().numpy(), model.samplerate, subtype="PCM_16")
    if device == "cuda":
        torch.cuda.empty_cache()
    report(1.0, "Separación terminada")
    return vocals_path, instrumental_path
