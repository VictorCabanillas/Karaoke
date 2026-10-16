"""Pydantic models: the single source of truth for data shapes shared with the browser.

TypeScript types are generated from these models (see scripts/gen_types.py).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union, get_args

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    # Fields with defaults are always present in responses: make them required in the generated TS.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class InputModel(Model):
    """Sent by the browser: fields with defaults stay optional in the generated TS."""


# --- Lyrics -----------------------------------------------------------------

# Type aliases that also get a named TypeScript type.
TS_ALIASES = (
    "GameMode",
    "WhisperModelName",
    "LyricsSource",
    "SongStatus",
    "SongOrigin",
    "MicToServer",
    "ServerToMic",
    "HostToServer",
    "ServerToHost",
)

LyricsSource = Literal["lrclib_synced", "lrclib_aligned", "whisper", "lrclib_lines_only", "manual"]


class Word(Model):
    text: str
    start_ms: int
    end_ms: int
    estimated: bool = True  # True when interpolated instead of aligned to the audio


class Line(Model):
    start_ms: int
    end_ms: int
    words: list[Word]


class Lyrics(Model):
    source: LyricsSource
    # False only for "lrclib_lines_only": plain text without any timing.
    synced: bool = True
    lines: list[Line]


class LrclibCandidate(Model):
    id: int
    track_name: str
    artist_name: str
    album_name: str | None = None
    duration: float | None = None
    instrumental: bool = False
    has_synced: bool
    has_plain: bool


# --- Songs ------------------------------------------------------------------

SongStatus = Literal["downloading", "imported", "lyrics_ready", "processing", "processed", "error"]
SongOrigin = Literal["file", "spotify"]


class Song(Model):
    id: str
    origin: SongOrigin = "file"
    title: str
    artist: str = ""
    album: str = ""
    duration_s: float = 0.0
    status: SongStatus = "imported"
    error: str | None = None
    progress: float = 0.0  # 0..1 while processing
    progress_label: str = ""
    offset_ms: int = 0  # manual lyrics offset; positive = lyrics appear later
    lyrics_source: LyricsSource | None = None
    instrumental: bool = False  # LRCLIB says the track has no vocals
    has_stems: bool = False
    has_melody: bool = False
    spotify_uri: str | None = None
    created_at: float = 0.0
    has_audio: bool = True  # False while downloading or if the download failed
    # Result of checking the lyrics against the vocals when processing (shown to the user); "⚠..." = problem.
    lyrics_note: str | None = None


class SongPatch(InputModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    offset_ms: int | None = Field(default=None, ge=-2000, le=2000)


class LyricsText(InputModel):
    """Lyrics pasted as plain text (one line per line)."""

    text: str = Field(min_length=1, max_length=50_000)
    # Align the text with the vocals (needs the processing extras and an audio file).
    align: bool = True


class ChooseLyrics(InputModel):
    lrclib_id: int


class Melody(Model):
    """Reference pitch of the vocals: one MIDI value (or null = not singing) every hop_ms."""

    hop_ms: int
    midi: list[float | None]


class SpotifyStatus(Model):
    configured: bool
    connected: bool
    redirect_uri: str


class SpotifyToken(Model):
    access_token: str
    expires_at: float


class SpotifyTrack(Model):
    id: str
    uri: str
    name: str
    artists: str
    album: str
    duration_ms: int
    image_url: str | None


class AddSpotifyTrack(InputModel):
    track_id: str = Field(pattern=r"^[A-Za-z0-9]{10,40}$")


class OnlineTrack(Model):
    """A search result that can be downloaded."""

    id: str
    title: str
    channel: str
    duration_s: float | None
    thumbnail: str | None


class DownloadRequest(InputModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    title: str = ""
    channel: str = ""


class SystemInfo(Model):
    device: str
    processing_available: bool
    missing_modules: list[str]
    ffmpeg: bool
    https: bool
    spotify_configured: bool
    downloads_available: bool = False


# --- Players ----------------------------------------------------------------


class Player(Model):
    id: str
    name: str
    color: str
    connected: bool = True
    calibrated: bool = False
    latency_ms: int = 100


class PitchFrame(InputModel):
    """One analysis frame from a phone. `t` is already converted to the server clock (ms)."""

    t: float
    f0: float | None
    conf: float
    rms: float


class LivePitch(Model):
    server_ms: float
    song_ms: float | None
    midi: float | None
    rms: float


class PlayerPitch(Model):
    player_id: str
    frames: list[LivePitch]


# --- Competition --------------------------------------------------------------

GameMode = Literal["free", "competition"]


class ScoreEntry(Model):
    player_id: str
    name: str
    color: str
    score: int
    combo: int
    presence: float = 0.0  # share of the sung parts where the player's voice was detected (0..1)
    accuracy: float | None = None  # pitch marks where they sang (0..1); None without reference melody


class AwardEntry(Model):
    title: str
    player_id: str
    name: str
    detail: str


# --- WebSocket messages: shared ------------------------------------------------


class ClockPing(InputModel):
    type: Literal["ping"] = "ping"
    t0: float


class ClockPong(Model):
    type: Literal["pong"] = "pong"
    t0: float
    t_server: float


class ErrorMsg(Model):
    type: Literal["error"] = "error"
    message: str


# --- WebSocket messages: phone -> server ---------------------------------------


class MicHello(InputModel):
    type: Literal["hello"] = "hello"
    name: str = Field(min_length=1, max_length=20)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    player_id: str | None = None


class PitchBatch(InputModel):
    type: Literal["pitch"] = "pitch"
    frames: list[PitchFrame] = Field(max_length=100)


class MicCalibration(InputModel):
    type: Literal["calibration"] = "calibration"
    noise_rms: float = Field(ge=0, le=1)
    latency_ms: int = Field(ge=0, le=1000)


MicToServer = Annotated[Union[MicHello, ClockPing, PitchBatch, MicCalibration], Field(discriminator="type")]


# --- WebSocket messages: server -> phone ---------------------------------------


class WelcomeMsg(Model):
    type: Literal["welcome"] = "welcome"
    room: str
    player: Player


class MicStatusMsg(Model):
    type: Literal["status"] = "status"
    song_title: str | None
    line: str | None
    next_line: str | None
    playing: bool


class MicScoreMsg(Model):
    type: Literal["score"] = "score"
    score: int
    rank: int
    players: int
    combo: int


class SongRecord(Model):
    """Best score ever on a song."""

    song_id: str
    song_title: str
    name: str
    color: str
    score: int
    played_at: float


class ResultsMsg(Model):
    type: Literal["results"] = "results"
    song_title: str
    simplified: bool
    ranking: list[ScoreEntry]
    awards: list[AwardEntry]
    # The song's record before this game, and whether the winner just beat it.
    previous_record: SongRecord | None = None
    new_record: bool = False


class GameRecord(Model):
    """A finished competition, as kept in the history."""

    id: int
    song_id: str
    song_title: str
    played_at: float
    simplified: bool
    ranking: list[ScoreEntry]
    awards: list[AwardEntry]


class PlayerRanking(Model):
    name: str
    color: str
    games: int
    wins: int
    best_score: int
    average_score: int


class Rankings(Model):
    players: list[PlayerRanking]
    records: list[SongRecord]


# Whisper models that can be chosen; "" = automatic (medium with GPU, small on CPU).
WhisperModelName = Literal["", "tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
WHISPER_MODELS: tuple[str, ...] = get_args(WhisperModelName)


class AppSettings(Model):
    """Settings changed from the app (stored in data/settings.json)."""

    whisper_model: WhisperModelName = ""


class GameStateMsg(Model):
    """Sent to phones and host whenever a game starts or ends."""

    type: Literal["game"] = "game"
    mode: GameMode
    song_title: str | None
    simplified: bool


ServerToMic = Annotated[
    Union[WelcomeMsg, ClockPong, MicStatusMsg, MicScoreMsg, ResultsMsg, GameStateMsg, ErrorMsg],
    Field(discriminator="type"),
]


# --- WebSocket messages: host screen -> server ---------------------------------


class PlaybackState(InputModel):
    """Where the song is. position_ms was heard at server_ms (server clock)."""

    type: Literal["playback"] = "playback"
    song_id: str | None
    playing: bool
    position_ms: float
    server_ms: float
    offset_ms: int = 0


class RemovePlayer(InputModel):
    type: Literal["remove_player"] = "remove_player"
    player_id: str


class StartGame(InputModel):
    type: Literal["start_game"] = "start_game"
    song_id: str
    mode: GameMode


class StopGame(InputModel):
    """End the current game. With publish=True the final results are computed and shown."""

    type: Literal["stop_game"] = "stop_game"
    publish: bool = True


HostToServer = Annotated[
    Union[ClockPing, PlaybackState, RemovePlayer, StartGame, StopGame], Field(discriminator="type")
]


# --- WebSocket messages: server -> host screen ---------------------------------


class SongUpdatedMsg(Model):
    type: Literal["song"] = "song"
    song: Song


class RoomMsg(Model):
    type: Literal["room"] = "room"
    code: str
    mic_url: str
    https: bool
    players: list[Player]


class PitchLiveMsg(Model):
    type: Literal["pitch_live"] = "pitch_live"
    players: list[PlayerPitch]


class ScoresMsg(Model):
    type: Literal["scores"] = "scores"
    simplified: bool
    entries: list[ScoreEntry]


ServerToHost = Annotated[
    Union[SongUpdatedMsg, RoomMsg, PitchLiveMsg, ClockPong, ScoresMsg, ResultsMsg, GameStateMsg, ErrorMsg],
    Field(discriminator="type"),
]
