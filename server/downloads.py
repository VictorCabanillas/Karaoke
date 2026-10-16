"""Search songs online and download their audio with yt-dlp (optional dependency).

Downloads run one at a time in a background thread; the finished file goes through the same import
pipeline as an uploaded file (metadata, normalization, lyrics, processing queue).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from server.library import Library
from server.models import OnlineTrack, Song, SongUpdatedMsg
from server.session import HostHub

log = logging.getLogger(__name__)

SEARCH_LIMIT = 10
# Only reasonable song lengths (skips 10-hour mixes and shorts).
MIN_DURATION_S = 30
MAX_DURATION_S = 20 * 60

_NOISE = re.compile(
    r"\s*[\(\[](?:[^\)\]]*\b(?:official|oficial|video|v[ií]deo|lyrics?|letra|audio|hd|hq|4k|visuali[sz]er|"
    r"videoclip|clip|remaster(?:ed)?|live|en vivo|directo)\b[^\)\]]*)[\)\]]",
    re.IGNORECASE,
)
_CHANNEL_SUFFIX = re.compile(r"\s*(?:-\s*Topic|VEVO|Official|Oficial)$", re.IGNORECASE)


def available() -> bool:
    return importlib.util.find_spec("yt_dlp") is not None


def guess_artist_title(video_title: str, channel: str = "") -> tuple[str, str]:
    """'Artist - Song (Official Video)' -> ('Artist', 'Song'); falls back to the channel name."""
    title = _NOISE.sub("", video_title).strip()
    title = re.sub(r"\s*\|.*$", "", title).strip()
    for sep in (" - ", " – ", " — ", " ~ ", ": "):
        if sep in title:
            artist, song = title.split(sep, 1)
            return artist.strip(), song.strip(" \"'")
    return _CHANNEL_SUFFIX.sub("", channel or "").strip(), title.strip(" \"'")


def track_metadata(info: dict[str, Any]) -> dict[str, Any]:
    """Title/artist/album/duration from yt-dlp's info (proper music metadata when YouTube has it)."""
    artist = info.get("artist") or ", ".join(info.get("artists") or []) or ""
    title = info.get("track") or ""
    if not (artist and title):
        guessed_artist, guessed_title = guess_artist_title(info.get("title") or "", info.get("channel") or "")
        artist = artist or guessed_artist
        title = title or guessed_title
    return {
        "title": title,
        "artist": artist.split(",")[0].strip(),
        "album": info.get("album") or "",
        "duration": float(info.get("duration") or 0),
    }


class YouTubeSource:
    """yt-dlp wrapper. Every method blocks: call from a worker thread."""

    def _ydl(self, **options):
        import yt_dlp

        return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True, **options})

    def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[OnlineTrack]:
        with self._ydl(skip_download=True, extract_flat="in_playlist") as ydl:
            info = ydl.extract_info(f"ytsearch{limit * 2}:{query}", download=False)
        tracks: list[OnlineTrack] = []
        for entry in info.get("entries") or []:
            duration = entry.get("duration")
            if not entry.get("id") or entry.get("live_status") in ("is_live", "is_upcoming"):
                continue
            if duration is not None and not (MIN_DURATION_S <= duration <= MAX_DURATION_S):
                continue
            thumbs = entry.get("thumbnails") or []
            tracks.append(
                OnlineTrack(
                    id=entry["id"],
                    title=entry.get("title") or "",
                    channel=entry.get("channel") or entry.get("uploader") or "",
                    duration_s=float(duration) if duration else None,
                    thumbnail=thumbs[0]["url"] if thumbs else None,
                )
            )
        return tracks[:limit]

    def download(self, video_id: str, folder: Path, on_progress: Callable[[float], None]) -> tuple[Path, dict]:
        def hook(status: dict) -> None:
            if status.get("status") == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                if total:
                    on_progress(min(1.0, status.get("downloaded_bytes", 0) / total))

        options = {
            # m4a (AAC) plays in every browser and needs no conversion.
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(folder / "original.%(ext)s"),
            "noplaylist": True,
            "progress_hooks": [hook],
        }
        with self._ydl(**options) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        path = Path(info.get("requested_downloads", [{}])[0].get("filepath") or ydl.prepare_filename(info))
        return path, info


ImportAudio = Callable[[str, Path, str, dict], Awaitable[Song]]


class DownloadManager:
    def __init__(self, library: Library, hub: HostHub, import_audio: ImportAudio, source: YouTubeSource | None = None):
        self.library = library
        self.hub = hub
        self.import_audio = import_audio
        self.source = source or YouTubeSource()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="download")
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self) -> None:
        self._loop = asyncio.get_running_loop()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def search(self, query: str) -> list[OnlineTrack]:
        return await asyncio.to_thread(self.source.search, query)

    def start(self, song: Song, video_id: str) -> None:
        self._executor.submit(self._run, song.id, video_id)

    def _set(self, song_id: str, **fields) -> None:
        song = self.library.update(song_id, **fields)
        if song is not None:
            self.hub.broadcast_threadsafe(SongUpdatedMsg(song=song))

    def _run(self, song_id: str, video_id: str) -> None:
        if self.library.get(song_id) is None:
            return  # deleted while queued
        folder = self.library.song_dir(song_id)
        folder.mkdir(parents=True, exist_ok=True)
        last = [-1.0]

        def progress(fraction: float) -> None:
            if fraction - last[0] >= 0.02 or fraction >= 1.0:
                last[0] = fraction
                self._set(song_id, progress=round(fraction, 3), progress_label=f"Descargando {round(fraction * 100)}%")

        try:
            self._set(song_id, progress=0.0, progress_label="Descargando")
            path, info = self.source.download(video_id, folder, progress)
            if self.library.get(song_id) is None:
                shutil.rmtree(folder, ignore_errors=True)
                return
            self._set(song_id, progress=1.0, progress_label="Preparando")
            assert self._loop is not None
            future = asyncio.run_coroutine_threadsafe(
                self.import_audio(song_id, path, path.name, track_metadata(info)), self._loop
            )
            future.result()
        except Exception as exc:
            log.exception("Download of %s failed", video_id)
            if self.library.get(song_id) is not None:
                self._set(song_id, status="error", error=f"No se pudo descargar: {exc}", progress=0.0, progress_label="")
