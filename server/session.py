"""Connected screens and players."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket
from pydantic import BaseModel

log = logging.getLogger(__name__)


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
        try:
            await ws.send_text(msg.model_dump_json())
        except Exception:  # closed while sending
            self.disconnect(ws)

    async def broadcast(self, msg: BaseModel) -> None:
        if not self.sockets:
            return
        data = msg.model_dump_json()
        for ws in list(self.sockets):
            try:
                await ws.send_text(data)
            except Exception:
                self.disconnect(ws)

    def broadcast_threadsafe(self, msg: BaseModel) -> None:
        """Broadcast from a worker thread."""
        if self.loop is not None and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.broadcast(msg), self.loop)
