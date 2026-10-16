import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import downloads
from server.downloads import guess_artist_title, track_metadata
from server.main import create_app
from server.models import OnlineTrack
from tests.conftest import FakeLrclib, make_wav


class FakeSource:
    """Stands in for YouTube: never touches the network."""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def search(self, query, limit=10):
        return [OnlineTrack(id="abcdefghijk", title=f"Grupo Inventado - {query} (Official Video)", channel="Grupo Inventado VEVO",
                            duration_s=2.0, thumbnail=None)]  # fmt: skip

    def download(self, video_id, folder: Path, on_progress):
        if self.fail:
            raise RuntimeError("Video unavailable")
        for f in (0.25, 0.5, 1.0):
            on_progress(f)
        path = folder / "original.wav"
        path.write_bytes(make_wav(2.0))
        info = {"title": "Grupo Inventado - Cancion de Prueba (Video Oficial)", "channel": "Grupo Inventado VEVO",
                "duration": 2.0}  # fmt: skip
        return path, info


def make_client(settings, source):
    return TestClient(create_app(settings, lrclib=FakeLrclib(), online_source=source))


def wait_for(client, song_id, status_not="downloading", timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        song = client.get(f"/api/songs/{song_id}").json()
        if song["status"] != status_not:
            return song
        time.sleep(0.05)
    raise AssertionError("download did not finish")


@pytest.mark.parametrize(
    "title, channel, expected",
    [
        ("Grupo Inventado - Cancion de Prueba (Official Video)", "x", ("Grupo Inventado", "Cancion de Prueba")),
        ("Artista – Tema [Lyrics] | Letra", "x", ("Artista", "Tema")),
        ("Tema Suelto (Video Oficial)", "Artista - Topic", ("Artista", "Tema Suelto")),
        ("Otro Tema (HD)", "ArtistaVEVO", ("Artista", "Otro Tema")),
        ("Artista: Tema con dos puntos", "x", ("Artista", "Tema con dos puntos")),
        ("Artista ~ Tema", "x", ("Artista", "Tema")),
    ],
)
def test_guess_artist_title(title, channel, expected):
    assert guess_artist_title(title, channel) == expected


def test_track_metadata_prefers_music_fields():
    info = {"title": "whatever (Official Video)", "track": "Tema", "artist": "Artista, Invitado", "album": "Disco",
            "duration": 200}  # fmt: skip
    assert track_metadata(info) == {"title": "Tema", "artist": "Artista", "album": "Disco", "duration": 200.0}


def test_search_and_download(settings):
    with make_client(settings, FakeSource()) as client, client.websocket_connect("/ws/host") as host:
        assert client.get("/api/system").json()["downloads_available"]
        results = client.get("/api/online/search", params={"q": "Cancion de Prueba"}).json()
        assert results[0]["id"] == "abcdefghijk"

        song = client.post("/api/online/download", json={"video_id": "abcdefghijk", "title": results[0]["title"],
                                                         "channel": results[0]["channel"]}).json()  # fmt: skip
        assert song["status"] == "downloading" and not song["has_audio"]
        assert song["title"] == "Cancion de Prueba"

        done = wait_for(client, song["id"])
        assert done["status"] == "lyrics_ready" and done["has_audio"]
        assert (done["title"], done["artist"]) == ("Cancion de Prueba", "Grupo Inventado")
        assert client.get(f"/api/songs/{song['id']}/audio/original").status_code == 200
        labels = []
        while not labels or labels[-1] != "lyrics_ready":
            msg = host.receive_json()
            if msg["type"] == "song" and msg["song"]["id"] == song["id"]:
                labels.append(msg["song"]["progress_label"] if msg["song"]["status"] == "downloading" else msg["song"]["status"])
        assert "Descargando 50%" in labels


def test_failed_download_marks_error(settings):
    with make_client(settings, FakeSource(fail=True)) as client:
        song = client.post("/api/online/download", json={"video_id": "abcdefghijk"}).json()
        done = wait_for(client, song["id"])
        assert done["status"] == "error" and "Video unavailable" in done["error"] and not done["has_audio"]


def test_downloads_unavailable_without_yt_dlp(client, monkeypatch):
    monkeypatch.setattr(downloads, "available", lambda: False)
    r = client.get("/api/online/search", params={"q": "x"})
    assert r.status_code == 503 and "yt-dlp" in r.json()["detail"]
    assert client.post("/api/online/download", json={"video_id": "bad"}).status_code in (422, 503)


@pytest.mark.anyio
async def test_suggestions_are_parsed_cached_and_never_fail():
    import httpx

    from server.downloads import SuggestClient

    calls = []

    def handler(request):
        calls.append(request.url.params["q"])
        if request.url.params["q"] == "roto":
            return httpx.Response(500)
        return httpx.Response(200, json=["la lo", ["la lola", "la lola letra", "la lola", " "], [], {}])

    client = SuggestClient(transport=httpx.MockTransport(handler))
    assert await client.suggest("La  Lo") == ["la lola", "la lola letra"]
    assert await client.suggest("la lo") == ["la lola", "la lola letra"]  # cached
    assert calls == ["la lo"]
    assert await client.suggest("roto") == []
    assert await client.suggest("x") == []  # too short: no request
    await client.close()


def test_suggest_endpoint(settings):
    class Fake:
        async def suggest(self, q):
            return [f"{q} letra"]

        async def close(self):
            pass

    with TestClient(create_app(settings, lrclib=FakeLrclib(), suggest=Fake())) as client:
        assert client.get("/api/online/suggest", params={"q": "la lola"}).json() == ["la lola letra"]
