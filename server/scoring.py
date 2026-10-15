"""Simultaneous scoring: every player is scored separately against the same reference.

The song is cut in 100 ms windows. With a reference melody (processed files) each window scores
pitch (octave-independent) and presence; without it (Spotify, unprocessed files) only presence
and a little pitch stability. Singing during long silences costs a few points.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from server.models import Lyrics

WINDOW_MS = 100
MAX_SCORE = 10_000
# Even the original vocal track is not detected in every sung window (breaths, consonants): singing
# this share of the windows perfectly already earns the maximum score.
COVERAGE_TOLERANCE = 0.9
# Weights of a window's score.
PITCH_WEIGHT = 0.7
PRESENCE_WEIGHT = 0.3
SIMPLE_PRESENCE_WEIGHT = 0.85
SIMPLE_STABILITY_WEIGHT = 0.15
# Pitch tolerance (semitones, octave ignored): full marks up to 1, nothing from 3.
FULL_MARKS_SEMITONES = 1.0
ZERO_MARKS_SEMITONES = 3.0
# Share of voiced frames in a window that counts as fully "present".
PRESENCE_FULL = 0.5
# Penalty (as a share of a window's points) for singing where the song is silent.
SILENCE_PENALTY = 0.1
# Windows this close to a sung window never get penalties (breaths, timing slack).
NEUTRAL_WINDOWS = 3
# A window with at least this score keeps the combo going.
COMBO_THRESHOLD = 0.6
# Share of reference frames in a window that must be voiced for it to count as "sung".
REF_VOICED_SHARE = 0.4


def octave_distance(a: float, b: float) -> float:
    """Distance in semitones ignoring the octave (0..6)."""
    d = abs(a - b) % 12
    return min(d, 12 - d)


def pitch_marks(distance: float) -> float:
    if distance <= FULL_MARKS_SEMITONES:
        return 1.0
    if distance >= ZERO_MARKS_SEMITONES:
        return 0.0
    return 1.0 - (distance - FULL_MARKS_SEMITONES) / (ZERO_MARKS_SEMITONES - FULL_MARKS_SEMITONES)


@dataclass
class Reference:
    """What should be sung in each window."""

    expected: list[bool]
    target: list[float | None]  # median reference MIDI, None without melody
    has_pitch: bool
    neutral: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        n = len(self.expected)
        near = [False] * n
        for i, e in enumerate(self.expected):
            if e:
                for k in range(max(0, i - NEUTRAL_WINDOWS), min(n, i + NEUTRAL_WINDOWS + 1)):
                    near[k] = True
        self.neutral = near

    @property
    def expected_count(self) -> int:
        return sum(self.expected)

    @classmethod
    def from_melody(cls, hop_ms: int, midi: list[float | None]) -> Reference:
        per_window = max(1, round(WINDOW_MS / hop_ms))
        expected: list[bool] = []
        target: list[float | None] = []
        for start in range(0, len(midi), per_window):
            chunk = midi[start : start + per_window]
            voiced = [m for m in chunk if m is not None]
            sung = len(voiced) >= REF_VOICED_SHARE * len(chunk)
            expected.append(sung)
            target.append(statistics.median(voiced) if sung else None)
        return cls(expected=expected, target=target, has_pitch=True)

    @classmethod
    def from_lyrics(cls, lyrics: Lyrics, offset_ms: int, duration_ms: int) -> Reference:
        n = max(1, math.ceil(duration_ms / WINDOW_MS))
        expected = [False] * n
        for line in lyrics.lines if lyrics.synced else []:
            for word in line.words:
                first = max(0, (word.start_ms + offset_ms) // WINDOW_MS)
                last = min(n - 1, (word.end_ms + offset_ms) // WINDOW_MS)
                for i in range(first, last + 1):
                    expected[i] = True
        return cls(expected=expected, target=[None] * n, has_pitch=False)


@dataclass
class PlayerStats:
    score: float = 0.0
    combo: int = 0
    best_combo: int = 0
    pitch_marks_sum: float = 0.0
    pitch_windows: int = 0
    sung_windows: int = 0
    expected_windows: int = 0
    silence_violations: int = 0

    @property
    def accuracy(self) -> float:
        """Average pitch marks over the windows where the player sang (0..1)."""
        return self.pitch_marks_sum / self.pitch_windows if self.pitch_windows else 0.0

    @property
    def presence(self) -> float:
        return self.sung_windows / self.expected_windows if self.expected_windows else 0.0


class PlayerScorer:
    def __init__(self, reference: Reference):
        self.ref = reference
        self.points_per_window = MAX_SCORE / max(1.0, reference.expected_count * COVERAGE_TOLERANCE)
        self.pending: dict[int, list[float | None]] = {}
        self.finalized_upto = 0  # windows below this index are scored
        self.stats = PlayerStats()

    def add(self, song_ms: float, midi: float | None) -> None:
        """One analysed frame: song time and MIDI pitch (None = not singing / unclear)."""
        if song_ms < 0:
            return
        index = int(song_ms // WINDOW_MS)
        if index < self.finalized_upto or index >= len(self.ref.expected):
            return
        self.pending.setdefault(index, []).append(midi)

    def finalize(self, until_ms: float) -> None:
        """Score every window that ends before until_ms."""
        upto = min(len(self.ref.expected), int(until_ms // WINDOW_MS))
        for index in range(self.finalized_upto, upto):
            self._score_window(index, self.pending.pop(index, []))
        self.finalized_upto = max(self.finalized_upto, upto)

    def finalize_all(self) -> None:
        self.finalize(len(self.ref.expected) * WINDOW_MS)

    @property
    def score(self) -> int:
        return round(min(MAX_SCORE, max(0.0, self.stats.score)))

    def _closest_target(self, index: int, sung: float) -> float | None:
        """Reference pitch around this window that is closest to what was sung (±1 window of slack)."""
        candidates = [
            self.ref.target[i]
            for i in (index - 1, index, index + 1)
            if 0 <= i < len(self.ref.target) and self.ref.target[i] is not None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda t: octave_distance(sung, t))  # type: ignore[arg-type]

    def _score_window(self, index: int, frames: list[float | None]) -> None:
        stats = self.stats
        voiced = [m for m in frames if m is not None]
        voiced_share = len(voiced) / len(frames) if frames else 0.0

        if not self.ref.expected[index]:
            if voiced_share > 0.5 and not self.ref.neutral[index]:
                stats.silence_violations += 1
                stats.score -= SILENCE_PENALTY * self.points_per_window
                stats.combo = 0
            return

        stats.expected_windows += 1
        presence = min(1.0, voiced_share / PRESENCE_FULL)
        if presence > 0:
            stats.sung_windows += 1

        if self.ref.has_pitch:
            marks = 0.0
            if voiced:
                sung = statistics.median(voiced)
                target = self._closest_target(index, sung)
                marks = pitch_marks(octave_distance(sung, target)) if target is not None else 0.0
                stats.pitch_marks_sum += marks
                stats.pitch_windows += 1
            value = PITCH_WEIGHT * marks * presence + PRESENCE_WEIGHT * presence
        else:
            stability = 0.0
            if len(voiced) >= 2:
                spread = statistics.pstdev(voiced)
                stability = max(0.0, min(1.0, 1.0 - (spread - 0.5) / 2.5))
            elif voiced:
                stability = 1.0
            value = SIMPLE_PRESENCE_WEIGHT * presence + SIMPLE_STABILITY_WEIGHT * stability * presence

        stats.score += value * self.points_per_window
        if value >= COMBO_THRESHOLD:
            stats.combo += 1
            stats.best_combo = max(stats.best_combo, stats.combo)
        else:
            stats.combo = 0


@dataclass
class Award:
    title: str
    player_id: str
    detail: str


def awards(players: dict[str, PlayerStats]) -> list[Award]:
    """Fun end-of-song prizes."""
    out: list[Award] = []
    if not players:
        return out
    tuned = {pid: s for pid, s in players.items() if s.pitch_windows >= 10}
    if tuned:
        pid, s = max(tuned.items(), key=lambda kv: kv[1].accuracy)
        out.append(Award("Más afinado", pid, f"{round(s.accuracy * 100)}% de notas en su sitio"))
    pid, s = max(players.items(), key=lambda kv: kv[1].best_combo)
    if s.best_combo > 0:
        out.append(Award("Más constante", pid, f"racha de {s.best_combo / 10:.1f} s seguidos"))
    pid, s = max(players.items(), key=lambda kv: kv[1].presence)
    if s.presence > 0:
        out.append(Award("Pulmones de acero", pid, f"cantó el {round(s.presence * 100)}% de la canción"))
    pid, s = max(players.items(), key=lambda kv: kv[1].silence_violations)
    if s.silence_violations >= 10:
        out.append(Award("No puede parar", pid, "siguió cantando en los silencios"))
    return out
