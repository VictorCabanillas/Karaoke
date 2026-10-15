"""Read title/artist/album/duration from audio files with mutagen."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import mutagen


@dataclass
class AudioMetadata:
    title: str
    artist: str
    album: str
    duration_s: float


def _first(tags, *keys: str) -> str:
    if not tags:
        return ""
    for key in keys:
        try:
            value = tags.get(key)
        except (KeyError, ValueError):
            continue
        if value:
            if isinstance(value, list):
                value = value[0]
            return str(value).strip()
    return ""


def guess_from_filename(filename: str) -> tuple[str, str]:
    """'Artist - Title.mp3' → ('Artist', 'Title'); otherwise ('', stem)."""
    stem = Path(filename).stem
    stem = re.sub(r"^\d{1,3}[\s._-]+", "", stem)  # leading track numbers
    stem = stem.replace("_", " ").strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem


def read_metadata(path: Path, original_filename: str | None = None) -> AudioMetadata:
    try:
        audio = mutagen.File(path, easy=True)
    except mutagen.MutagenError:
        audio = None
    tags = audio.tags if audio is not None else None
    title = _first(tags, "title")
    artist = _first(tags, "artist", "albumartist")
    album = _first(tags, "album")
    duration = float(getattr(getattr(audio, "info", None), "length", 0.0) or 0.0)
    if not title:
        guessed_artist, guessed_title = guess_from_filename(original_filename or path.name)
        title = guessed_title
        artist = artist or guessed_artist
    return AudioMetadata(title=title, artist=artist, album=album, duration_s=duration)
