"""WebSocket of the main screen."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/host")
async def host_socket(ws: WebSocket) -> None:
    hub = ws.app.state.hub
    await ws.accept()
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)
