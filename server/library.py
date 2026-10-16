"""Song library: metadata in SQLite, heavy files under data/songs/<id>/."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from server.models import Lyrics, Song

_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "origin": "TEXT NOT NULL DEFAULT 'file'",
    "title": "TEXT NOT NULL",
    "artist": "TEXT NOT NULL DEFAULT ''",
    "album": "TEXT NOT NULL DEFAULT ''",
    "duration_s": "REAL NOT NULL DEFAULT 0",
    "status": "TEXT NOT NULL DEFAULT 'imported'",
    "error": "TEXT",
    "progress": "REAL NOT NULL DEFAULT 0",
    "progress_label": "TEXT NOT NULL DEFAULT ''",
    "offset_ms": "INTEGER NOT NULL DEFAULT 0",
    "lyrics_source": "TEXT",
    "instrumental": "INTEGER NOT NULL DEFAULT 0",
    "has_stems": "INTEGER NOT NULL DEFAULT 0",
    "has_melody": "INTEGER NOT NULL DEFAULT 0",
    "spotify_uri": "TEXT",
    "created_at": "REAL NOT NULL DEFAULT 0",
    "audio_file": "TEXT",
}

LYRICS_FILE = "lyrics.json"
MELODY_FILE = "melody.json"
VOCALS_FILE = "vocals.wav"
INSTRUMENTAL_FILE = "instrumental.wav"


class Library:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.songs_dir = data_dir / "songs"
        self.songs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(data_dir / "library.sqlite3", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._db:
            cols = ", ".join(f"{name} {kind}" for name, kind in _COLUMNS.items())
            self._db.execute(f"CREATE TABLE IF NOT EXISTS songs ({cols})")
            existing = {r["name"] for r in self._db.execute("PRAGMA table_info(songs)")}
            for name, kind in _COLUMNS.items():
                if name not in existing:
                    self._db.execute(f"ALTER TABLE songs ADD COLUMN {name} {kind}")
            self._db.execute(
                "UPDATE songs SET status = 'error', error = 'Descarga interrumpida: bórrala y vuelve a buscarla',"
                " progress = 0, progress_label = '' WHERE status = 'downloading'"
            )
            # Jobs interrupted by a restart go back to the queue-able state.
            self._db.execute(
                "UPDATE songs SET status = CASE WHEN lyrics_source IS NULL THEN 'imported' ELSE 'lyrics_ready' END,"
                " progress = 0, progress_label = '' WHERE status = 'processing'"
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- rows ---------------------------------------------------------------

    @staticmethod
    def _to_song(row: sqlite3.Row) -> Song:
        data = {k: row[k] for k in row.keys() if k in Song.model_fields}
        for flag in ("instrumental", "has_stems", "has_melody"):
            data[flag] = bool(data[flag])
        data["has_audio"] = bool(row["audio_file"]) or row["origin"] == "spotify"
        return Song(**data)

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def song_dir(self, song_id: str) -> Path:
        return self.songs_dir / song_id

    def add(self, song: Song, audio_file: str | None = None) -> Song:
        song.created_at = song.created_at or time.time()
        data = song.model_dump(exclude={"has_audio"})
        data["audio_file"] = audio_file
        with self._lock, self._db:
            keys = ", ".join(data)
            marks = ", ".join(f":{k}" for k in data)
            self._db.execute(f"INSERT INTO songs ({keys}) VALUES ({marks})", data)
        return song

    def get(self, song_id: str) -> Song | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        return self._to_song(row) if row else None

    def list(self) -> list[Song]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM songs ORDER BY created_at DESC").fetchall()
        return [self._to_song(r) for r in rows]

    def update(self, song_id: str, **fields: Any) -> Song | None:
        fields = {k: v for k, v in fields.items() if k in _COLUMNS and k != "id"}
        if fields:
            sets = ", ".join(f"{k} = :{k}" for k in fields)
            with self._lock, self._db:
                self._db.execute(f"UPDATE songs SET {sets} WHERE id = :id", {**fields, "id": song_id})
        return self.get(song_id)

    def delete(self, song_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
        shutil.rmtree(self.song_dir(song_id), ignore_errors=True)

    def audio_path(self, song_id: str) -> Path | None:
        with self._lock:
            row = self._db.execute("SELECT audio_file FROM songs WHERE id = ?", (song_id,)).fetchone()
        if not row or not row["audio_file"]:
            return None
        path = self.song_dir(song_id) / row["audio_file"]
        return path if path.exists() else None

    # --- files --------------------------------------------------------------

    def save_lyrics(self, song_id: str, lyrics: Lyrics | None, instrumental: bool = False) -> Song | None:
        path = self.song_dir(song_id) / LYRICS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        if lyrics is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(lyrics.model_dump_json(), encoding="utf-8")
        song = self.get(song_id)
        if song is None:
            return None
        status = song.status
        if status in ("imported", "lyrics_ready"):
            status = "lyrics_ready" if (lyrics is not None or instrumental) else "imported"
        return self.update(
            song_id,
            lyrics_source=lyrics.source if lyrics else None,
            instrumental=int(instrumental),
            status=status,
        )

    def load_lyrics(self, song_id: str) -> Lyrics | None:
        path = self.song_dir(song_id) / LYRICS_FILE
        if not path.exists():
            return None
        return Lyrics.model_validate_json(path.read_text(encoding="utf-8"))

    def save_json(self, song_id: str, name: str, data: Any) -> None:
        path = self.song_dir(song_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    def load_json(self, song_id: str, name: str) -> Any | None:
        path = self.song_dir(song_id) / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
