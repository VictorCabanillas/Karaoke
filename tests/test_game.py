from server.session import now_ms
from tests.conftest import make_wav
from tests.test_room import receive_until

MIC_LATENCY = 100
SONG_END = 14900


def upload_song(client, seconds=15):
    return client.post(
        "/api/songs", files={"file": ("Grupo Inventado - Cancion de Prueba.wav", make_wav(seconds), "audio/wav")}
    ).json()


def join(client, code, name, color):
    ws = client.websocket_connect(f"/ws/mic?room={code}")
    mic = ws.__enter__()
    mic.send_json({"type": "hello", "name": name, "color": color})
    player = receive_until(mic, "welcome")["player"]
    return ws, mic, player


class SongTimeline:
    """Song position 0 is placed a minute in the future, so no window gets scored while frames are sent."""

    def __init__(self, host, song_id, first_mic):
        self.host = host
        self.song_id = song_id
        self.t0 = now_ms() + 60_000
        host.send_json({"type": "playback", "song_id": song_id, "playing": True, "position_ms": 0, "server_ms": self.t0})
        receive_until(first_mic, "status")  # the server has the new clock
        self.sent = 0

    def send(self, mic, song_ms_values, hz):
        frames = [{"t": self.t0 + ms + MIC_LATENCY, "f0": hz, "conf": 0.95, "rms": 0.2} for ms in song_ms_values]
        for start in range(0, len(frames), 100):
            mic.send_json({"type": "pitch", "frames": frames[start : start + 100]})
        self.sent += len(frames)

    def finish(self):
        """Wait until the host saw every frame, jump to the end of the song and stop the game."""
        seen = 0
        while seen < self.sent:
            msg = receive_until(self.host, "pitch_live")
            seen += sum(len(p["frames"]) for p in msg["players"])
        self.host.send_json(
            {"type": "playback", "song_id": self.song_id, "playing": True, "position_ms": SONG_END, "server_ms": now_ms()}
        )
        self.host.send_json({"type": "stop_game"})
        return receive_until(self.host, "results")


def test_competition_with_lyrics_only_is_simplified_and_ranks_players(client):
    song = upload_song(client)
    code = client.get("/api/room").json()["code"]
    with client.websocket_connect("/ws/host") as host:
        ws1, singer, p1 = join(client, code, "Canta", "#ff4f9a")
        ws2, quiet, _ = join(client, code, "Calla", "#4fd8ff")
        try:
            host.send_json({"type": "start_game", "song_id": song["id"], "mode": "competition"})
            state = receive_until(host, "game")
            assert state["mode"] == "competition" and state["simplified"]
            assert receive_until(singer, "game")["mode"] == "competition"
            assert len(receive_until(host, "scores")["entries"]) == 2

            timeline = SongTimeline(host, song["id"], singer)
            timeline.send(singer, range(1000, 8000, 20), 262.0)  # first two lines
            timeline.send(quiet, range(1000, 8000, 20), 0.0)  # no pitch
            results = timeline.finish()

            assert results["simplified"]
            ranking = results["ranking"]
            assert [e["name"] for e in ranking] == ["Canta", "Calla"]
            assert ranking[0]["score"] > 5000 and ranking[1]["score"] == 0
            assert any(a["title"] == "Pulmones de acero" and a["name"] == "Canta" for a in results["awards"])
            assert receive_until(singer, "results")["ranking"][0]["player_id"] == p1["id"]
            assert receive_until(host, "game")["mode"] == "free"
        finally:
            ws1.__exit__(None, None, None)
            ws2.__exit__(None, None, None)


def test_competition_uses_melody_when_processed(client):
    song = upload_song(client)
    library = client.app.state.library
    library.save_json(song["id"], "melody.json", {"hop_ms": 10, "midi": [60.0] * 1500})
    library.update(song["id"], has_melody=1, status="processed")
    code = client.get("/api/room").json()["code"]
    with client.websocket_connect("/ws/host") as host:
        ws1, singer, _ = join(client, code, "Afinado", "#ff4f9a")
        ws2, wrong, _ = join(client, code, "Desafinado", "#4fd8ff")
        try:
            host.send_json({"type": "start_game", "song_id": song["id"], "mode": "competition"})
            assert receive_until(host, "game")["simplified"] is False
            timeline = SongTimeline(host, song["id"], singer)
            timeline.send(singer, range(0, SONG_END, 20), 523.25)  # C5: the reference C4 an octave up
            timeline.send(wrong, range(0, SONG_END, 20), 370.0)  # F#4: a tritone away
            ranking = timeline.finish()["ranking"]
            assert ranking[0]["name"] == "Afinado" and ranking[0]["score"] > 9500
            assert ranking[1]["score"] < 4000
        finally:
            ws1.__exit__(None, None, None)
            ws2.__exit__(None, None, None)


def test_competition_needs_lyrics_or_melody(client, fake_lrclib):
    fake_lrclib.records = []
    song = upload_song(client)
    with client.websocket_connect("/ws/host") as host:
        host.send_json({"type": "start_game", "song_id": song["id"], "mode": "competition"})
        assert "letra sincronizada" in receive_until(host, "error")["message"]
        assert receive_until(host, "game")["mode"] == "free"
