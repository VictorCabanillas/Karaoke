import httpx
import pytest

from processing.lrclib import LrclibClient, choose_best, find_lyrics


def rec(id, duration, synced=True, plain=True):
    return {
        "id": id,
        "trackName": "T",
        "artistName": "A",
        "duration": duration,
        "syncedLyrics": "[00:01.00]hola" if synced else None,
        "plainLyrics": "hola" if plain else None,
        "instrumental": False,
    }


def test_choose_best_prefers_synced_within_tolerance():
    records = [rec(1, 200.0, synced=False), rec(2, 201.5), rec(3, 199.9, synced=False), rec(4, 230.0)]
    assert choose_best(records, 200.0)["id"] == 2


def test_choose_best_none_when_far():
    assert choose_best([rec(1, 260.0)], 200.0) is None


def test_choose_best_without_duration():
    assert choose_best([rec(1, 10, synced=False), rec(2, 20)], None)["id"] == 2


@pytest.mark.anyio
async def test_client_sends_user_agent_and_falls_back_to_search():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/get":
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=[rec(5, 180.5)])

    client = LrclibClient(base_url="https://lrclib.test", transport=httpx.MockTransport(handler))
    found = await find_lyrics(client, "T", "A", "", 180.0)
    assert found["id"] == 5
    assert all("karaoke-amigos" in r.headers["user-agent"] for r in seen)
    assert seen[0].url.params["duration"] == "180"


@pytest.mark.anyio
async def test_client_retries_when_rate_limited(monkeypatch):
    import processing.lrclib as lrclib_module

    async def no_sleep(_):
        return None

    monkeypatch.setattr(lrclib_module.asyncio, "sleep", no_sleep)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"name": "RateLimited"})
        return httpx.Response(200, json=[rec(9, 100.0)])

    client = LrclibClient(base_url="https://lrclib.test", transport=httpx.MockTransport(handler))
    assert (await client.search(q="x"))[0]["id"] == 9
    assert len(calls) == 3
