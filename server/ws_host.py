"""WebSocket of the main screen."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from server.models import ClockPing, ClockPong, HostToServer, PlaybackState, RemovePlayer
from server.session import now_ms

router = APIRouter()
_messages: TypeAdapter[HostToServer] = TypeAdapter(HostToServer)


@router.websocket("/ws/host")
async def host_socket(ws: WebSocket) -> None:
    app = ws.app
    hub = app.state.hub
    room = app.state.room
    await ws.accept()
    await hub.connect(ws)
    await hub.send(ws, room.state())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = _messages.validate_json(raw)
            except ValidationError:
                continue
            if isinstance(msg, ClockPing):
                await hub.send(ws, ClockPong(t0=msg.t0, t_server=now_ms()))
            elif isinstance(msg, PlaybackState):
                await app.state.on_playback(msg)
            elif isinstance(msg, RemovePlayer):
                await room.remove(msg.player_id)
            else:
                await app.state.on_host_message(msg)
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)
