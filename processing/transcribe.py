"""Whisper (faster-whisper): word timestamps, alignment of known lyrics and fallback transcription."""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
import zlib
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
# Transcribed lines break at pauses longer than this, at Whisper segment starts or punctuation once a
# line has a few words, and never grow past MAX_WORDS_PER_LINE.
LINE_BREAK_GAP_MS = 700
MIN_WORDS_BEFORE_SOFT_BREAK = 4
MAX_WORDS_PER_LINE = 12
PROMPT_CHARS = 600

# Quality control of Whisper output.
MIN_SEGMENT_PROBABILITY = 0.4
MAX_COLLAPSED_SHARE = 0.5  # words with (almost) no duration: Whisper lost the timing
COLLAPSED_MS = 40
MIN_WORD_MS = 120
# The song is transcribed in clips cut at pauses of the vocal stem (one Whisper window each).
CLIP_MAX_MS = 28_000
CLIP_SEARCH_MS = 8_000  # look this far back from the max length for a pause to cut at
CLIP_JOIN_GAP_MS = 2_000  # pauses shorter than this stay inside a clip
CLIP_PAD_MS = 500
ACTIVITY_HOP_MS = 100
ACTIVITY_RANGE_DB = 30.0
# Text that compresses this well is a decoding loop. Higher than Whisper's own 2.4: songs repeat a lot.
MAX_COMPRESSION_RATIO = 4.0
# After the first round, voiced stretches still without words for this long get a clip of their own.
RETRY_MIN_GAP_MS = 3_000
# Segments from other attempts fill a clip's gaps if they overlap accepted words by less than this.
MERGE_OVERLAP_MS = 300

# Phrases Whisper invents on music or unintelligible audio (learned from YouTube subtitles).
_HALLUCINATIONS = re.compile(
    r"suscr[ií]b|gracias por ver|subt[ií]tul|amara\.org|thanks? (you )?for watching|please subscribe"
    r"|like and subscribe|dale like|nos vemos en el pr[oó]ximo",
    re.IGNORECASE,
)


@dataclass
class AsrWord:
    text: str
    start_ms: int
    end_ms: int
    probability: float = 1.0
    starts_segment: bool = False


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
    """Group transcribed words into lines following the phrasing (pauses, segments, punctuation)."""
    lines: list[Line] = []
    current: list[Word] = []
    previous: AsrWord | None = None
    for a in asr:
        text = a.text.strip()
        if not text:
            continue
        if current:
            soft = len(current) >= MIN_WORDS_BEFORE_SOFT_BREAK and (
                a.starts_segment or (previous is not None and previous.text.rstrip()[-1:] in ",.;:?!")
            )
            if a.start_ms - current[-1].end_ms > LINE_BREAK_GAP_MS or len(current) >= MAX_WORDS_PER_LINE or soft:
                lines.append(Line(start_ms=current[0].start_ms, end_ms=current[-1].end_ms, words=current))
                current = []
        current.append(Word(text=text, start_ms=a.start_ms, end_ms=max(a.start_ms, a.end_ms), estimated=False))
        previous = a
    if current:
        lines.append(Line(start_ms=current[0].start_ms, end_ms=current[-1].end_ms, words=current))
    return Lyrics(source=source, synced=True, lines=lines)


# --- quality control of the transcription (pure) ------------------------------


def is_hallucination(text: str) -> bool:
    return bool(_HALLUCINATIONS.search(text))


def has_letters(text: str) -> bool:
    return any(c.isalpha() for c in text)


def compression_ratio(text: str) -> float:
    data = text.encode("utf-8")
    return len(data) / max(1, len(zlib.compress(data)))


def segment_score(words: list[AsrWord]) -> float | None:
    """Mean word probability of a Whisper segment, or None if it should be thrown away."""
    words = [w for w in words if has_letters(w.text)]
    if not words:
        return None
    text = " ".join(w.text for w in words)
    if is_hallucination(text) or compression_ratio(text) > MAX_COMPRESSION_RATIO:
        return None
    collapsed = sum(1 for w in words if w.end_ms - w.start_ms < COLLAPSED_MS)
    if collapsed / len(words) > MAX_COLLAPSED_SHARE:
        return None
    score = sum(w.probability for w in words) / len(words)
    return score if score >= MIN_SEGMENT_PROBABILITY else None


def fix_durations(words: list[AsrWord]) -> list[AsrWord]:
    """Give collapsed words (no duration) a share of the time up to the next word, by syllables."""
    out = [AsrWord(w.text, w.start_ms, max(w.start_ms, w.end_ms), w.probability, w.starts_segment) for w in words]
    i = 0
    while i < len(out):
        if out[i].end_ms - out[i].start_ms >= COLLAPSED_MS:
            i += 1
            continue
        j = i
        while j < len(out) and out[j].end_ms - out[j].start_ms < COLLAPSED_MS:
            j += 1
        start = out[i].start_ms
        wanted = start + MIN_WORD_MS * (j - i)
        end = min(wanted, out[j].start_ms) if j < len(out) else wanted
        for k, (s, e) in zip(range(i, j), _spread([w.text for w in out[i:j]], start, end)):
            out[k].start_ms, out[k].end_ms = s, e
        i = j
    return out


def vocal_activity(audio, sample_rate: int) -> list[bool]:
    """Per 100 ms: is the vocal stem loud enough to contain singing?"""
    import numpy as np

    hop = sample_rate * ACTIVITY_HOP_MS // 1000
    frames = len(audio) // hop
    if frames == 0:
        return []
    chunks = np.asarray(audio[: frames * hop], dtype=np.float32).reshape(frames, hop)
    db = 20 * np.log10(np.sqrt((chunks**2).mean(axis=1)) + 1e-9)
    return (db > db.max() - ACTIVITY_RANGE_DB).tolist()


def merge_candidates(candidates: list[list[list[AsrWord]]]) -> list[AsrWord]:
    """Combine several transcriptions (lists of segments) of the same clip.

    The candidate with the highest total word probability is the base; segments of the others fill
    the stretches it left empty.
    """

    def total(candidate: list[list[AsrWord]]) -> float:
        return sum(w.probability for segment in candidate for w in segment)

    ranked = sorted(candidates, key=total, reverse=True)
    if not ranked:
        return []
    chosen = [w for segment in ranked[0] for w in segment]
    for candidate in ranked[1:]:
        for segment in candidate:
            start, end = segment[0].start_ms, segment[-1].end_ms
            overlap = sum(max(0, min(end, w.end_ms) - max(start, w.start_ms)) for w in chosen)
            if overlap < MERGE_OVERLAP_MS and not any(start <= w.start_ms < end for w in chosen):
                chosen.extend(segment)
    return sorted(chosen, key=lambda w: w.start_ms)


def uncovered_spans(words: list[AsrWord], activity: list[bool]) -> list[tuple[int, int]]:
    """Voiced spans (ms) of at least RETRY_MIN_GAP_MS without any word (breaths under 1 s don't split)."""
    hop = ACTIVITY_HOP_MS
    covered = [False] * len(activity)
    for w in words:
        for i in range(w.start_ms // hop, min(len(covered), w.end_ms // hop + 1)):
            covered[i] = True
    spans: list[tuple[int, int]] = []
    start: int | None = None
    last = 0
    for i in range(len(activity) + 1):
        if i < len(activity) and covered[i]:
            gap_ends = True
        else:
            gap_ends = i == len(activity)
            if i < len(activity) and activity[i]:
                if start is None:
                    start = i
                last = i
                continue
            gap_ends = gap_ends or (start is not None and i - last > 10)
        if gap_ends and start is not None:
            if (last + 1 - start) * hop >= RETRY_MIN_GAP_MS:
                spans.append((start * hop, (last + 1) * hop))
            start = None
    return spans


def plan_clips(activity: list[bool]) -> list[tuple[int, int]]:
    """Split the voiced parts of the song into clips (ms) of at most CLIP_MAX_MS, cutting at pauses."""
    hop = ACTIVITY_HOP_MS
    # Voiced regions, joining pauses shorter than CLIP_JOIN_GAP_MS.
    regions: list[list[int]] = []
    for i, active in enumerate(activity):
        if not active:
            continue
        if regions and i - regions[-1][1] <= CLIP_JOIN_GAP_MS // hop:
            regions[-1][1] = i + 1
        else:
            regions.append([i, i + 1])
    clips: list[tuple[int, int]] = []
    max_len, search = CLIP_MAX_MS // hop, CLIP_SEARCH_MS // hop
    for a, b in regions:
        while b - a > max_len:
            window = range(a + max_len - 1, a + max_len - search, -1)
            cut = next((i for i in window if not activity[i]), a + max_len)
            clips.append((a * hop, cut * hop))
            a = cut
        clips.append((a * hop, b * hop))
    return [(x, y) for x, y in clips if y - x >= 2 * hop]


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
            # ctranslate2 finds cuBLAS/cuDNN through the DLLs PyTorch ships: load torch first.
            import torch  # noqa: F401

            _models[key] = WhisperModel(size, device="cuda", compute_type="float16")
        else:
            # faster-whisper has no MPS backend: Apple machines run it on CPU.
            _models[key] = WhisperModel(size, device="cpu", compute_type="int8")
    return _models[key]


_WS = re.compile(r"\s+")
SAMPLE_RATE = 16000


def _segments(
    model, audio, language: str | None, prompt: str | None, strict: bool, greedy: bool = False
) -> list[list[AsrWord]]:
    """Run Whisper; strict=False drops its no-speech/low-confidence filters (used on retried clips).

    greedy=True decodes at temperature 0 only: deterministic, no random fallback.
    """
    options: dict = dict(
        language=language,
        word_timestamps=True,
        condition_on_previous_text=False,
        initial_prompt=prompt,
        vad_filter=strict,
    )
    if not strict:
        options.update(no_speech_threshold=None, log_prob_threshold=None)
    if greedy:
        options.update(temperature=0.0)
    out: list[list[AsrWord]] = []
    segments, _ = model.transcribe(audio, **options)
    for segment in segments:
        words = []
        for w in segment.words or []:
            text = _WS.sub(" ", w.word).strip()
            if text:
                words.append(
                    AsrWord(
                        text=text,
                        start_ms=round(w.start * 1000),
                        end_ms=round(w.end * 1000),
                        probability=float(getattr(w, "probability", 1.0)),
                        starts_segment=not words,
                    )
                )
        if words:
            out.append(words)
    return out


def _language(model, audio) -> str | None:
    """Detect the language on several voiced fragments (the first 30 s are often an intro)."""
    try:
        language, probability, _ = model.detect_language(audio, vad_filter=True, language_detection_segments=4)
    except (TypeError, AttributeError):  # older faster-whisper
        return None
    return language if probability >= 0.5 else None


def transcribe_words(
    vocals_path: Path,
    device: str,
    report: Report,
    model_size: str = "",
    prompt: str | None = None,
    mix_path: Path | None = None,
) -> list[AsrWord]:
    """Transcribe the song clip by clip, keeping the best of several attempts for each clip.

    Clips are cut at pauses of the vocal stem. Each one is transcribed from the stem and from the
    original mix (separation sometimes makes processed vocals unintelligible), deterministically and
    with Whisper's random temperature fallback (GPU only). Segments that fail the quality checks are
    dropped; a clip where nothing passes stays without words: lyrics are never invented.
    """
    import librosa

    size = model_size or default_model_size(device)
    report(0.0, f"Cargando Whisper ({size})")
    try:
        model = _load_model(size, device)
    except Exception as exc:  # CUDA libraries missing, out of memory...
        if device == "cpu":
            raise
        log.warning("Whisper on %s failed (%s); falling back to CPU", device, exc)
        device = "cpu"
        size = model_size or default_model_size("cpu")
        model = _load_model(size, "cpu")

    # Decode ourselves (librosa) instead of letting faster-whisper use PyAV: fewer version clashes.
    vocals, _ = librosa.load(str(vocals_path), sr=SAMPLE_RATE, mono=True)
    mix = librosa.load(str(mix_path), sr=SAMPLE_RATE, mono=True)[0] if mix_path else None
    language = _language(model, vocals)
    clips = plan_clips(vocal_activity(vocals, SAMPLE_RATE))
    greedy_modes = (True, False) if device == "cuda" else (True,)
    attempts = [(src, greedy) for src in (vocals, mix) if src is not None for greedy in greedy_modes]

    def transcribe_clip(a: int, b: int) -> list[AsrWord]:
        lo, hi = max(0, a - CLIP_PAD_MS), b + CLIP_PAD_MS
        candidates: list[list[list[AsrWord]]] = []
        for source, greedy in attempts:
            clip = source[lo * SAMPLE_RATE // 1000 : hi * SAMPLE_RATE // 1000]
            candidate: list[list[AsrWord]] = []
            for segment in _segments(model, clip, language, prompt, strict=False, greedy=greedy):
                kept = [
                    AsrWord(w.text, w.start_ms + lo, w.end_ms + lo, w.probability, w.starts_segment)
                    for w in segment
                    if has_letters(w.text) and a - CLIP_PAD_MS <= w.start_ms + lo < b + CLIP_PAD_MS
                ]
                if kept and segment_score(kept) is not None:
                    kept[0].starts_segment = True
                    candidate.append(kept)
            candidates.append(candidate)
        return merge_candidates(candidates)

    words: list[AsrWord] = []
    for done, (a, b) in enumerate(clips):
        report(0.8 * done / max(1, len(clips)), "Transcribiendo la voz")
        words.extend(transcribe_clip(a, b))

    # Whisper behaves differently depending on where a clip starts: give each stretch that is still
    # empty a clip of its own, and keep what it finds there.
    activity = vocal_activity(vocals, SAMPLE_RATE)
    retries = [(t, min(b, t + CLIP_MAX_MS)) for a, b in uncovered_spans(words, activity) for t in range(a, b, CLIP_MAX_MS)]
    for done, (a, b) in enumerate(retries):
        report(0.8 + 0.2 * done / len(retries), "Revisando partes sin letra")
        found = [w for w in transcribe_clip(a, b) if a - CLIP_PAD_MS <= w.start_ms < b]
        words.extend(found)

    words.sort(key=lambda w: w.start_ms)
    report(1.0, "Transcripción lista")
    return fix_durations(words)
