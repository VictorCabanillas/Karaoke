"""Check that synced lyrics match the audio before a song is ready, and fix their timing when possible.

The lyrics text is matched against the words Whisper hears in the vocal stem (by text only). Only the
start of each line is a real timestamp in LRC lyrics (the other words are spread over the line), so the
timing is measured at line starts: each line whose first words were heard gives an "anchor" offset.

- Anchors far from their neighbours are discarded (a repeated chorus matched to the wrong repetition).
- Every line gets the smoothed offset of the anchors around it (lines without one take it from the
  nearest anchors), so a version with a longer intro, an extra bridge or a slow drift is fixed section
  by section; a constant offset is applied as one shift.
- With too few anchors the timing is left alone (better than making it worse), and lyrics whose words
  are mostly not heard are flagged as probably not the right ones.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from processing.transcribe import AsrWord, _match_indices, align_lyrics, normalize_token
from server.models import Line, Lyrics, Word

# Not enough matched words: the lyrics are probably not what is sung.
MIN_MATCHES = 12
MIN_MATCH_RATIO = 0.25
# Line anchors needed to touch the timing at all (absolute and as a share of the lines).
MIN_ANCHORS = 4
MIN_ANCHOR_RATIO = 0.2
# Offsets this small are left alone (line-start measurements are good to ~0.1-0.2 s).
OFFSET_TOLERANCE_MS = 200
# Smoothing: median over this many anchors around each line; anchors further than this from the
# smoothed value are outliers.
SMOOTH_ANCHORS = 5
OUTLIER_MS = 1500
# If the smoothed offsets vary less than this, it is one constant shift.
CONSTANT_RANGE_MS = 500
MIN_LINE_GAP_MS = 100


@dataclass
class TimingCheck:
    matched: int  # lyric words heard in the vocals
    total: int
    anchors: dict[int, int]  # line index -> offset (ms) measured at its start, outliers removed
    line_offsets: list[int]  # smoothed offset for every line

    @property
    def ratio(self) -> float:
        return self.matched / self.total if self.total else 0.0

    @property
    def offset_ms(self) -> int:
        return round(statistics.median(self.line_offsets)) if self.line_offsets else 0

    @property
    def range_ms(self) -> int:
        return max(self.line_offsets) - min(self.line_offsets) if self.line_offsets else 0


@dataclass
class CheckResult:
    lyrics: Lyrics | None  # lyrics to save, or None to keep the stored ones
    note: str | None  # shown to the user in the library and while singing


def _median_window(values: list[int], index: int, size: int) -> float:
    half = size // 2
    return statistics.median(values[max(0, index - half) : index + half + 1])


def check_timing(lyrics: Lyrics, asr: list[AsrWord]) -> TimingCheck | None:
    flat = [(li, wi, w) for li, line in enumerate(lyrics.lines) for wi, w in enumerate(line.words)]
    if not flat or not asr:
        return None
    mapping = _match_indices([normalize_token(w.text) for _, _, w in flat], [normalize_token(a.text) for a in asr])

    # Anchor = first or second word of the line heard: offset of the line start.
    raw: dict[int, int] = {}
    for i, (li, wi, word) in enumerate(flat):
        if wi < 2 and li not in raw and i in mapping:
            heard = asr[mapping[i]].start_ms
            # The second word's own time is an estimate: measure from the line start instead.
            raw[li] = heard - lyrics.lines[li].start_ms - (word.start_ms - lyrics.lines[li].start_ms if wi else 0)

    lines = sorted(raw)
    values = [raw[li] for li in lines]
    anchors = {
        li: off for k, (li, off) in enumerate(zip(lines, values)) if abs(off - _median_window(values, k, SMOOTH_ANCHORS)) <= OUTLIER_MS
    }
    if not anchors:
        return TimingCheck(len(mapping), len(flat), {}, [])

    kept = sorted(anchors)
    kept_values = [anchors[li] for li in kept]
    smoothed = {li: round(_median_window(kept_values, k, SMOOTH_ANCHORS)) for k, li in enumerate(kept)}
    # Lines without an anchor: interpolate between the nearest anchored lines (constant at the ends).
    offsets: list[int] = []
    for li in range(len(lyrics.lines)):
        before = [a for a in kept if a <= li]
        after = [a for a in kept if a >= li]
        if before and after:
            a, b = before[-1], after[0]
            if a == b:
                offsets.append(smoothed[a])
            else:
                t = (li - a) / (b - a)
                offsets.append(round(smoothed[a] + (smoothed[b] - smoothed[a]) * t))
        else:
            offsets.append(smoothed[(before or after)[-1 if before else 0]])
    return TimingCheck(len(mapping), len(flat), anchors, offsets)


def shift_lyrics(lyrics: Lyrics, ms: int) -> Lyrics:
    return shift_lines(lyrics, [ms] * len(lyrics.lines))


def shift_lines(lyrics: Lyrics, offsets: list[int]) -> Lyrics:
    """Move every line (and its words) by its own offset, keeping the lines in order."""
    lines: list[Line] = []
    for line, ms in zip(lyrics.lines, offsets):
        start = max(0, line.start_ms + ms)
        if lines and start < lines[-1].start_ms + MIN_LINE_GAP_MS:
            start = lines[-1].start_ms + MIN_LINE_GAP_MS
        delta = start - line.start_ms
        words = [
            Word(text=w.text, start_ms=max(0, w.start_ms + delta), end_ms=max(0, w.end_ms + delta), estimated=w.estimated)
            for w in line.words
        ]
        lines.append(Line(start_ms=start, end_ms=max(start + 1, line.end_ms + delta), words=words))
    # A line never runs into the next one.
    for current, nxt in zip(lines, lines[1:]):
        if current.end_ms > nxt.start_ms:
            current.end_ms = max(current.start_ms + 1, nxt.start_ms)
            for w in current.words:
                w.end_ms = min(w.end_ms, current.end_ms)
                w.start_ms = min(w.start_ms, w.end_ms)
    return Lyrics(source=lyrics.source, synced=lyrics.synced, lines=lines)


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
    enough_anchors = len(check.anchors) >= max(MIN_ANCHORS, MIN_ANCHOR_RATIO * len(lyrics.lines))
    # Few words heard is normal in some languages or busy mixes: only flag the lyrics when, besides,
    # hardly any line start lines up with the singing.
    if check.matched < MIN_MATCHES or (check.ratio < MIN_MATCH_RATIO and not enough_anchors):
        return CheckResult(
            None,
            "⚠ La letra no parece coincidir con lo que se canta: revísala con «Cambiar letra» o «Editar letra»",
        )
    if not enough_anchors:
        # The words are there but too few line starts were heard to trust any timing change.
        return CheckResult(align_lyrics(lyrics, asr, duration_ms), None)

    note = None
    if check.range_ms <= CONSTANT_RANGE_MS:
        if abs(check.offset_ms) > OFFSET_TOLERANCE_MS:
            lyrics = shift_lyrics(lyrics, check.offset_ms)
            direction = "adelantada" if check.offset_ms > 0 else "retrasada"
            note = f"La letra iba {abs(check.offset_ms) / 1000:.1f} s {direction}: corregida automáticamente"
    else:
        lyrics = shift_lines(lyrics, check.line_offsets)
        biggest = max(check.line_offsets, key=abs)
        note = f"Los tiempos de la letra se han ajustado por partes a esta versión (hasta {abs(biggest) / 1000:.1f} s)"
    aligned = align_lyrics(lyrics, asr, duration_ms)
    if aligned is not None:
        return CheckResult(aligned, note)
    return CheckResult(lyrics if note else None, note)

