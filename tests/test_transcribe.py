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
