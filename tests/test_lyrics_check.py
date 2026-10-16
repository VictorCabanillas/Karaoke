import random

from processing.lrc import parse_lrc
from processing.lyrics_check import check_timing, shift_lines, shift_lyrics, verify_lyrics
from processing.transcribe import AsrWord

# Invented lyrics: 12 lines, one every 4 s from 10 s (every word appears once, so matching is unambiguous).
TEXT = [
    "la luna come queso azul",
    "el gato baila en tejados",
    "mi abuela canta por radio",
    "noche brilla sobre el mar",
    "corre viento entre pinos verdes",
    "duermen todas las campanas",
    "vuelve pronto que te espero",
    "suena tambor muy lejos ya",
    "pajaros cruzan cielo gris",
    "nadie sabe donde iremos",
    "trenes viejos cantan sin parar",
    "fin del baile hasta mañana",
]
LRC = "\n".join(f"[00:{10 + 4 * i:02d}.00]{line}" for i, line in enumerate(TEXT)) + "\n[00:58.00]"


def lyrics():
    return parse_lrc(LRC, duration_s=90)


def sung(shift=lambda i: 0, fill=1.0, jitter=60, only_lines=None):
    """What Whisper hears: each line sung from its start + shift(i), over `fill` of the line's length."""
    rng = random.Random(3)
    out = []
    for i, line in enumerate(lyrics().lines):
        if only_lines is not None and i not in only_lines:
            continue
        span = (line.end_ms - line.start_ms) * fill
        for w in line.words:
            rel = (w.start_ms - line.start_ms) / (line.end_ms - line.start_ms)
            start = round(line.start_ms + shift(i) + rel * span + rng.randint(-jitter, jitter))
            out.append(AsrWord(w.text, start, start + 200))
    return out


def test_constant_shift_is_measured_at_line_starts():
    check = check_timing(lyrics(), sung(shift=lambda i: 3500))
    assert len(check.anchors) == 12
    assert abs(check.offset_ms - 3500) < 100 and check.range_ms < 200


def test_lyrics_in_time_are_left_alone():
    result = verify_lyrics(lyrics(), sung(), 90_000)
    assert result.note is None
    assert result.lyrics is not None and result.lyrics.lines[0].start_ms == 10_000
    assert not result.lyrics.lines[0].words[0].estimated  # aligned word by word


def test_singers_finishing_lines_early_is_not_an_offset():
    # The bug behind "0.3 s retrasada": estimated word times are late when lines are sung quickly.
    result = verify_lyrics(lyrics(), sung(fill=0.6), 90_000)
    assert result.note is None
    assert result.lyrics.lines[3].start_ms == lyrics().lines[3].start_ms


def test_early_lyrics_are_shifted():
    result = verify_lyrics(lyrics(), sung(shift=lambda i: 3500), 90_000)
    assert "3.5 s adelantada" in result.note
    assert abs(result.lyrics.lines[0].start_ms - 13_500) < 150
    assert result.lyrics.source == "lrclib_synced"


def test_late_lyrics_are_shifted_back():
    result = verify_lyrics(lyrics(), sung(shift=lambda i: -2000), 90_000)
    assert "2.0 s retrasada" in result.note
    assert abs(result.lyrics.lines[0].start_ms - 8_000) < 150


def test_a_longer_bridge_is_fixed_section_by_section():
    # This version has 8 s more music before line 6: the first half is right, the second is 8 s late.
    result = verify_lyrics(lyrics(), sung(shift=lambda i: 0 if i < 6 else 8000), 90_000)
    assert "por partes" in result.note
    starts = [line.start_ms for line in result.lyrics.lines]
    assert abs(starts[2] - 18_000) < 300 and abs(starts[9] - (46_000 + 8000)) < 300
    assert starts == sorted(starts)


def test_slow_drift_is_followed():
    result = verify_lyrics(lyrics(), sung(shift=lambda i: 400 * i), 90_000)
    assert "por partes" in result.note
    assert abs(result.lyrics.lines[-1].start_ms - (54_000 + 4400)) < 500


def test_a_chorus_matched_to_the_wrong_repetition_is_ignored():
    asr = sung()
    # One line also "heard" a minute later (the same chorus sung again): an outlier anchor.
    stray = [w for w in sung(shift=lambda i: 60_000, only_lines={5})]
    asr = sorted([w for w in asr if w.text not in {x.text for x in stray}] + stray, key=lambda w: w.start_ms)
    check = check_timing(lyrics(), asr)
    assert 5 not in check.anchors
    assert verify_lyrics(lyrics(), asr, 90_000).note is None


def test_too_few_line_starts_heard_leaves_timing_alone():
    asr = sung(shift=lambda i: 5000, only_lines={0, 1, 2})  # only three lines heard
    result = verify_lyrics(lyrics(), asr, 90_000)
    assert result.note is None or result.note.startswith("⚠")


def test_wrong_lyrics_are_flagged_and_kept():
    other = [AsrWord(t, i * 400, i * 400 + 300) for i, t in enumerate("hoy no quiero salir de casa porque llueve".split() * 4)]
    result = verify_lyrics(lyrics(), other, 90_000)
    assert result.lyrics is None and result.note.startswith("⚠ La letra no parece coincidir")


def test_nothing_to_compare_with():
    assert verify_lyrics(lyrics(), [], 90_000).note is None


def test_manual_lyrics_are_trusted():
    manual = lyrics().model_copy(update={"source": "manual"})
    assert verify_lyrics(manual, sung(shift=lambda i: 3500), 90_000).note is None


def test_shifts_keep_lines_in_order_and_non_negative():
    shifted = shift_lyrics(lyrics(), -20_000)
    assert shifted.lines[0].start_ms == 0
    squeezed = shift_lines(lyrics(), [0, -6000] + [0] * 10)  # line 1 would jump before line 0
    starts = [line.start_ms for line in squeezed.lines]
    assert starts == sorted(starts) and starts[1] >= starts[0] + 100
    assert all(a.end_ms <= b.start_ms for a, b in zip(squeezed.lines, squeezed.lines[1:]))


def test_few_words_heard_but_line_starts_match_is_not_flagged():
    # Busy mix / another language: Whisper hears only the first two words of each line.
    asr = [w for w in sung(shift=lambda i: 1000) if any(w.text == line.split()[k] for line in TEXT for k in (0, 1))]
    check = check_timing(lyrics(), asr)
    assert check.ratio < 0.5 and len(check.anchors) == 12
    result = verify_lyrics(lyrics(), asr, 90_000)
    assert result.note and not result.note.startswith("⚠") and "1.0 s adelantada" in result.note
