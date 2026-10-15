"""WebSocket of the players' phones."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from server.models import ClockPing, ClockPong, ErrorMsg, MicCalibration, MicHello, MicToServer, PitchBatch, WelcomeMsg
from server.session import now_ms, send_json

router = APIRouter()
_messages: TypeAdapter[MicToServer] = TypeAdapter(MicToServer)


@router.websocket("/ws/mic")
async def mic_socket(ws: WebSocket, room: str = "") -> None:
    current = ws.app.state.room
    await ws.accept()
    if room.upper() != current.code:
        await send_json(ws, ErrorMsg(message="Código de sala incorrecto. Vuelve a escanear el QR."))
        await ws.close(code=4004)
        return

    player_id: str | None = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = _messages.validate_json(raw)
            except ValidationError as exc:
                if player_id is None:
                    await send_json(ws, ErrorMsg(message=f"Datos no válidos: {exc.errors()[0]['msg']}"))
                continue
            if isinstance(msg, ClockPing):
                await send_json(ws, ClockPong(t0=msg.t0, t_server=now_ms()))
            elif isinstance(msg, MicHello):
                player = await current.join(ws, msg.name, msg.color, msg.player_id)
                player_id = player.id
                await send_json(ws, WelcomeMsg(room=current.code, player=player))
            elif player_id is None:
                await send_json(ws, ErrorMsg(message="Primero hay que unirse a la sala"))
            elif isinstance(msg, PitchBatch):
                current.add_frames(player_id, msg.frames)
            elif isinstance(msg, MicCalibration):
                await current.calibrate(player_id, msg.noise_rms, msg.latency_ms)
    except WebSocketDisconnect:
        pass
    finally:
        if player_id is not None:
            await current.leave(player_id, ws)
