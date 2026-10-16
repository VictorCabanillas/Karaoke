import random

from processing.lrc import parse_lrc
from processing.lyrics_check import check_timing, shift_lyrics, verify_lyrics
from processing.transcribe import AsrWord

# Invented lyrics: 8 lines, 5 words each, one line every 4 s from 10 s.
WORDS = (
    "la luna come queso azul / el gato baila en tejados / mi abuela canta por la / noche brilla sobre el mar / "
    "corre el viento entre pinos / duermen todas las campanas / vuelve pronto que te espero / suena el tambor muy lejos"
).split(" / ")
LRC = "\n".join(f"[00:{10 + 4 * i:02d}.00]{line}" for i, line in enumerate(WORDS)) + "\n[00:44.00]"


def lyrics():
    return parse_lrc(LRC, duration_s=60)


def sung(shift_ms=0, drift_per_line_ms=0, words=None, jitter=60):
    """What Whisper would hear: the lyric words at their time + shift (+ growing drift)."""
    rng = random.Random(3)
    out = []
    for i, line in enumerate(lyrics().lines):
        for w in line.words:
            start = w.start_ms + shift_ms + i * drift_per_line_ms + rng.randint(-jitter, jitter)
            out.append(AsrWord(w.text, start, start + 250))
    if words is not None:
        out = [AsrWord(t, i * 400, i * 400 + 300) for i, t in enumerate(words)]
    return out


def test_check_timing_measures_a_constant_shift():
    check = check_timing(lyrics(), sung(shift_ms=3500))
    assert check.matched == check.total == sum(len(l.split()) for l in WORDS)
    assert abs(check.offset_ms - 3500) < 100 and check.spread_ms < 100 and abs(check.drift_ms) < 150


def test_lyrics_in_time_are_left_alone():
    result = verify_lyrics(lyrics(), sung(), 60_000)
    assert result.note is None
    assert result.lyrics is not None and result.lyrics.lines[0].start_ms == 10_000
    assert not result.lyrics.lines[0].words[0].estimated  # aligned word by word


def test_early_lyrics_are_shifted():
    result = verify_lyrics(lyrics(), sung(shift_ms=3500), 60_000)
    assert "3.5 s adelantada" in result.note
    assert abs(result.lyrics.lines[0].start_ms - 13_500) < 100
    assert result.lyrics.source == "lrclib_synced"


def test_late_lyrics_are_shifted_back():
    result = verify_lyrics(lyrics(), sung(shift_ms=-2000), 60_000)
    assert "2.0 s retrasada" in result.note
    assert abs(result.lyrics.lines[0].start_ms - 8_000) < 100


def test_drifting_lyrics_are_retimed_from_the_audio():
    result = verify_lyrics(lyrics(), sung(drift_per_line_ms=600), 60_000)
    assert "recalculado" in result.note
    lines = result.lyrics.lines
    assert lines[-1].start_ms > 10_000 + 7 * 4000 + 7 * 600 - 300  # the last line follows the singing
    assert result.lyrics.source == "lrclib_aligned"


def test_wrong_lyrics_are_flagged_and_kept():
    other = "hoy no quiero salir de casa porque llueve mucho en la ciudad gris".split() * 3
    result = verify_lyrics(lyrics(), sung(words=other), 60_000)
    assert result.lyrics is None and result.note.startswith("⚠ La letra no parece coincidir")


def test_nothing_to_compare_with():
    assert verify_lyrics(lyrics(), [], 60_000).note is None


def test_manual_lyrics_are_trusted():
    manual = lyrics().model_copy(update={"source": "manual"})
    result = verify_lyrics(manual, sung(shift_ms=3500), 60_000)
    assert result.note is None


def test_shift_never_goes_negative():
    shifted = shift_lyrics(lyrics(), -20_000)
    assert shifted.lines[0].start_ms == 0 and shifted.lines[-1].start_ms == 38_000 - 20_000
