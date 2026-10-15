"""Spotify: OAuth with PKCE (no client secret), track search and tokens for the Web Playback SDK.

Spotify is optional and isolated here: the app works fully without it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from server.models import SpotifyTrack

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
# Playback in the browser (streaming) + controlling it + reading the account.
SCOPES = "streaming user-read-email user-read-private user-read-playback-state user-modify-playback-state"
SEARCH_LIMIT = 10  # maximum page size allowed for apps in development mode
REFRESH_MARGIN_S = 60


class SpotifyError(Exception):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def to_track(item: dict[str, Any]) -> SpotifyTrack:
    images = (item.get("album") or {}).get("images") or []
    return SpotifyTrack(
        id=item["id"],
        uri=item["uri"],
        name=item.get("name") or "",
        artists=", ".join(a.get("name", "") for a in item.get("artists") or []),
        album=(item.get("album") or {}).get("name") or "",
        duration_ms=int(item.get("duration_ms") or 0),
        image_url=images[-1]["url"] if images else None,
    )


class SpotifyService:
    def __init__(
        self,
        client_id: str,
        redirect_uri: str,
        token_file: Path,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_file = token_file
        self._transport = transport
        self._pending: dict[str, str] = {}  # state -> code verifier
        self._tokens: dict[str, Any] | None = self._load()

    # --- state ------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    @property
    def connected(self) -> bool:
        return self._tokens is not None

    def _load(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.token_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save(self, tokens: dict[str, Any] | None) -> None:
        self._tokens = tokens
        if tokens is None:
            self.token_file.unlink(missing_ok=True)
        else:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(json.dumps(tokens), encoding="utf-8")

    def logout(self) -> None:
        self._save(None)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15.0, transport=self._transport)

    # --- OAuth (PKCE) -----------------------------------------------------

    def authorize_url(self) -> str:
        if not self.configured:
            raise SpotifyError("Falta configurar KARAOKE_SPOTIFY_CLIENT_ID")
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(16)
        self._pending[state] = verifier
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
            "scope": SCOPES,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        async with self._client() as client:
            r = await client.post(TOKEN_URL, data={**data, "client_id": self.client_id})
        if r.status_code != 200:
            raise SpotifyError(f"Spotify rechazó la autorización ({r.status_code}): {r.text[:200]}")
        payload = r.json()
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or (self._tokens or {}).get("refresh_token"),
            "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        }

    async def finish_login(self, code: str, state: str) -> None:
        verifier = self._pending.pop(state, None)
        if verifier is None:
            raise SpotifyError("La sesión de inicio con Spotify caducó; vuelve a intentarlo")
        tokens = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            }
        )
        self._save(tokens)

    async def access_token(self) -> tuple[str, float]:
        if self._tokens is None:
            raise SpotifyError("No hay sesión de Spotify")
        if self._tokens["expires_at"] - REFRESH_MARGIN_S < time.time():
            if not self._tokens.get("refresh_token"):
                self.logout()
                raise SpotifyError("La sesión de Spotify caducó; vuelve a conectar")
            try:
                self._save(
                    await self._token_request(
                        {"grant_type": "refresh_token", "refresh_token": self._tokens["refresh_token"]}
                    )
                )
            except SpotifyError:
                self.logout()
                raise
        assert self._tokens is not None
        return self._tokens["access_token"], self._tokens["expires_at"]

    # --- Web API ----------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token, _ = await self.access_token()
        async with self._client() as client:
            r = await client.get(f"{API_URL}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 401:
            raise SpotifyError("Spotify no aceptó la sesión; vuelve a conectar")
        if r.status_code != 200:
            raise SpotifyError(f"Error de Spotify ({r.status_code}): {r.text[:200]}")
        return r.json()

    async def search(self, query: str) -> list[SpotifyTrack]:
        if not query.strip():
            return []
        data = await self._get("/search", {"q": query, "type": "track", "limit": SEARCH_LIMIT})
        return [to_track(item) for item in (data.get("tracks") or {}).get("items") or [] if item]

    async def track(self, track_id: str) -> SpotifyTrack:
        return to_track(await self._get(f"/tracks/{track_id}"))
