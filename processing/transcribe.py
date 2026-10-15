"""Whisper (faster-whisper): word timestamps, alignment of known lyrics and fallback transcription."""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from processing.lrc import count_syllables
from server.models import Line, Lyrics, LyricsSource, Word

log = logging.getLogger(__name__)
Report = Callable[[float, str], None]

# Whisper words further than this from their LRC line are considered wrong matches.
LINE_SLACK_MS = 1500
# Below this share of matched words the alignment is not trusted.
MIN_MATCH_RATIO = 0.3
# Transcribed lines are split at pauses longer than this, or when they get too long.
LINE_BREAK_GAP_MS = 700
MAX_WORDS_PER_LINE = 10
PROMPT_CHARS = 600


@dataclass
class AsrWord:
    text: str
    start_ms: int
    end_ms: int


# --- pure alignment logic (no Whisper needed) ------------------------------------


def normalize_token(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if c.isalnum())


def _match_indices(lyric_tokens: list[str], asr_tokens: list[str]) -> dict[int, int]:
    """Monotonic mapping lyric word index -> ASR word index (exact + fuzzy matches)."""
    matcher = difflib.SequenceMatcher(None, lyric_tokens, asr_tokens, autojunk=False)
    mapping: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[i1 + k] = j1 + k
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            # Same number of words: accept the ones that look alike (misheard spelling).
            for k in range(i2 - i1):
                a, b = lyric_tokens[i1 + k], asr_tokens[j1 + k]
                if a and b and difflib.SequenceMatcher(None, a, b).ratio() >= 0.6:
                    mapping[i1 + k] = j1 + k
    return {i: j for i, j in mapping.items() if lyric_tokens[i]}


def _spread(texts: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Share [start, end] between words by syllable count."""
    weights = [count_syllables(t) for t in texts]
    total = sum(weights) or 1
    span = max(0, end - start)
    out, acc = [], 0
    for w in weights:
        s = start + round(span * acc / total)
        acc += w
        out.append((s, start + round(span * acc / total)))
    return out


def _fill_gaps(words: list[Word], anchored: list[bool], lo: int, hi: int) -> None:
    """Interpolate the non-anchored words between their anchored neighbours (in place)."""
    i = 0
    n = len(words)
    while i < n:
        if anchored[i]:
            i += 1
            continue
        j = i
        while j < n and not anchored[j]:
            j += 1
        start = words[i - 1].end_ms if i > 0 else lo
        end = words[j].start_ms if j < n else hi
        if end < start:
            end = start
        for k, (s, e) in zip(range(i, j), _spread([w.text for w in words[i:j]], start, end)):
            words[k].start_ms, words[k].end_ms, words[k].estimated = s, e, True
        i = j


def align_lyrics(lyrics: Lyrics, asr: list[AsrWord], duration_ms: int | None = None) -> Lyrics | None:
    """Give each lyric word the timing of the matching transcribed word.

    Synced lyrics keep their line timing (words are clamped into the line); plain lyrics get
    line timing from the words. Returns None if too few words could be matched.
    """
    flat = [(li, wi, w) for li, line in enumerate(lyrics.lines) for wi, w in enumerate(line.words)]
    if not flat or not asr:
        return None
    lyric_tokens = [normalize_token(w.text) for _, _, w in flat]
    asr_tokens = [normalize_token(w.text) for w in asr]
    mapping = _match_indices(lyric_tokens, asr_tokens)

    if lyrics.synced:
        # Discard matches that land far away from their LRC line (repeated choruses...).
        for i, j in list(mapping.items()):
            line = lyrics.lines[flat[i][0]]
            if asr[j].start_ms < line.start_ms - LINE_SLACK_MS or asr[j].end_ms > line.end_ms + LINE_SLACK_MS:
                del mapping[i]

    if len(mapping) < MIN_MATCH_RATIO * len(flat):
        return None

    new_words: list[Word] = []
    anchored: list[bool] = []
    for i, (_, _, w) in enumerate(flat):
        if i in mapping:
            a = asr[mapping[i]]
            new_words.append(Word(text=w.text, start_ms=a.start_ms, end_ms=max(a.start_ms, a.end_ms), estimated=False))
            anchored.append(True)
        else:
            new_words.append(Word(text=w.text, start_ms=0, end_ms=0, estimated=True))
            anchored.append(False)

    lines: list[Line] = []
    if lyrics.synced:
        pos = 0
        for line in lyrics.lines:
            words = new_words[pos : pos + len(line.words)]
            marks = anchored[pos : pos + len(line.words)]
            pos += len(line.words)
            for w, m in zip(words, marks):
                if m:
                    w.start_ms = min(max(w.start_ms, line.start_ms), line.end_ms)
                    w.end_ms = min(max(w.end_ms, w.start_ms), line.end_ms)
            _fill_gaps(words, marks, line.start_ms, line.end_ms)
            lines.append(Line(start_ms=line.start_ms, end_ms=line.end_ms, words=words))
        return Lyrics(source=lyrics.source, synced=True, lines=lines)

    end_bound = duration_ms or max(a.end_ms for a in asr)
    _fill_gaps(new_words, anchored, 0, end_bound)
    pos = 0
    for line in lyrics.lines:
        words = new_words[pos : pos + len(line.words)]
        pos += len(line.words)
        if words:
            lines.append(Line(start_ms=words[0].start_ms, end_ms=words[-1].end_ms, words=words))
    return Lyrics(source="lrclib_aligned", synced=True, lines=lines)


def lyrics_from_words(asr: list[AsrWord], source: LyricsSource = "whisper") -> Lyrics:
    """Group transcribed words into lines at pauses."""
    lines: list[Line] = []
    current: list[Word] = []
    for a in asr:
        text = a.text.strip()
        if not text:
            continue
        if current and (a.start_ms - current[-1].end_ms > LINE_BREAK_GAP_MS or len(current) >= MAX_WORDS_PER_LINE):
            lines.append(Line(start_ms=current[0].start_ms, end_ms=current[-1].end_ms, words=current))
            current = []
        current.append(Word(text=text, start_ms=a.start_ms, end_ms=max(a.start_ms, a.end_ms), estimated=False))
    if current:
        lines.append(Line(start_ms=current[0].start_ms, end_ms=current[-1].end_ms, words=current))
    return Lyrics(source=source, synced=True, lines=lines)


def lyrics_prompt(lyrics: Lyrics | None) -> str | None:
    if lyrics is None:
        return None
    text = " ".join(w.text for line in lyrics.lines for w in line.words)
    return text[:PROMPT_CHARS] or None


# --- Whisper ----------------------------------------------------------------


def default_model_size(device: str) -> str:
    return "medium" if device == "cuda" else "small"


_models: dict[tuple[str, str], object] = {}


def _load_model(size: str, device: str):
    from faster_whisper import WhisperModel

    key = (size, device)
    if key not in _models:
        if device == "cuda":
            _models[key] = WhisperModel(size, device="cuda", compute_type="float16")
        else:
            # faster-whisper has no MPS backend: Apple machines run it on CPU.
            _models[key] = WhisperModel(size, device="cpu", compute_type="int8")
    return _models[key]


_WS = re.compile(r"\s+")


def transcribe_words(
    vocals_path: Path, device: str, report: Report, model_size: str = "", prompt: str | None = None
) -> list[AsrWord]:
    size = model_size or default_model_size(device)
    report(0.0, f"Cargando Whisper ({size})")
    try:
        model = _load_model(size, device)
    except Exception as exc:  # CUDA libraries missing, out of memory...
        if device == "cpu":
            raise
        log.warning("Whisper on %s failed (%s); falling back to CPU", device, exc)
        size = model_size or default_model_size("cpu")
        model = _load_model(size, "cpu")

    # Decode ourselves (librosa) instead of letting faster-whisper use PyAV: fewer version clashes.
    import librosa

    audio, _ = librosa.load(str(vocals_path), sr=16000, mono=True)
    segments, info = model.transcribe(  # type: ignore[attr-defined]
        audio,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=prompt,
    )
    duration = max(1.0, float(getattr(info, "duration", 0.0) or 1.0))
    words: list[AsrWord] = []
    for segment in segments:
        for w in segment.words or []:
            text = _WS.sub(" ", w.word).strip()
            if text:
                words.append(AsrWord(text=text, start_ms=round(w.start * 1000), end_ms=round(w.end * 1000)))
        report(min(1.0, segment.end / duration), "Transcribiendo la voz")
    return words
