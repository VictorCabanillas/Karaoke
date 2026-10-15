from __future__ import annotations

import io
import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app

# Invented lyrics: never put real song lyrics in the repository.
FAKE_LRC = """[ar:Grupo Inventado]
[ti:Cancion de Prueba]
[00:01.00]La luna come queso azul
[00:04.50]el gato baila en el balcón
[00:08.00]
[00:12.00]y mañana volverá
"""


def make_wav(seconds: float = 2.0, freq: float = 440.0, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(int(seconds * rate))
        )
        w.writeframes(frames)
    return buf.getvalue()


class FakeLrclib:
    """Stand-in for LrclibClient that never touches the network."""

    def __init__(self, records: list[dict] | None = None):
        self.records = records if records is not None else [
            {
                "id": 7,
                "trackName": "Cancion de Prueba",
                "artistName": "Grupo Inventado",
                "albumName": "Disco",
                "duration": 2.0,
                "instrumental": False,
                "syncedLyrics": FAKE_LRC,
                "plainLyrics": "La luna come queso azul",
            }
        ]

    async def get(self, track, artist, album="", duration_s=None):
        for r in self.records:
            if r["trackName"] == track and r["artistName"] == artist:
                return r
        return None

    async def get_by_id(self, lrclib_id):
        return next((r for r in self.records if r["id"] == lrclib_id), None)

    async def search(self, q="", track="", artist=""):
        needle = (track or q).lower()
        return [r for r in self.records if not needle or needle in r["trackName"].lower()]


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.data_dir = tmp_path / "data"
    s.web_dist = tmp_path / "no-dist"
    s.auto_process = False
    return s


@pytest.fixture
def fake_lrclib() -> FakeLrclib:
    return FakeLrclib()


@pytest.fixture
def client(settings, fake_lrclib):
    app = create_app(settings, lrclib=fake_lrclib)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def anyio_backend():
    return "asyncio"
