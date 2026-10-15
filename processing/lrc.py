"""LRC parsing and per-word timing interpolation."""

from __future__ import annotations

import re

from server.models import Line, Lyrics, LyricsSource, Word

_TIME_TAG = re.compile(r"\[(\d+):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_META_TAG = re.compile(r"^\[([a-zA-Z#]+):(.*)\]\s*$")
_WORD_TAG = re.compile(r"<(\d+):(\d{1,2}(?:[.:]\d{1,3})?)>")
_VOWEL_GROUPS = re.compile(r"[aeiouyáéíóúàèìòùâêîôûäëïöüåæøœ]+", re.IGNORECASE)

# Lines whose next timestamp is very far away (instrumental break) are capped to this pace.
_MS_PER_SYLLABLE_CAP = 600
_LINE_TAIL_MS = 1500
_LAST_LINE_MS_PER_SYLLABLE = 350


def _to_ms(minutes: str, seconds: str) -> int:
    return round((int(minutes) * 60 + float(seconds.replace(":", "."))) * 1000)


def count_syllables(word: str) -> int:
    """Rough syllable count (vowel groups); good enough to spread a line's duration."""
    return max(1, len(_VOWEL_GROUPS.findall(word)))


def interpolate_words(text: str, start_ms: int, end_ms: int) -> list[Word]:
    """Split a line into words and share its duration according to syllable counts."""
    tokens = text.split()
    if not tokens:
        return []
    weights = [count_syllables(t) for t in tokens]
    total = sum(weights)
    span = max(0, end_ms - start_ms)
    words: list[Word] = []
    acc = 0
    for token, weight in zip(tokens, weights):
        w_start = start_ms + round(span * acc / total)
        acc += weight
        w_end = start_ms + round(span * acc / total)
        words.append(Word(text=token, start_ms=w_start, end_ms=w_end, estimated=True))
    return words


def _parse_enhanced_words(text: str, start_ms: int, end_ms: int) -> list[Word] | None:
    """Words of an enhanced-LRC line (<mm:ss.xx>word ...), or None if it has no word tags."""
    parts = _WORD_TAG.split(text)
    if len(parts) < 4:
        return None
    pending: list[tuple[int, str]] = []
    if parts[0].strip():
        pending.append((start_ms, parts[0]))
    for i in range(1, len(parts), 3):
        pending.append((_to_ms(parts[i], parts[i + 1]), parts[i + 2]))
    words: list[Word] = []
    for idx, (t, chunk) in enumerate(pending):
        nxt = pending[idx + 1][0] if idx + 1 < len(pending) else end_ms
        tokens = chunk.split()
        if not tokens:
            continue
        sub = interpolate_words(chunk, t, max(t, nxt))
        for w in sub:
            w.estimated = len(tokens) > 1
        words.extend(sub)
    return words


def parse_lrc(text: str, duration_s: float | None = None, source: LyricsSource = "lrclib_synced") -> Lyrics:
    """Parse synced lyrics in LRC format into lines with (interpolated) word timings."""
    offset_ms = 0
    entries: list[tuple[int, str]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        times = list(_TIME_TAG.finditer(raw))
        if not times:
            meta = _META_TAG.match(raw)
            if meta and meta.group(1).lower() == "offset":
                try:
                    offset_ms = int(meta.group(2).strip())
                except ValueError:
                    pass
            continue
        # Text is whatever follows the last leading time tag.
        content = raw[times[-1].end():].strip()
        for m in times:
            entries.append((_to_ms(m.group(1), m.group(2)), content))

    # A positive [offset:] means lyrics should be shown earlier.
    entries = sorted((max(0, t - offset_ms), c) for t, c in entries)
    duration_ms = round(duration_s * 1000) if duration_s else None

    lines: list[Line] = []
    for idx, (start, content) in enumerate(entries):
        plain = _WORD_TAG.sub("", content).strip()
        if not plain:
            continue  # empty lines only mark the end of the previous one
        syllables = sum(count_syllables(t) for t in plain.split())
        if idx + 1 < len(entries):
            end = min(entries[idx + 1][0], start + syllables * _MS_PER_SYLLABLE_CAP + _LINE_TAIL_MS)
        else:
            end = start + syllables * _LAST_LINE_MS_PER_SYLLABLE + _LINE_TAIL_MS
            if duration_ms:
                end = min(end, max(start, duration_ms))
        end = max(end, start + 1)
        words = _parse_enhanced_words(content, start, end) or interpolate_words(plain, start, end)
        lines.append(Line(start_ms=start, end_ms=end, words=words))
    return Lyrics(source=source, synced=True, lines=lines)


def plain_lyrics(text: str) -> Lyrics:
    """Unsynced lyrics: one line per text line, without timing."""
    lines = [
        Line(start_ms=0, end_ms=0, words=[Word(text=t, start_ms=0, end_ms=0) for t in raw.split()])
        for raw in text.splitlines()
        if raw.strip()
    ]
    return Lyrics(source="lrclib_lines_only", synced=False, lines=lines)


def lyrics_text_lines(lyrics: Lyrics) -> list[str]:
    return [" ".join(w.text for w in line.words) for line in lyrics.lines]
