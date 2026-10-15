from processing.melody import build_melody, clean_voicing, hz_to_midi


def test_hz_to_midi():
    assert hz_to_midi(440.0) == 69.0
    assert round(hz_to_midi(261.63), 1) == 60.0


def test_clean_voicing_removes_short_blips():
    midi = [None, 60.0, None, 62.0, 62.0, 62.0, 62.0, None]
    assert clean_voicing(midi, hop_ms=20, min_voiced_ms=60) == [None, None, None, 62.0, 62.0, 62.0, 62.0, None]


def test_build_melody_gates_quiet_frames():
    f0 = [440.0] * 10
    voiced = [True] * 10
    rms = [0.5] * 5 + [0.001] * 5  # second half is stem bleed, far below the loudest frame
    melody = build_melody(f0, voiced, rms, hop_ms=20)
    assert melody["hop_ms"] == 20
    assert melody["midi"][:5] == [69.0] * 5
    assert melody["midi"][5:] == [None] * 5
