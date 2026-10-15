import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from server.spotify import SpotifyService, pkce_pair
from tests.conftest import FakeLrclib

TRACK = {
    "id": "4uLU6hMCjMI75M1A2tKUQC",
    "uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
    "name": "Cancion de Prueba",
    "artists": [{"name": "Grupo Inventado"}, {"name": "Invitado"}],
    "album": {"name": "Disco", "images": [{"url": "https://img/large"}, {"url": "https://img/small"}]},
    "duration_ms": 2000,
}


class FakeSpotify:
    def __init__(self):
        self.token_calls: list[dict] = []
        self.api_calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "accounts.spotify.com":
            form = parse_qs(request.content.decode())
            self.token_calls.append({k: v[0] for k, v in form.items()})
            n = len(self.token_calls)
            return httpx.Response(200, json={"access_token": f"tok{n}", "refresh_token": "ref", "expires_in": 3600})
        self.api_calls.append(request)
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"tracks": {"items": [TRACK]}})
        if request.url.path.startswith("/v1/tracks/"):
            return httpx.Response(200, json=TRACK)
        return httpx.Response(404)


def test_pkce_pair():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


@pytest.fixture
def fake_spotify():
    return FakeSpotify()


@pytest.fixture
def spotify_client(settings, fake_spotify):
    service = SpotifyService(
        "client123", "http://127.0.0.1:8000/api/spotify/callback", settings.data_dir / "spotify.json",
        transport=httpx.MockTransport(fake_spotify),
    )  # fmt: skip
    app = create_app(settings, lrclib=FakeLrclib(), spotify=service)
    with TestClient(app) as c:
        yield c, service


def login(client):
    r = client.get("/api/spotify/login", follow_redirects=False)
    assert r.status_code == 307
    url = urlparse(r.headers["location"])
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.spotify.com"
    assert params["code_challenge_method"] == "S256" and params["client_id"] == "client123"
    assert "streaming" in params["scope"]
    r = client.get(f"/api/spotify/callback?code=abc&state={params['state']}", follow_redirects=False)
    assert r.headers["location"] == "/host/#/"
    return params


def test_login_flow_search_and_add(spotify_client, fake_spotify, settings):
    client, service = spotify_client
    assert client.get("/api/spotify/status").json() == {
        "configured": True, "connected": False, "redirect_uri": "http://127.0.0.1:8000/api/spotify/callback",
    }  # fmt: skip
    login(client)
    exchange = fake_spotify.token_calls[0]
    assert exchange["grant_type"] == "authorization_code" and exchange["code"] == "abc"
    assert exchange["code_verifier"] and "client_secret" not in exchange
    assert client.get("/api/spotify/status").json()["connected"]
    assert json.loads((settings.data_dir / "spotify.json").read_text())["refresh_token"] == "ref"

    results = client.get("/api/spotify/search", params={"q": "prueba"}).json()
    assert results[0]["artists"] == "Grupo Inventado, Invitado" and results[0]["image_url"] == "https://img/small"
    assert fake_spotify.api_calls[0].url.params["limit"] == "10"
    assert fake_spotify.api_calls[0].headers["authorization"] == "Bearer tok1"

    song = client.post("/api/spotify/songs", json={"track_id": TRACK["id"]}).json()
    assert song["origin"] == "spotify" and song["spotify_uri"] == TRACK["uri"]
    assert song["artist"] == "Grupo Inventado" and song["duration_s"] == 2.0
    assert song["status"] == "lyrics_ready" and song["lyrics_source"] == "lrclib_synced"
    again = client.post("/api/spotify/songs", json={"track_id": TRACK["id"]}).json()
    assert again["id"] == song["id"]
    assert client.post(f"/api/songs/{song['id']}/process").status_code == 400

    token = client.get("/api/spotify/token").json()
    assert token["access_token"] == "tok1"


def test_token_is_refreshed_when_expired(spotify_client, fake_spotify):
    client, service = spotify_client
    login(client)
    service._tokens["expires_at"] = time.time() - 1
    assert client.get("/api/spotify/token").json()["access_token"] == "tok2"
    assert fake_spotify.token_calls[-1] == {"grant_type": "refresh_token", "refresh_token": "ref", "client_id": "client123"}


def test_callback_with_unknown_state_and_logout(spotify_client):
    client, _ = spotify_client
    r = client.get("/api/spotify/callback?code=abc&state=nope", follow_redirects=False)
    assert "spotify_error" in r.headers["location"]
    login(client)
    assert client.post("/api/spotify/logout").status_code == 204
    assert client.get("/api/spotify/status").json()["connected"] is False
    assert client.get("/api/spotify/token").status_code == 401


def test_not_configured(client):
    assert client.get("/api/spotify/status").json()["configured"] is False
    assert client.get("/api/spotify/login", follow_redirects=False).status_code == 400
