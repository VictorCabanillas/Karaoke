"""FastAPI application: REST API, WebSockets and the compiled frontend."""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from processing import audio as audio_tools
from processing.lrclib import LrclibClient, find_lyrics, record_to_lyrics, to_candidate
from processing.metadata import read_metadata
from server.config import Settings, settings as default_settings
from server.library import Library
from server.models import ChooseLyrics, LrclibCandidate, Lyrics, Song, SongPatch

log = logging.getLogger("karaoke")

ALLOWED_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".webm", ".wma"}


def create_app(config: Settings | None = None, lrclib: LrclibClient | None = None) -> FastAPI:
    config = config or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        app.state.library = Library(config.data_dir)
        yield
        app.state.library.close()

    app = FastAPI(title="Karaoke entre amigos", lifespan=lifespan)
    app.state.config = config
    app.state.lrclib = lrclib or LrclibClient()

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
        return await attach_lyrics(request, song)

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
