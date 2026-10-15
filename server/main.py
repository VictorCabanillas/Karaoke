"""FastAPI application: REST API, WebSockets and the compiled frontend."""

from __future__ import annotations

import io
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import segno
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from processing import audio as audio_tools
from processing import device as device_mod
from processing.lrclib import LrclibClient, find_lyrics, record_to_lyrics, to_candidate
from processing.metadata import read_metadata
from processing.queue import Pipeline, ProcessingQueue
from server import ws_host, ws_mic
from server.config import Settings, lan_ip, settings as default_settings
from server.game import GameController
from server.spotify import SpotifyError, SpotifyService
from server.library import MELODY_FILE, Library
from server.models import (
    AddSpotifyTrack,
    ChooseLyrics,
    LrclibCandidate,
    Lyrics,
    Melody,
    PlaybackState,
    RoomMsg,
    Song,
    SongPatch,
    SongUpdatedMsg,
    SpotifyStatus,
    SpotifyToken,
    SpotifyTrack,
    StartGame,
    StopGame,
    SystemInfo,
)
from server.session import HostHub, Room

log = logging.getLogger("karaoke")

ALLOWED_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".webm", ".wma"}


def create_app(
    config: Settings | None = None,
    lrclib: LrclibClient | None = None,
    pipeline: Pipeline | None = None,
    spotify: SpotifyService | None = None,
) -> FastAPI:
    config = config or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        app.state.hub.bind_loop()
        app.state.library = Library(config.data_dir)
        app.state.queue = ProcessingQueue(
            app.state.library,
            on_update=lambda song: app.state.hub.broadcast_threadsafe(SongUpdatedMsg(song=song)),
            pipeline=pipeline or Pipeline(config.whisper_model),
        )
        app.state.queue.start()
        scheme = "https" if config.https else "http"
        app.state.room = Room(
            code=Room.load_or_create_code(config.data_dir / "room.txt"),
            hub=app.state.hub,
            mic_url=lambda code: f"{scheme}://{lan_ip()}:{config.public_port}/mic/?room={code}",
            https=config.https,
            default_latency_ms=config.default_mic_latency_ms,
        )
        app.state.game = GameController(app.state.room, app.state.library)
        app.state.room.start()
        yield
        await app.state.room.stop()
        app.state.queue.stop()
        app.state.library.close()

    app = FastAPI(title="Karaoke entre amigos", lifespan=lifespan)
    app.state.config = config
    app.state.lrclib = lrclib or LrclibClient()
    app.state.hub = HostHub()
    app.state.spotify = spotify or SpotifyService(
        config.spotify_client_id, config.spotify_callback, config.data_dir / "spotify.json"
    )
    app.include_router(ws_host.router)
    app.include_router(ws_mic.router)

    async def on_playback(state: PlaybackState) -> None:
        room: Room = app.state.room
        if state.song_id != room.clock.song_id:
            song = app.state.library.get(state.song_id) if state.song_id else None
            lyrics = app.state.library.load_lyrics(song.id) if song else None
            room.set_song(song.title if song else None, lyrics)
        room.clock.update(state)

    async def on_host_message(msg) -> None:
        if isinstance(msg, StartGame):
            await app.state.game.start(msg)
        elif isinstance(msg, StopGame):
            await app.state.game.stop(msg)

    app.state.on_playback = on_playback
    app.state.on_host_message = on_host_message

    def library(request: Request) -> Library:
        return request.app.state.library

    def get_song(request: Request, song_id: str) -> Song:
        song = library(request).get(song_id)
        if song is None:
            raise HTTPException(404, "Canción no encontrada")
        return song

    async def attach_lyrics(request: Request, song: Song) -> Song:
        lib = library(request)
        try:
            record = await find_lyrics(request.app.state.lrclib, song.title, song.artist, song.album, song.duration_s)
        except httpx.HTTPError as exc:
            log.warning("LRCLIB lookup failed for %s: %s", song.id, exc)
            return song
        if record is None:
            return song
        lyrics = record_to_lyrics(record, song.duration_s)
        return lib.save_lyrics(song.id, lyrics, instrumental=bool(record.get("instrumental"))) or song

    # --- songs --------------------------------------------------------------

    @app.get("/api/songs")
    def list_songs(request: Request) -> list[Song]:
        return library(request).list()

    @app.post("/api/songs")
    async def upload_song(request: Request, file: UploadFile) -> Song:
        lib = library(request)
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Formato no soportado: {ext or 'desconocido'}")
        song_id = lib.new_id()
        folder = lib.song_dir(song_id)
        folder.mkdir(parents=True, exist_ok=True)
        original = folder / f"original{ext}"
        with original.open("wb") as out:
            shutil.copyfileobj(file.file, out)

        meta = read_metadata(original, file.filename)
        audio_file = original.name
        normalized = folder / "audio.mp3"
        if audio_tools.normalize(original, normalized):
            audio_file = normalized.name
        elif ext not in audio_tools.BROWSER_FRIENDLY:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, f"Para usar archivos {ext} hace falta instalar ffmpeg")

        song = lib.add(
            Song(id=song_id, title=meta.title, artist=meta.artist, album=meta.album, duration_s=meta.duration_s),
            audio_file=audio_file,
        )
        song = await attach_lyrics(request, song)
        if config.auto_process and device_mod.processing_available():
            song = request.app.state.queue.enqueue(song.id) or song
        return song

    @app.get("/api/songs/{song_id}")
    def read_song(request: Request, song_id: str) -> Song:
        return get_song(request, song_id)

    @app.patch("/api/songs/{song_id}")
    def patch_song(request: Request, song_id: str, patch: SongPatch) -> Song:
        get_song(request, song_id)
        return library(request).update(song_id, **patch.model_dump(exclude_none=True))  # type: ignore[return-value]

    @app.delete("/api/songs/{song_id}", status_code=204)
    def delete_song(request: Request, song_id: str) -> None:
        get_song(request, song_id)
        library(request).delete(song_id)

    @app.get("/api/songs/{song_id}/audio/{kind}")
    def song_audio(request: Request, song_id: str, kind: str) -> FileResponse:
        get_song(request, song_id)
        lib = library(request)
        if kind == "original":
            path = lib.audio_path(song_id)
        elif kind in ("vocals", "instrumental"):
            path = lib.song_dir(song_id) / f"{kind}.wav"
        else:
            raise HTTPException(404, "Pista desconocida")
        if path is None or not path.exists():
            raise HTTPException(404, "Pista no disponible")
        return FileResponse(path)

    @app.post("/api/songs/{song_id}/process")
    def process_song(request: Request, song_id: str) -> Song:
        song = get_song(request, song_id)
        if song.origin != "file":
            raise HTTPException(400, "Solo se pueden procesar archivos de audio")
        return request.app.state.queue.enqueue(song_id) or song

    @app.get("/api/songs/{song_id}/melody")
    def song_melody(request: Request, song_id: str) -> Melody:
        get_song(request, song_id)
        data = library(request).load_json(song_id, MELODY_FILE)
        if data is None:
            raise HTTPException(404, "La melodía todavía no está disponible")
        return Melody.model_validate(data)

    @app.get("/api/system")
    def system_info(request: Request) -> SystemInfo:
        missing = device_mod.missing_modules()
        return SystemInfo(
            device=device_mod.detect_device() if not missing else "cpu",
            processing_available=not missing,
            missing_modules=missing,
            ffmpeg=audio_tools.ffmpeg_path() is not None,
            https=config.https,
            spotify_configured=request.app.state.spotify.configured,
        )

    # --- lyrics -------------------------------------------------------------

    @app.get("/api/songs/{song_id}/lyrics")
    def song_lyrics(request: Request, song_id: str) -> Lyrics:
        get_song(request, song_id)
        lyrics = library(request).load_lyrics(song_id)
        if lyrics is None:
            raise HTTPException(404, "Esta canción no tiene letra")
        return lyrics

    @app.get("/api/songs/{song_id}/lyrics/search")
    async def search_lyrics(
        request: Request, song_id: str, q: str = "", track: str = "", artist: str = ""
    ) -> list[LrclibCandidate]:
        song = get_song(request, song_id)
        if not (q or track or artist):
            track, artist = song.title, song.artist
        try:
            records = await request.app.state.lrclib.search(q=q, track=track, artist=artist)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"No se pudo consultar LRCLIB: {exc}") from exc
        candidates = [to_candidate(r) for r in records]
        if song.duration_s:
            candidates.sort(key=lambda c: abs((c.duration or 0) - song.duration_s))
        return candidates

    @app.post("/api/songs/{song_id}/lyrics")
    async def choose_lyrics(request: Request, song_id: str, choice: ChooseLyrics) -> Song:
        song = get_song(request, song_id)
        try:
            record = await request.app.state.lrclib.get_by_id(choice.lrclib_id)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"No se pudo consultar LRCLIB: {exc}") from exc
        if record is None:
            raise HTTPException(404, "Letra no encontrada en LRCLIB")
        lyrics = record_to_lyrics(record, song.duration_s)
        return library(request).save_lyrics(song_id, lyrics, instrumental=bool(record.get("instrumental"))) or song

    # --- Spotify ------------------------------------------------------------

    def spotify(request: Request) -> SpotifyService:
        return request.app.state.spotify

    @app.get("/api/spotify/status")
    def spotify_status(request: Request) -> SpotifyStatus:
        s = spotify(request)
        return SpotifyStatus(configured=s.configured, connected=s.connected, redirect_uri=s.redirect_uri)

    @app.get("/api/spotify/login", include_in_schema=False)
    def spotify_login(request: Request) -> RedirectResponse:
        try:
            return RedirectResponse(spotify(request).authorize_url())
        except SpotifyError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/spotify/callback", include_in_schema=False)
    async def spotify_callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        if error or not code:
            return RedirectResponse(f"/host/#/?spotify_error={error or 'cancelado'}")
        try:
            await spotify(request).finish_login(code, state)
        except (SpotifyError, httpx.HTTPError) as exc:
            log.warning("Spotify login failed: %s", exc)
            return RedirectResponse("/host/#/?spotify_error=login")
        return RedirectResponse("/host/#/")

    @app.post("/api/spotify/logout", status_code=204)
    def spotify_logout(request: Request) -> None:
        spotify(request).logout()

    @app.get("/api/spotify/token")
    async def spotify_token(request: Request) -> SpotifyToken:
        try:
            token, expires_at = await spotify(request).access_token()
        except (SpotifyError, httpx.HTTPError) as exc:
            raise HTTPException(401, str(exc)) from exc
        return SpotifyToken(access_token=token, expires_at=expires_at)

    @app.get("/api/spotify/search")
    async def spotify_search(request: Request, q: str) -> list[SpotifyTrack]:
        try:
            return await spotify(request).search(q)
        except (SpotifyError, httpx.HTTPError) as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/spotify/songs")
    async def spotify_add(request: Request, body: AddSpotifyTrack) -> Song:
        lib = library(request)
        uri = f"spotify:track:{body.track_id}"
        existing = next((s for s in lib.list() if s.spotify_uri == uri), None)
        if existing:
            return existing
        try:
            track = await spotify(request).track(body.track_id)
        except (SpotifyError, httpx.HTTPError) as exc:
            raise HTTPException(502, str(exc)) from exc
        artist = track.artists.split(", ")[0] if track.artists else ""
        song = lib.add(
            Song(
                id=lib.new_id(),
                origin="spotify",
                title=track.name,
                artist=artist,
                album=track.album,
                duration_s=track.duration_ms / 1000,
                spotify_uri=track.uri,
            )
        )
        return await attach_lyrics(request, song)

    # --- room ---------------------------------------------------------------

    @app.get("/api/room")
    def room_state(request: Request) -> RoomMsg:
        return request.app.state.room.state()

    @app.get("/api/room/qr.svg", include_in_schema=False)
    def room_qr(request: Request) -> Response:
        buf = io.BytesIO()
        segno.make(request.app.state.room.mic_url, error="m").save(
            buf, kind="svg", scale=8, border=2, dark="#12101c", light="#ffffff"
        )
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    # --- frontend -----------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/host/")

    @app.get("/mic", include_in_schema=False)
    def mic(request: Request) -> RedirectResponse:
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/mic/{query}")

    if config.web_dist.exists():
        app.mount("/", StaticFiles(directory=config.web_dist, html=True), name="web")

    return app
