from processing.lrc import parse_lrc, plain_lyrics
from processing.transcribe import AsrWord, align_lyrics, lyrics_from_words, normalize_token


def asr(*items):
    return [AsrWord(text=t, start_ms=s, end_ms=e) for t, s, e in items]


def test_normalize_token():
    assert normalize_token("¡Corazón,") == "corazon"
    assert normalize_token("...") == ""


def test_align_synced_lyrics_sets_exact_word_times():
    lyrics = parse_lrc("[00:01.00]el gato baila\n[00:04.00]la luna mira\n[00:07.00]")
    words = asr(
        ("el", 1100, 1300), ("gato", 1300, 1900), ("baila", 2000, 2900),
        ("la", 4050, 4200), ("lunaa", 4200, 4800), ("mira", 5000, 5600),
    )  # fmt: skip
    aligned = align_lyrics(lyrics, words)
    assert aligned is not None
    assert aligned.source == "lrclib_synced"
    first = aligned.lines[0].words
    assert [(w.start_ms, w.end_ms) for w in first] == [(1100, 1300), (1300, 1900), (2000, 2900)]
    assert not any(w.estimated for line in aligned.lines for w in line.words)  # "lunaa" fuzzy-matches "luna"
    assert aligned.lines[0].start_ms == 1000  # line timing kept


def test_align_interpolates_unmatched_words_inside_the_line():
    lyrics = parse_lrc("[00:01.00]uno dos tres cuatro\n[00:05.00]")
    aligned = align_lyrics(lyrics, asr(("uno", 1000, 1500), ("cuatro", 3500, 4000)))
    assert aligned is not None
    w = aligned.lines[0].words
    assert w[1].estimated and w[2].estimated
    assert w[0].end_ms <= w[1].start_ms <= w[2].start_ms <= w[3].start_ms


def test_align_rejects_far_matches_and_poor_alignment():
    lyrics = parse_lrc("[00:01.00]uno dos tres\n[00:04.00]")
    # Words heard a minute later belong to another part of the song.
    assert align_lyrics(lyrics, asr(("uno", 61000, 61500), ("dos", 61500, 62000), ("tres", 62000, 62500))) is None


def test_align_plain_lyrics_gets_line_timing():
    lyrics = plain_lyrics("hola mundo\nadios sol")
    aligned = align_lyrics(lyrics, asr(("hola", 500, 900), ("mundo", 900, 1500), ("adios", 3000, 3500), ("sol", 3500, 4000)))
    assert aligned is not None
    assert aligned.source == "lrclib_aligned" and aligned.synced
    assert [(ln.start_ms, ln.end_ms) for ln in aligned.lines] == [(500, 1500), (3000, 4000)]


def test_lyrics_from_words_splits_at_pauses():
    lyrics = lyrics_from_words(asr(("a", 0, 200), ("b", 200, 400), ("c", 2000, 2300)))
    assert lyrics.source == "whisper"
    assert [len(ln.words) for ln in lyrics.lines] == [2, 1]


# --- quality control of Whisper output ---------------------------------------

from processing.transcribe import (  # noqa: E402
    fix_durations,
    is_hallucination,
    segment_score,
    merge_candidates,
    plan_clips,
    uncovered_spans,
)


def w(text, start, end, p=0.9, first=False):
    return AsrWord(text=text, start_ms=start, end_ms=end, probability=p, starts_segment=first)


def test_hallucinations_are_detected():
    assert is_hallucination("¡Suscríbete!")
    assert is_hallucination("Gracias por ver el video")
    assert is_hallucination("Subtítulos realizados por la comunidad de Amara.org")
    assert not is_hallucination("la luna come queso azul")


def test_segment_score_rejects_bad_segments():
    good = [w("la", 0, 200), w("luna", 200, 600)]
    assert segment_score(good) == 0.9
    assert segment_score([w("la", 0, 200, p=0.1), w("luna", 200, 600, p=0.2)]) is None
    collapsed = [w("bam", 1000, 1000), w("blom", 1000, 1010), w("run", 1010, 1500)]
    assert segment_score(collapsed) is None
    assert segment_score([w("¡Suscríbete!", 0, 900)]) is None
    assert segment_score([]) is None


def test_fix_durations_spreads_collapsed_words():
    fixed = fix_durations([w("uno", 0, 300), w("dos", 300, 300), w("tres", 300, 300), w("cuatro", 900, 1200)])
    assert [(x.start_ms, x.end_ms) for x in fixed][0] == (0, 300)
    assert fixed[1].end_ms - fixed[1].start_ms >= 100 and fixed[2].end_ms <= 900
    assert fixed[1].end_ms <= fixed[2].start_ms


def test_plan_clips_cuts_at_pauses():
    # 10 s voice, 3 s pause, 40 s voice with a short breath at 38 s
    activity = [True] * 100 + [False] * 30 + [True] * 400
    activity[130 + 250] = False
    clips = plan_clips(activity)
    assert clips[0] == (0, 10_000)
    assert all(b - a <= 28_000 for a, b in clips)
    assert clips[1] == (13_000, 38_000)  # cut at the breath, not at 41 s
    assert clips[-1][1] == 53_000
    assert plan_clips([False] * 50) == []


def test_loops_and_symbol_only_words_are_rejected():
    loop = [w(t, i * 300, i * 300 + 250) for i, t in enumerate(("bam " * 60).split())]
    assert segment_score(loop) is None
    assert segment_score([w("¡¡¡¡¡¡¡¡", 0, 500)]) is None


def test_lines_follow_segments_and_punctuation():
    words = [w("uno", 0, 200, first=True), w("dos", 200, 400), w("tres", 400, 600), w("cuatro,", 600, 800),
             w("cinco", 800, 1000), w("seis", 1000, 1200, first=True), w("siete", 1200, 1400)]  # fmt: skip
    lyrics = lyrics_from_words(words)
    assert [[x.text for x in line.words] for line in lyrics.lines] == [
        ["uno", "dos", "tres", "cuatro,"],
        ["cinco", "seis", "siete"],
    ]


def test_merge_candidates_fills_gaps_from_other_attempts():
    base = [[w("uno", 0, 500, p=0.95), w("dos", 500, 1000, p=0.95)], [w("tres", 1000, 1500, p=0.9)]]
    other = [
        [w("un", 0, 450, p=0.5)],  # overlaps the base: ignored
        [w("cinco", 4000, 4500, p=0.6), w("seis", 4500, 5000, p=0.6)],  # fills a gap
    ]
    merged = merge_candidates([other, base])
    assert [x.text for x in merged] == ["uno", "dos", "tres", "cinco", "seis"]
    assert merge_candidates([]) == []


def test_uncovered_spans_finds_voiced_gaps():
    activity = [False] * 10 + [True] * 100 + [False] * 10  # voice from 1 s to 11 s
    words = [w("hola", 1000, 1500), w("mundo", 1500, 2000)]
    assert uncovered_spans(words, activity) == [(2100, 11000)]
    assert uncovered_spans(words, [False] * 10 + [True] * 25) == []  # under 3 s
    breath = [True] * 20 + [False] * 5 + [True] * 20  # a 0.5 s breath does not split the span
    assert uncovered_spans([], breath) == [(0, 4500)]
