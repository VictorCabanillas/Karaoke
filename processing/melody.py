"""Reference melody: pitch curve (MIDI) of the separated vocals + mask of where singing happens."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from pathlib import Path

from processing.device import has_torchcrepe

Report = Callable[[float, str], None]

SAMPLE_RATE = 16000
FMIN_HZ = 65.0  # ~C2
FMAX_HZ = 1100.0  # ~C#6
# Frames quieter than this (dB below the loudest frame) count as silence: removes stem bleed.
SILENCE_DB = 35.0
MIN_VOICED_MS = 60


def hz_to_midi(hz: float) -> float:
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def clean_voicing(midi: list[float | None], hop_ms: int, min_voiced_ms: int = MIN_VOICED_MS) -> list[float | None]:
    """Drop voiced runs shorter than min_voiced_ms (spurious blips)."""
    min_frames = max(1, math.ceil(min_voiced_ms / hop_ms))
    out = list(midi)
    i = 0
    while i < len(out):
        if out[i] is None:
            i += 1
            continue
        j = i
        while j < len(out) and out[j] is not None:
            j += 1
        if j - i < min_frames:
            for k in range(i, j):
                out[k] = None
        i = j
    return out


def build_melody(
    f0_hz: Sequence[float], voiced: Sequence[bool], rms: Sequence[float], hop_ms: int
) -> dict:
    """Combine pitch, voicing and loudness into the stored melody format."""
    loud = max((r for r in rms if r > 0), default=0.0)
    threshold = loud * 10 ** (-SILENCE_DB / 20) if loud > 0 else 0.0
    midi: list[float | None] = []
    for f, v, r in zip(f0_hz, voiced, rms):
        if v and f and f > 0 and math.isfinite(f) and r >= threshold:
            midi.append(round(hz_to_midi(float(f)), 2))
        else:
            midi.append(None)
    return {"hop_ms": hop_ms, "midi": clean_voicing(midi, hop_ms)}


def extract_melody(vocals_path: Path, device: str, report: Report) -> dict:
    import librosa
    import numpy as np

    report(0.0, "Analizando la melodía")
    audio, _ = librosa.load(str(vocals_path), sr=SAMPLE_RATE, mono=True)

    if has_torchcrepe():
        import torch
        import torchcrepe

        hop = SAMPLE_RATE // 100  # 10 ms
        tensor = torch.from_numpy(audio)[None]
        pitch, periodicity = torchcrepe.predict(
            tensor,
            SAMPLE_RATE,
            hop_length=hop,
            fmin=FMIN_HZ,
            fmax=FMAX_HZ,
            model="full" if device == "cuda" else "tiny",
            decoder=torchcrepe.decode.viterbi,
            return_periodicity=True,
            batch_size=2048 if device == "cuda" else 512,
            device=device,
        )
        periodicity = torchcrepe.filter.median(periodicity, 3)
        pitch = torchcrepe.filter.mean(pitch, 3)
        f0 = pitch[0].cpu().numpy()
        voiced = (periodicity[0].cpu().numpy() > 0.45).tolist()
    else:
        hop = SAMPLE_RATE // 50  # 20 ms; pyin is slow, keep it coarse
        f0, voiced_flag, _ = librosa.pyin(
            audio, fmin=FMIN_HZ, fmax=FMAX_HZ, sr=SAMPLE_RATE, frame_length=1024, hop_length=hop
        )
        f0 = np.nan_to_num(f0)
        voiced = voiced_flag.tolist()

    rms = librosa.feature.rms(y=audio, frame_length=hop * 4, hop_length=hop, center=True)[0]
    n = min(len(f0), len(voiced), len(rms))
    report(1.0, "Melodía lista")
    return build_melody(f0[:n].tolist(), voiced[:n], rms[:n].tolist(), hop_ms=round(hop * 1000 / SAMPLE_RATE))
