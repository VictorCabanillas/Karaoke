"""Client for LRCLIB (https://lrclib.net), an open database of synced lyrics."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from processing.lrc import parse_lrc, plain_lyrics
from server.config import settings
from server.models import LrclibCandidate, Lyrics

DURATION_TOLERANCE_S = 2.0
RATE_LIMIT_RETRIES = 3
MAX_RETRY_WAIT_S = 5.0

Record = dict[str, Any]


class LrclibClient:
    def __init__(self, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self._base_url = (base_url or settings.lrclib_url).rstrip("/")
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"User-Agent": settings.user_agent},
            timeout=15.0,
            transport=self._transport,
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET that waits and retries when LRCLIB answers 429 (rate limited)."""
        async with self._client() as client:
            for attempt in range(RATE_LIMIT_RETRIES + 1):
                r = await client.get(path, params=params)
                if r.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                    return r
                try:
                    wait = float(r.headers.get("retry-after", "1"))
                except ValueError:
                    wait = 1.0
                await asyncio.sleep(min(MAX_RETRY_WAIT_S, max(0.5, wait)) * (attempt + 1))
        raise AssertionError("unreachable")

    async def get(self, track: str, artist: str, album: str = "", duration_s: float | None = None) -> Record | None:
        params: dict[str, Any] = {"track_name": track, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration_s:
            params["duration"] = round(duration_s)
        r = await self._get("/api/get", params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def get_by_id(self, lrclib_id: int) -> Record | None:
        r = await self._get(f"/api/get/{lrclib_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def search(self, q: str = "", track: str = "", artist: str = "") -> list[Record]:
        params = {k: v for k, v in {"q": q, "track_name": track, "artist_name": artist}.items() if v}
        if not params:
            return []
        r = await self._get("/api/search", params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []


def to_candidate(record: Record) -> LrclibCandidate:
    return LrclibCandidate(
        id=record["id"],
        track_name=record.get("trackName") or "",
        artist_name=record.get("artistName") or "",
        album_name=record.get("albumName"),
        duration=record.get("duration"),
        instrumental=bool(record.get("instrumental")),
        has_synced=bool(record.get("syncedLyrics")),
        has_plain=bool(record.get("plainLyrics")),
    )


def choose_best(records: list[Record], duration_s: float | None) -> Record | None:
    """Pick the record whose duration is closest (±2 s), preferring synced lyrics.

    Returns None when nothing is close enough: the user picks manually in the UI.
    """
    usable = [r for r in records if r.get("syncedLyrics") or r.get("plainLyrics") or r.get("instrumental")]
    if not usable:
        return None
    if not duration_s:
        synced = [r for r in usable if r.get("syncedLyrics")]
        return (synced or usable)[0]

    def distance(r: Record) -> float:
        d = r.get("duration")
        return abs(float(d) - duration_s) if d is not None else float("inf")

    close = [r for r in usable if distance(r) <= DURATION_TOLERANCE_S]
    if not close:
        return None
    synced = [r for r in close if r.get("syncedLyrics")]
    return min(synced or close, key=distance)


def record_to_lyrics(record: Record, duration_s: float | None) -> Lyrics | None:
    if record.get("syncedLyrics"):
        return parse_lrc(record["syncedLyrics"], duration_s)
    if record.get("plainLyrics"):
        return plain_lyrics(record["plainLyrics"])
    return None


async def find_lyrics(
    client: LrclibClient, title: str, artist: str, album: str, duration_s: float | None
) -> Record | None:
    """Exact match first, then a flexible search filtered by duration."""
    if title and artist:
        exact = await client.get(title, artist, album, duration_s)
        if exact and (exact.get("syncedLyrics") or exact.get("instrumental")):
            return exact
        results = await client.search(track=title, artist=artist)
        best = choose_best(results, duration_s)
        if best:
            return best
        if exact:
            return exact
    if title:
        results = await client.search(q=f"{artist} {title}".strip())
        return choose_best(results, duration_s)
    return None
