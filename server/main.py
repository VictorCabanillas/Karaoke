"""FastAPI application: REST API, WebSockets and the compiled frontend."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
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
from processing.lrc import plain_lyrics
from processing.lrclib import LrclibClient, find_lyrics, record_to_lyrics, to_candidate
from processing.metadata import read_metadata
from processing.queue import Pipeline, ProcessingQueue
from server import downloads as downloads_mod
from server import ws_host, ws_mic
from server.config import Settings, lan_ip, settings as default_settings
from server.game import GameController
from server.spotify import SpotifyError, SpotifyService
from server.library import MELODY_FILE, Library
from server.models import (
    AddSpotifyTrack,
    ChooseLyrics,
    DownloadRequest,
    LrclibCandidate,
    Lyrics,
    LyricsText,
    Melody,
    OnlineTrack,
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


def exit_now() -> None:
    """End the process immediately, skipping the interpreter's teardown.

    A Demucs/Whisper job on the GPU or a running download cannot be interrupted, and on Windows exiting
    normally while one runs hangs forever while the CUDA libraries unload ("Shutting down" never ends).
    By the time this is called everything is saved and closed.
    """
    log.warning("Trabajo en segundo plano interrumpido: se retomará al volver a arrancar el servidor")
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0)
    os._exit(0)


class ImportError_(Exception):
    """The audio file cannot be used (message for the user)."""


def create_app(
    config: Settings | None = None,
    lrclib: LrclibClient | None = None,
    pipeline: Pipeline | None = None,
    spotify: SpotifyService | None = None,
    online_source: downloads_mod.YouTubeSource | None = None,
    suggest: downloads_mod.SuggestClient | None = None,
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
        if device_mod.processing_available():
            for song_id in app.state.library.interrupted:
                app.state.queue.enqueue(song_id)
        scheme = "https" if config.https else "http"
        app.state.room = Room(
            code=Room.load_or_create_code(config.data_dir / "room.txt"),
            hub=app.state.hub,
            mic_url=lambda code: f"{scheme}://{lan_ip()}:{config.public_port}/mic/?room={code}",
            https=config.https,
            default_latency_ms=config.default_mic_latency_ms,
        )
        app.state.game = GameController(app.state.room, app.state.library)
        app.state.downloads = downloads_mod.DownloadManager(
            app.state.library, app.state.hub, import_audio, online_source
        )
        app.state.downloads.bind_loop()
        app.state.suggest = suggest or downloads_mod.SuggestClient()
        app.state.room.start()
        yield
        busy = app.state.queue.busy or app.state.downloads.busy
        app.state.downloads.shutdown()
        await app.state.suggest.close()
        await app.state.room.stop()
        app.state.queue.stop(timeout=0 if busy else 5)
        app.state.library.close()
        if busy and config.hard_exit:
            exit_now()

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

    async def attach_lyrics(song: Song) -> Song:
        lib: Library = app.state.library
        try:
            record = await find_lyrics(app.state.lrclib, song.title, song.artist, song.album, song.duration_s)
        except httpx.HTTPError as exc:
            log.warning("LRCLIB lookup failed for %s: %s", song.id, exc)
            return song
        if record is None:
            return song
        lyrics = record_to_lyrics(record, song.duration_s)
        return lib.save_lyrics(song.id, lyrics, instrumental=bool(record.get("instrumental"))) or song

    async def import_audio(song_id: str, original: Path, filename: str, hint: dict | None = None) -> Song:
        """Shared by uploads and downloads: metadata, normalization, lyrics lookup, processing queue."""
        lib: Library = app.state.library
        hint = hint or {}
        meta = read_metadata(original, filename)
        audio_file = original.name
        normalized = original.parent / "audio.mp3"
        # ffmpeg can take a while: never block the event loop (phones stay connected meanwhile).
        if await asyncio.to_thread(audio_tools.normalize, original, normalized):
            audio_file = normalized.name
        elif original.suffix.lower() not in audio_tools.BROWSER_FRIENDLY:
            raise ImportError_(f"Para usar archivos {original.suffix.lower()} hace falta instalar ffmpeg")
        fields = dict(
            title=hint.get("title") or meta.title,
            artist=hint.get("artist") or meta.artist,
            album=hint.get("album") or meta.album,
            duration_s=meta.duration_s or hint.get("duration") or 0.0,
        )
        if lib.get(song_id) is None:
            song = lib.add(Song(id=song_id, **fields), audio_file=audio_file)
        else:
            song = lib.update(
                song_id, **fields, audio_file=audio_file, status="imported", progress=0.0, progress_label="", error=None
            )
        song = await attach_lyrics(song)
        if config.auto_process and device_mod.processing_available():
            song = app.state.queue.enqueue(song.id) or song
        await app.state.hub.broadcast(SongUpdatedMsg(song=song))
        return song

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
            await asyncio.to_thread(shutil.copyfileobj, file.file, out)
        try:
            return await import_audio(song_id, original, file.filename or original.name)
        except ImportError_ as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, str(exc)) from exc

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
            downloads_available=online_source is not None or downloads_mod.available(),
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

    @app.put("/api/songs/{song_id}/lyrics")
    def save_edited_lyrics(request: Request, song_id: str, lyrics: Lyrics) -> Song:
        """Lyrics edited by hand in the editor."""
        song = get_song(request, song_id)
        lines = [line for line in lyrics.lines if any(w.text.strip() for w in line.words)]
        if not lines:
            raise HTTPException(422, "La letra está vacía")
        if lyrics.synced:
            for i, line in enumerate(lines):
                if line.end_ms < line.start_ms or (i and line.start_ms < lines[i - 1].start_ms):
                    raise HTTPException(422, f"Los tiempos de la línea {i + 1} no van en orden")
                for w in line.words:
                    w.start_ms = min(max(w.start_ms, line.start_ms), line.end_ms)
                    w.end_ms = min(max(w.end_ms, w.start_ms), line.end_ms)
            edited = Lyrics(source="manual", synced=True, lines=lines)
        else:
            edited = Lyrics(source="lrclib_lines_only", synced=False, lines=lines)
        return library(request).save_lyrics(song_id, edited, instrumental=song.instrumental) or song

    @app.post("/api/songs/{song_id}/lyrics/text")
    def paste_lyrics(request: Request, song_id: str, body: LyricsText) -> Song:
        """Plain lyrics pasted by the user; aligned with the vocals when possible."""
        song = get_song(request, song_id)
        lyrics = plain_lyrics(body.text)
        if not lyrics.lines:
            raise HTTPException(422, "La letra está vacía")
        song = library(request).save_lyrics(song_id, lyrics, instrumental=False) or song
        if body.align and song.origin == "file" and device_mod.processing_available():
            song = request.app.state.queue.enqueue(song_id) or song
        return song

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
        return await attach_lyrics(song)

    # --- online search & download --------------------------------------------

    def require_downloads() -> None:
        if online_source is None and not downloads_mod.available():
            raise HTTPException(503, "Falta yt-dlp: ejecuta «uv sync» (o setup) y reinicia el servidor")

    @app.get("/api/online/search")
    async def online_search(request: Request, q: str) -> list[OnlineTrack]:
        require_downloads()
        if not q.strip():
            return []
        try:
            return await request.app.state.downloads.search(q.strip())
        except Exception as exc:  # network errors, YouTube changes...
            log.warning("Online search failed: %s", exc)
            raise HTTPException(502, f"La búsqueda falló: {exc}") from exc

    @app.get("/api/online/suggest")
    async def online_suggest(request: Request, q: str = "") -> list[str]:
        return await request.app.state.suggest.suggest(q)

    @app.post("/api/online/download")
    def online_download(request: Request, body: DownloadRequest) -> Song:
        require_downloads()
        lib = library(request)
        artist, title = downloads_mod.guess_artist_title(body.title, body.channel)
        song = lib.add(
            Song(
                id=lib.new_id(),
                title=title or body.title or "Descargando…",
                artist=artist,
                status="downloading",
                progress_label="En cola",
            )
        )
        request.app.state.downloads.start(song, body.video_id)
        return lib.get(song.id) or song

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
