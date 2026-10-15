import random

from processing.lrc import parse_lrc
from server.scoring import (
    MAX_SCORE,
    PlayerScorer,
    PlayerStats,
    Reference,
    awards,
    octave_distance,
    pitch_marks,
)

HOP = 10


def melody(seconds: float = 20.0):
    """Reference: phrases of 2 s singing / 1 s silence, notes changing every 500 ms."""
    midi = []
    notes = [60, 62, 64, 65, 67, 65, 64, 62]
    for i in range(int(seconds * 1000 / HOP)):
        t = i * HOP
        midi.append(None if (t % 3000) >= 2000 else float(notes[(t // 500) % len(notes)]))
    return midi


def ref_at(midi, t):
    i = int(t // HOP)
    return midi[i] if 0 <= i < len(midi) else None


def simulate(midi, singer, frame_ms=21, seconds=20.0):
    scorer = PlayerScorer(Reference.from_melody(HOP, midi))
    t = 0.0
    rng = random.Random(42)
    while t < seconds * 1000:
        scorer.add(t, singer(t, ref_at(midi, t), rng))
        t += frame_ms
        if int(t) % 1000 < frame_ms:
            scorer.finalize(t - 1500)
    scorer.finalize_all()
    return scorer


def good(t, ref, rng):
    return None if ref is None else ref + rng.uniform(-0.3, 0.3)


def octave_down(t, ref, rng):
    return None if ref is None else ref - 12 + rng.uniform(-0.3, 0.3)


def bad(t, ref, rng):
    return None if ref is None else ref + rng.choice([-5, -4, 4, 5]) + rng.uniform(-0.5, 0.5)


def silent(t, ref, rng):
    return None


def shouting(t, ref, rng):
    return 70.0 + rng.uniform(-6, 6)


def test_helpers():
    assert octave_distance(60, 72) == 0
    assert octave_distance(60, 71) == 1
    assert octave_distance(60, 66) == 6
    assert pitch_marks(0.5) == 1 and pitch_marks(2) == 0.5 and pitch_marks(4) == 0


def test_singing_well_beats_singing_badly_beats_silence():
    perfect = simulate(melody(), good).score
    out_of_tune = simulate(melody(), bad).score
    quiet = simulate(melody(), silent).score
    assert perfect > out_of_tune > quiet
    assert perfect > 0.9 * MAX_SCORE
    assert quiet == 0


def test_octave_is_ignored():
    assert abs(simulate(melody(), octave_down).score - simulate(melody(), good).score) < 0.03 * MAX_SCORE


def test_shouting_all_the_time_is_not_rewarded_like_singing():
    scorer = simulate(melody(), shouting)
    assert scorer.score < simulate(melody(), good).score * 0.6
    assert scorer.stats.silence_violations > 0


def test_late_frames_are_ignored_after_finalize():
    scorer = PlayerScorer(Reference.from_melody(HOP, melody()))
    scorer.finalize(5000)
    scorer.add(1000, 60.0)
    assert scorer.pending == {}


def test_lyrics_only_reference_scores_presence():
    lyrics = parse_lrc("[00:01.00]uno dos tres cuatro\n[00:03.00]\n[00:05.00]cinco seis\n[00:07.00]")
    ref = Reference.from_lyrics(lyrics, offset_ms=0, duration_ms=8000)
    assert not ref.has_pitch
    assert ref.expected[15] and not ref.expected[40]

    def run(singer):
        s = PlayerScorer(ref)
        for t in range(0, 8000, 20):
            s.add(t, singer(t))
        s.finalize_all()
        return s.score

    in_lines = run(lambda t: 62.0 if ref.expected[t // 100] else None)
    nothing = run(lambda t: None)
    assert in_lines > 0.95 * MAX_SCORE
    assert nothing == 0


def test_awards():
    a = PlayerStats(pitch_marks_sum=90, pitch_windows=100, best_combo=50, sung_windows=90, expected_windows=100)
    b = PlayerStats(pitch_marks_sum=40, pitch_windows=100, best_combo=80, sung_windows=95, expected_windows=100,
                    silence_violations=30)  # fmt: skip
    titles = {aw.title: aw.player_id for aw in awards({"a": a, "b": b})}
    assert titles == {"Más afinado": "a", "Más constante": "b", "Pulmones de acero": "b", "No puede parar": "b"}
