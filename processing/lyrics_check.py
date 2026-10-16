"""Check that synced lyrics match the audio before a song is ready, and fix their timing when possible.

The lyrics text is matched against the words Whisper hears in the vocal stem (by text only, ignoring the
LRC times). The time difference of the matched words tells whether the lyrics are shifted by a constant
amount (another edit of the song, a longer intro...), drift or jump around (another version), or simply
are not what is being sung.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from processing.transcribe import AsrWord, _match_indices, align_lyrics, normalize_token
from server.models import Line, Lyrics, Word

# Not enough matched words to judge: the lyrics are probably not what is sung.
MIN_MATCHES = 12
MIN_MATCH_RATIO = 0.25
# Shifts smaller than this are left alone (the per-word alignment absorbs them).
OFFSET_TOLERANCE_MS = 250
# Above these, the timing is not a constant shift: re-time the lines from the audio.
MAX_SPREAD_MS = 700
MAX_DRIFT_MS = 1500


@dataclass
class TimingCheck:
    matched: int
    total: int
    offset_ms: int  # positive: the lyrics come before the singing ("adelantada")
    spread_ms: int  # median absolute deviation of the offsets
    drift_ms: int  # offset at the end minus offset at the start

    @property
    def ratio(self) -> float:
        return self.matched / self.total if self.total else 0.0


@dataclass
class CheckResult:
    lyrics: Lyrics | None  # lyrics to save, or None to keep the stored ones
    note: str | None  # shown to the user in the library and while singing


def check_timing(lyrics: Lyrics, asr: list[AsrWord]) -> TimingCheck | None:
    words = [w for line in lyrics.lines for w in line.words]
    if not words or not asr:
        return None
    mapping = _match_indices([normalize_token(w.text) for w in words], [normalize_token(a.text) for a in asr])
    pairs = sorted((words[i].start_ms, asr[j].start_ms - words[i].start_ms) for i, j in mapping.items())
    if len(pairs) < 3:
        return TimingCheck(len(pairs), len(words), 0, 0, 0)
    deltas = [d for _, d in pairs]
    offset = statistics.median(deltas)
    spread = statistics.median(abs(d - offset) for d in deltas)
    third = max(1, len(pairs) // 3)
    drift = statistics.median(deltas[-third:]) - statistics.median(deltas[:third])
    return TimingCheck(len(pairs), len(words), round(offset), round(spread), round(drift))


def shift_lyrics(lyrics: Lyrics, ms: int) -> Lyrics:
    def at(t: int) -> int:
        return max(0, t + ms)

    return Lyrics(
        source=lyrics.source,
        synced=lyrics.synced,
        lines=[
            Line(
                start_ms=at(line.start_ms),
                end_ms=at(line.end_ms),
                words=[Word(text=w.text, start_ms=at(w.start_ms), end_ms=at(w.end_ms), estimated=w.estimated) for w in line.words],
            )
            for line in lyrics.lines
        ],
    )


def as_plain(lyrics: Lyrics) -> Lyrics:
    """Same text, no timing: to be re-timed from the audio."""
    return Lyrics(
        source="lrclib_lines_only",
        synced=False,
        lines=[
            Line(start_ms=0, end_ms=0, words=[Word(text=w.text, start_ms=0, end_ms=0) for w in line.words])
            for line in lyrics.lines
        ],
    )


def verify_lyrics(lyrics: Lyrics, asr: list[AsrWord], duration_ms: int | None) -> CheckResult:
    """Align the lyrics with the audio, checking (and fixing) their timing first."""
    # Lyrics without timing get it from the audio; lyrics synced by hand are trusted.
    if not lyrics.synced or lyrics.source == "manual":
        aligned = align_lyrics(lyrics, asr, duration_ms)
        if aligned is None and not lyrics.synced:
            return CheckResult(None, "⚠ No se pudo sincronizar la letra con la voz: sincronízala con «Editar letra»")
        return CheckResult(aligned, None)

    check = check_timing(lyrics, asr)
    if check is None:
        return CheckResult(None, None)  # nothing intelligible to compare with
    if check.matched < MIN_MATCHES or check.ratio < MIN_MATCH_RATIO:
        return CheckResult(
            None,
            "⚠ La letra no parece coincidir con lo que se canta: revísala con «Cambiar letra» o «Editar letra»",
        )
    if check.spread_ms > MAX_SPREAD_MS or abs(check.drift_ms) > MAX_DRIFT_MS:
        retimed = align_lyrics(as_plain(lyrics), asr, duration_ms)
        if retimed is not None:
            return CheckResult(
                retimed, "Los tiempos de la letra no encajaban con esta versión: se han recalculado a partir de la voz"
            )
        return CheckResult(
            None, "⚠ Los tiempos de la letra no encajan con esta versión de la canción: corrígelos con «Editar letra»"
        )

    note = None
    if abs(check.offset_ms) > OFFSET_TOLERANCE_MS:
        lyrics = shift_lyrics(lyrics, check.offset_ms)
        direction = "adelantada" if check.offset_ms > 0 else "retrasada"
        note = f"La letra iba {abs(check.offset_ms) / 1000:.1f} s {direction}: corregida automáticamente"
    aligned = align_lyrics(lyrics, asr, duration_ms)
    if aligned is not None:
        return CheckResult(aligned, note)
    return CheckResult(lyrics if note else None, note)
