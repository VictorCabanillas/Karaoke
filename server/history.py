"""Competition history and rankings (data/history.sqlite3). Players are identified by their name."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from server.models import GameRecord, PlayerRanking, Rankings, ResultsMsg, SongRecord


def player_key(name: str) -> str:
    return " ".join(name.lower().split())


class History:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS games (id INTEGER PRIMARY KEY AUTOINCREMENT, song_id TEXT NOT NULL,"
                " song_title TEXT NOT NULL, played_at REAL NOT NULL, data TEXT NOT NULL)"
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def record(self, song_id: str, results: ResultsMsg, played_at: float | None = None) -> GameRecord | None:
        """Store a finished game (games where nobody scored are not worth keeping)."""
        if not any(e.score > 0 for e in results.ranking):
            return None
        game = GameRecord(
            id=0,
            song_id=song_id,
            song_title=results.song_title,
            played_at=played_at or time.time(),
            simplified=results.simplified,
            ranking=results.ranking,
            awards=results.awards,
        )
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO games (song_id, song_title, played_at, data) VALUES (?, ?, ?, ?)",
                (song_id, game.song_title, game.played_at, game.model_dump_json()),  # id is replaced on read
            )
        game.id = cur.lastrowid or 0
        return game

    def games(self, limit: int = 100) -> list[GameRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, data FROM games ORDER BY played_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [GameRecord.model_validate_json(data).model_copy(update={"id": gid}) for gid, data in rows]

    def clear(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM games")

    def song_record(self, song_id: str) -> SongRecord | None:
        best: SongRecord | None = None
        for game in self._all(song_id):
            if game.ranking and (best is None or game.ranking[0].score > best.score):
                top = game.ranking[0]
                best = SongRecord(song_id=game.song_id, song_title=game.song_title, name=top.name,
                                  color=top.color, score=top.score, played_at=game.played_at)  # fmt: skip
        return best

    def _all(self, song_id: str | None = None) -> list[GameRecord]:
        with self._lock:
            if song_id is None:
                rows = self._db.execute("SELECT id, data FROM games ORDER BY played_at").fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, data FROM games WHERE song_id = ? ORDER BY played_at", (song_id,)
                ).fetchall()
        return [GameRecord.model_validate_json(data).model_copy(update={"id": gid}) for gid, data in rows]

    def rankings(self) -> Rankings:
        players: dict[str, dict] = {}
        records: dict[str, SongRecord] = {}
        for game in self._all():
            for position, entry in enumerate(game.ranking):
                p = players.setdefault(player_key(entry.name), {"games": 0, "wins": 0, "best": 0, "total": 0})
                p.update(name=entry.name, color=entry.color)  # the latest spelling and colour win
                p["games"] += 1
                p["total"] += entry.score
                p["best"] = max(p["best"], entry.score)
                # A win needs someone to beat.
                if position == 0 and len(game.ranking) > 1 and entry.score > game.ranking[1].score:
                    p["wins"] += 1
            if game.ranking:
                top = game.ranking[0]
                current = records.get(game.song_id)
                if current is None or top.score > current.score:
                    records[game.song_id] = SongRecord(song_id=game.song_id, song_title=game.song_title,
                                                       name=top.name, color=top.color, score=top.score,
                                                       played_at=game.played_at)  # fmt: skip
        ranking = [
            PlayerRanking(
                name=p["name"],
                color=p["color"],
                games=p["games"],
                wins=p["wins"],
                best_score=p["best"],
                average_score=round(p["total"] / p["games"]),
            )
            for p in players.values()
        ]
        ranking.sort(key=lambda r: (r.wins, r.average_score), reverse=True)
        return Rankings(players=ranking, records=sorted(records.values(), key=lambda r: r.song_title.lower()))
