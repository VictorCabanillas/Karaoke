from server.history import History
from server.models import AwardEntry, ResultsMsg, ScoreEntry


def results(*scores, title="Cancion"):
    ranking = [ScoreEntry(player_id=f"p{i}", name=name, color="#ff4f9a", score=score, combo=0)
               for i, (name, score) in enumerate(scores)]  # fmt: skip
    ranking.sort(key=lambda e: e.score, reverse=True)
    return ResultsMsg(song_title=title, simplified=False, ranking=ranking,
                      awards=[AwardEntry(title="Más afinado", player_id="p0", name=scores[0][0], detail="x")])  # fmt: skip


def test_history_rankings_and_records(tmp_path):
    h = History(tmp_path / "h.sqlite3")
    h.record("s1", results(("Ana", 8000), ("Leo", 6000)), played_at=1)
    h.record("s1", results(("ana ", 9000), ("Leo", 9500)), played_at=2)  # same player, other spelling
    h.record("s2", results(("Leo", 7000), title="Otra"), played_at=3)  # alone: no win
    assert h.record("s2", results(("Leo", 0)), played_at=4) is None  # nobody scored: not stored

    assert [g.played_at for g in h.games()] == [3, 2, 1]
    ranking = {p.name.strip().lower(): p for p in h.rankings().players}
    assert (ranking["ana"].games, ranking["ana"].wins, ranking["ana"].best_score) == (2, 1, 9000)
    assert (ranking["leo"].games, ranking["leo"].wins, ranking["leo"].average_score) == (3, 1, 7500)
    records = {r.song_id: r for r in h.rankings().records}
    assert (records["s1"].name, records["s1"].score) == ("Leo", 9500)
    assert h.song_record("s2").score == 7000 and h.song_record("nope") is None
    h.clear()
    assert h.games() == []
    h.close()


def test_settings_store(tmp_path):
    from server.app_settings import SettingsStore
    from server.models import AppSettings

    path = tmp_path / "settings.json"
    store = SettingsStore(path, AppSettings(whisper_model="small"))
    assert store.get().whisper_model == "small"
    store.update(AppSettings(whisper_model="large-v3"))
    assert SettingsStore(path).get().whisper_model == "large-v3"
    path.write_text("{broken", encoding="utf-8")
    assert SettingsStore(path).get().whisper_model == ""


def test_settings_history_and_reprocess_endpoints(client):
    assert client.get("/api/settings").json() == {"whisper_model": ""}
    assert client.put("/api/settings", json={"whisper_model": "large-v3-turbo"}).json()["whisper_model"] == "large-v3-turbo"
    assert client.put("/api/settings", json={"whisper_model": "enorme"}).status_code == 422
    assert client.get("/api/history").json() == []
    assert client.get("/api/rankings").json() == {"players": [], "records": []}
    assert client.delete("/api/history").status_code == 204
