"""The game room: connected screens, players and the song clock."""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import WebSocket
from pydantic import BaseModel

from server.models import (
    LivePitch,
    Lyrics,
    MicStatusMsg,
    PitchFrame,
    PitchLiveMsg,
    PlaybackState,
    Player,
    PlayerPitch,
    RoomMsg,
)

log = logging.getLogger(__name__)

ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"
# Frames below this confidence are treated as "no clear pitch".
MIN_CONFIDENCE = 0.6
DEFAULT_GATE_RMS = 0.01
TICK_S = 0.1


def now_ms() -> float:
    return time.monotonic() * 1000.0


def hz_to_midi(hz: float) -> float:
    return 69.0 + 12.0 * math.log2(hz / 440.0)


async def send_json(ws: WebSocket, msg: BaseModel) -> bool:
    try:
        await ws.send_text(msg.model_dump_json())
        return True
    except Exception:  # closed while sending
        return False


class HostHub:
    """Main screens connected over WebSocket (usually one, but more are fine)."""

    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self) -> None:
        self.loop = asyncio.get_running_loop()

    async def connect(self, ws: WebSocket) -> None:
        self.sockets.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.sockets.discard(ws)

    async def send(self, ws: WebSocket, msg: BaseModel) -> None:
        if not await send_json(ws, msg):
            self.disconnect(ws)

    async def broadcast(self, msg: BaseModel) -> None:
        for ws in list(self.sockets):
            await self.send(ws, msg)

    def broadcast_threadsafe(self, msg: BaseModel) -> None:
        """Broadcast from a worker thread."""
        if self.loop is not None and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.broadcast(msg), self.loop)


@dataclass
class SongClock:
    """Song position as a function of server time, fed by the host's playback reports."""

    song_id: str | None = None
    playing: bool = False
    position_ms: float = 0.0
    server_ms: float = 0.0
    offset_ms: int = 0

    def update(self, state: PlaybackState) -> None:
        self.song_id = state.song_id
        self.playing = state.playing
        self.position_ms = state.position_ms
        self.server_ms = state.server_ms
        self.offset_ms = state.offset_ms

    def at(self, server_ms: float) -> float | None:
        """Song position (ms) at a server time, or None when nothing is playing."""
        if self.song_id is None or not self.playing:
            return None
        return self.position_ms + (server_ms - self.server_ms)


@dataclass
class PlayerConn:
    player: Player
    ws: WebSocket | None = None
    gate_rms: float = DEFAULT_GATE_RMS
    live: list[LivePitch] = field(default_factory=list)


class Room:
    def __init__(self, code: str, hub: HostHub, mic_url: Callable[[str], str], https: bool, default_latency_ms: int):
        self.code = code
        self.hub = hub
        self._mic_url = mic_url
        self.https = https
        self.default_latency_ms = default_latency_ms
        self.players: dict[str, PlayerConn] = {}
        self.clock = SongClock()
        self.song_title: str | None = None
        self.lyrics: Lyrics | None = None
        self._last_status: tuple | None = None
        self._task: asyncio.Task | None = None
        # Hook for the competition (phase 4): called with (player_id, song_ms, midi).
        self.on_frame: Callable[[str, float, float | None], None] | None = None
        self.on_tick: Callable[[], object] | None = None

    @staticmethod
    def load_or_create_code(path: Path) -> str:
        """Keep the room code across restarts so phones can reconnect without rescanning."""
        try:
            code = path.read_text(encoding="utf-8").strip()
            if len(code) == 4 and all(c in ROOM_ALPHABET for c in code):
                return code
        except OSError:
            pass
        code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(4))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        return code

    @property
    def mic_url(self) -> str:
        return self._mic_url(self.code)

    def state(self) -> RoomMsg:
        return RoomMsg(
            code=self.code,
            mic_url=self.mic_url,
            https=self.https,
            players=[c.player for c in self.players.values()],
        )

    async def broadcast_room(self) -> None:
        await self.hub.broadcast(self.state())

    # --- players ----------------------------------------------------------

    async def join(self, ws: WebSocket, name: str, color: str, player_id: str | None) -> Player:
        conn = self.players.get(player_id or "")
        if conn is None:
            player = Player(
                id=secrets.token_hex(4), name=name.strip(), color=color, latency_ms=self.default_latency_ms
            )
            conn = PlayerConn(player=player)
            self.players[player.id] = conn
        else:
            if conn.ws is not None and conn.ws is not ws:
                try:
                    await conn.ws.close(code=4000)
                except Exception:
                    pass
            conn.player.name = name.strip()
            conn.player.color = color
        conn.ws = ws
        conn.player.connected = True
        await self.broadcast_room()
        self._last_status = None  # resend the lyrics status to everyone
        return conn.player

    async def leave(self, player_id: str, ws: WebSocket) -> None:
        conn = self.players.get(player_id)
        if conn is None or conn.ws is not ws:
            return
        conn.ws = None
        conn.player.connected = False
        await self.broadcast_room()

    async def remove(self, player_id: str) -> None:
        conn = self.players.pop(player_id, None)
        if conn and conn.ws is not None:
            try:
                await conn.ws.close(code=4001)
            except Exception:
                pass
        await self.broadcast_room()

    async def calibrate(self, player_id: str, noise_rms: float, latency_ms: int) -> None:
        conn = self.players.get(player_id)
        if conn is None:
            return
        conn.gate_rms = max(0.002, noise_rms)
        conn.player.latency_ms = latency_ms
        conn.player.calibrated = True
        await self.broadcast_room()

    def add_frames(self, player_id: str, frames: list[PitchFrame]) -> None:
        conn = self.players.get(player_id)
        if conn is None:
            return
        for frame in frames:
            server_ms = frame.t - conn.player.latency_ms
            song_ms = self.clock.at(server_ms)
            voiced = (
                frame.f0 is not None
                and frame.f0 > 0
                and frame.conf >= MIN_CONFIDENCE
                and frame.rms >= conn.gate_rms
            )
            midi = round(hz_to_midi(frame.f0), 2) if voiced and frame.f0 else None
            conn.live.append(LivePitch(server_ms=server_ms, song_ms=song_ms, midi=midi, rms=round(frame.rms, 4)))
            if song_ms is not None and self.on_frame is not None:
                self.on_frame(player_id, song_ms, midi)

    # --- song -------------------------------------------------------------

    def set_song(self, title: str | None, lyrics: Lyrics | None) -> None:
        self.song_title = title
        self.lyrics = lyrics
        self._last_status = None

    def _current_lines(self) -> tuple[str | None, str | None]:
        lyrics = self.lyrics
        position = self.clock.at(now_ms()) if self.clock.playing else self.clock.position_ms
        if lyrics is None or not lyrics.synced or not lyrics.lines or position is None:
            return None, None
        t = position - self.clock.offset_ms
        index = -1
        for i, line in enumerate(lyrics.lines):
            if line.start_ms <= t:
                index = i
            else:
                break
        # Between lines (or before the first one) only the upcoming line is shown.
        current = index if index >= 0 and t <= lyrics.lines[index].end_ms + 400 else None
        nxt = index + 1

        def text(i: int | None) -> str | None:
            if i is None or i < 0 or i >= len(lyrics.lines):
                return None
            return " ".join(w.text for w in lyrics.lines[i].words)

        return text(current), text(nxt)

    async def _send_mic_status(self) -> None:
        line, next_line = self._current_lines()
        status = (self.song_title, line, next_line, self.clock.playing)
        if status == self._last_status:
            return
        self._last_status = status
        msg = MicStatusMsg(song_title=self.song_title, line=line, next_line=next_line, playing=self.clock.playing)
        for conn in list(self.players.values()):
            if conn.ws is not None:
                await send_json(conn.ws, msg)

    # --- periodic tick ----------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await self.tick()
            except Exception:
                log.exception("Room tick failed")

    async def tick(self) -> None:
        live = [PlayerPitch(player_id=pid, frames=c.live) for pid, c in self.players.items() if c.live]
        for c in self.players.values():
            c.live = []
        if live:
            await self.hub.broadcast(PitchLiveMsg(players=live))
        await self._send_mic_status()
        if self.on_tick is not None:
            result = self.on_tick()
            if asyncio.iscoroutine(result):
                await result
