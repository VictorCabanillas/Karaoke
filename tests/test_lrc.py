from processing.lrc import count_syllables, interpolate_words, parse_lrc, plain_lyrics
from tests.conftest import FAKE_LRC


def test_count_syllables():
    assert count_syllables("luna") == 2
    assert count_syllables("balcón") == 2
    assert count_syllables("y") == 1
    assert count_syllables("...") == 1


def test_interpolate_words_spreads_by_syllables():
    words = interpolate_words("la mariposa", 0, 1000)
    assert [w.text for w in words] == ["la", "mariposa"]
    assert words[0].start_ms == 0 and words[-1].end_ms == 1000
    # "la" = 1 syllable, "mariposa" = 4 -> 200 ms / 800 ms
    assert words[0].end_ms == 200
    assert all(w.estimated for w in words)


def test_parse_lrc_lines_and_gaps():
    lyrics = parse_lrc(FAKE_LRC, duration_s=20)
    assert lyrics.source == "lrclib_synced"
    assert [ln.start_ms for ln in lyrics.lines] == [1000, 4500, 12000]
    assert lyrics.lines[0].end_ms == 4500
    # The empty line at 8.00 ends the second line.
    assert lyrics.lines[1].end_ms == 8000
    assert lyrics.lines[2].end_ms <= 20000
    for line in lyrics.lines:
        assert line.words[0].start_ms == line.start_ms
        assert line.words[-1].end_ms == line.end_ms


def test_parse_lrc_caps_long_instrumental_breaks():
    lyrics = parse_lrc("[00:01.00]hola mundo\n[01:00.00]adios", duration_s=70)
    assert lyrics.lines[0].end_ms < 10000


def test_parse_lrc_offset_and_repeated_tags():
    lyrics = parse_lrc("[offset:500]\n[00:02.00][00:10.00]otra vez\n")
    assert [ln.start_ms for ln in lyrics.lines] == [1500, 9500]


def test_parse_enhanced_lrc_words():
    lyrics = parse_lrc("[00:01.00]<00:01.00>uno <00:01.50>dos <00:02.20>tres\n[00:03.00]fin")
    words = lyrics.lines[0].words
    assert [w.start_ms for w in words] == [1000, 1500, 2200]
    assert not any(w.estimated for w in words)


def test_plain_lyrics():
    lyrics = plain_lyrics("uno dos\n\ntres")
    assert not lyrics.synced
    assert len(lyrics.lines) == 2
