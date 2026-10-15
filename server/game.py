"""Competition mode: feeds every player's frames to a scorer and publishes live scores and results."""

from __future__ import annotations

import logging

from server.library import MELODY_FILE, Library
from server.models import (
    AwardEntry,
    ErrorMsg,
    GameStateMsg,
    MicScoreMsg,
    ResultsMsg,
    ScoreEntry,
    ScoresMsg,
    Song,
    StartGame,
    StopGame,
)
from server.scoring import PlayerScorer, Reference, awards
from server.session import Room, now_ms, send_json

log = logging.getLogger(__name__)

# Windows are scored once the song is this far past them (late frames over Wi-Fi).
FINALIZE_DELAY_MS = 1500
# Phones get their own score every this many ticks (~2 per second).
MIC_SCORE_EVERY_TICKS = 5


class Game:
    def __init__(self, song: Song, reference: Reference, simplified: bool):
        self.song = song
        self.reference = reference
        self.simplified = simplified
        self.scorers: dict[str, PlayerScorer] = {}

    def scorer(self, player_id: str) -> PlayerScorer:
        if player_id not in self.scorers:
            self.scorers[player_id] = PlayerScorer(self.reference)
        return self.scorers[player_id]

    def add(self, player_id: str, song_ms: float, midi: float | None) -> None:
        self.scorer(player_id).add(song_ms, midi)

    def finalize(self, song_ms: float) -> None:
        for scorer in self.scorers.values():
            scorer.finalize(song_ms)

    def finalize_all(self) -> None:
        for scorer in self.scorers.values():
            scorer.finalize_all()


def build_reference(library: Library, song: Song) -> tuple[Reference, bool] | None:
    """Reference melody when the song is processed; otherwise the synced lyrics (simplified scoring)."""
    if song.has_melody:
        data = library.load_json(song.id, MELODY_FILE)
        if data:
            return Reference.from_melody(data["hop_ms"], data["midi"]), False
    lyrics = library.load_lyrics(song.id)
    if lyrics is not None and lyrics.synced and lyrics.lines:
        duration_ms = round(song.duration_s * 1000) or (lyrics.lines[-1].end_ms + 5000)
        return Reference.from_lyrics(lyrics, song.offset_ms, duration_ms), True
    return None


class GameController:
    def __init__(self, room: Room, library: Library):
        self.room = room
        self.library = library
        self.game: Game | None = None
        self._ticks = 0
        room.on_frame = self._on_frame
        room.on_tick = self._on_tick

    def _on_frame(self, player_id: str, song_ms: float, midi: float | None) -> None:
        game = self.game
        if game is not None and self.room.clock.song_id == game.song.id:
            game.add(player_id, song_ms, midi)

    async def start(self, msg: StartGame) -> None:
        self.game = None
        song = self.library.get(msg.song_id)
        if song is None:
            await self.room.hub.broadcast(ErrorMsg(message="Canción no encontrada"))
            return
        if msg.mode == "free":
            await self._announce(GameStateMsg(mode="free", song_title=song.title, simplified=False))
            return
        built = build_reference(self.library, song)
        if built is None:
            await self.room.hub.broadcast(
                ErrorMsg(message="Para competir hace falta letra sincronizada o que la canción esté procesada")
            )
            await self._announce(GameStateMsg(mode="free", song_title=song.title, simplified=False))
            return
        reference, simplified = built
        self.game = Game(song, reference, simplified)
        for pid, conn in self.room.players.items():
            if conn.player.connected:
                self.game.scorer(pid)
        await self._announce(GameStateMsg(mode="competition", song_title=song.title, simplified=simplified))
        await self.room.hub.broadcast(self.scores())

    async def stop(self, msg: StopGame) -> None:
        game, self.game = self.game, None
        if game is None:
            return
        if msg.publish:
            position = self.room.clock.at(now_ms()) if self.room.clock.playing else self.room.clock.position_ms
            # Song finished (or almost): score everything; stopped early: only what was sung so far.
            if position is None or position >= len(game.reference.expected) * 100 - 2000:
                game.finalize_all()
            else:
                game.finalize(position)
            results = self.results(game)
            await self.room.hub.broadcast(results)
            await self._send_mics(results)
        await self._announce(GameStateMsg(mode="free", song_title=None, simplified=False))

    async def _announce(self, msg: GameStateMsg) -> None:
        await self.room.hub.broadcast(msg)
        await self._send_mics(msg)

    async def _send_mics(self, msg) -> None:
        for conn in list(self.room.players.values()):
            if conn.ws is not None:
                await send_json(conn.ws, msg)

    def entries(self, game: Game) -> list[ScoreEntry]:
        out = []
        for pid, scorer in game.scorers.items():
            conn = self.room.players.get(pid)
            if conn is None:
                continue
            out.append(
                ScoreEntry(
                    player_id=pid,
                    name=conn.player.name,
                    color=conn.player.color,
                    score=scorer.score,
                    combo=scorer.stats.combo,
                    presence=round(scorer.stats.presence, 3),
                    accuracy=round(scorer.stats.accuracy, 3) if not game.simplified else None,
                )
            )
        out.sort(key=lambda e: e.score, reverse=True)
        return out

    def scores(self) -> ScoresMsg:
        game = self.game
        if game is None:
            return ScoresMsg(simplified=False, entries=[])
        return ScoresMsg(simplified=game.simplified, entries=self.entries(game))

    def results(self, game: Game) -> ResultsMsg:
        ranking = self.entries(game)
        names = {e.player_id: e.name for e in ranking}
        stats = {pid: s.stats for pid, s in game.scorers.items() if pid in names}
        return ResultsMsg(
            song_title=game.song.title,
            simplified=game.simplified,
            ranking=ranking,
            awards=[
                AwardEntry(title=a.title, player_id=a.player_id, name=names[a.player_id], detail=a.detail)
                for a in awards(stats)
            ],
        )

    async def _on_tick(self) -> None:
        game = self.game
        if game is None:
            return
        clock = self.room.clock
        if clock.playing and clock.song_id == game.song.id:
            position = clock.at(now_ms())
            if position is not None:
                game.finalize(position - FINALIZE_DELAY_MS)
        scores = self.scores()
        await self.room.hub.broadcast(scores)
        self._ticks += 1
        if self._ticks % MIC_SCORE_EVERY_TICKS == 0:
            for rank, entry in enumerate(scores.entries, start=1):
                conn = self.room.players.get(entry.player_id)
                if conn is not None and conn.ws is not None:
                    await send_json(
                        conn.ws,
                        MicScoreMsg(score=entry.score, rank=rank, players=len(scores.entries), combo=entry.combo),
                    )
