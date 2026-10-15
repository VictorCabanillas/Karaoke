import pytest
from starlette.websockets import WebSocketDisconnect

from server.models import PlaybackState
from server.session import SongClock, now_ms
from tests.conftest import make_wav


def receive_until(ws, msg_type, where=lambda msg: True, limit=50):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == msg_type and where(msg):
            return msg
    raise AssertionError(f"no {msg_type} message")


def room_code(client):
    return client.get("/api/room").json()["code"]


def test_song_clock():
    clock = SongClock()
    assert clock.at(1000) is None
    clock.update(PlaybackState(song_id="x", playing=True, position_ms=5000, server_ms=1000))
    assert clock.at(1500) == 5500
    clock.update(PlaybackState(song_id="x", playing=False, position_ms=5000, server_ms=1000))
    assert clock.at(1500) is None


def test_room_info_and_qr(client):
    room = client.get("/api/room").json()
    assert len(room["code"]) == 4
    assert room["mic_url"].endswith(f"/mic/?room={room['code']}")
    qr = client.get("/api/room/qr.svg")
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg+xml")


def test_wrong_room_code_is_rejected(client):
    with client.websocket_connect("/ws/mic?room=ZZZZ") as ws:
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_join_ping_calibrate_and_reconnect(client):
    code = room_code(client)
    with client.websocket_connect("/ws/host") as host:
        assert receive_until(host, "room")["players"] == []
        with client.websocket_connect(f"/ws/mic?room={code.lower()}") as mic:
            mic.send_json({"type": "hello", "name": "Ana", "color": "#ff4f9a"})
            welcome = receive_until(mic, "welcome")
            player = welcome["player"]
            assert player["name"] == "Ana" and player["connected"] and not player["calibrated"]
            assert receive_until(host, "room")["players"][0]["id"] == player["id"]

            before = now_ms()
            mic.send_json({"type": "ping", "t0": 123.5})
            pong = receive_until(mic, "pong")
            assert pong["t0"] == 123.5 and pong["t_server"] >= before

            mic.send_json({"type": "calibration", "noise_rms": 0.02, "latency_ms": 150})
            calibrated = receive_until(host, "room", lambda m: m["players"][0]["calibrated"])["players"][0]
            assert calibrated["latency_ms"] == 150

        receive_until(host, "room", lambda m: m["players"][0]["connected"] is False)

        with client.websocket_connect(f"/ws/mic?room={code}") as mic:
            mic.send_json({"type": "hello", "name": "Ana B", "color": "#4fd8ff", "player_id": player["id"]})
            again = receive_until(mic, "welcome")["player"]
            assert again["id"] == player["id"] and again["name"] == "Ana B" and again["calibrated"]

        host.send_json({"type": "remove_player", "player_id": player["id"]})
        receive_until(host, "room", lambda m: m["players"] == [])


def test_invalid_hello_and_messages_before_joining(client):
    code = room_code(client)
    with client.websocket_connect(f"/ws/mic?room={code}") as mic:
        mic.send_json({"type": "hello", "name": "", "color": "#ff4f9a"})
        assert mic.receive_json()["type"] == "error"
        mic.send_json({"type": "pitch", "frames": []})
        assert mic.receive_json()["type"] == "error"


def test_live_pitch_reaches_host_with_song_time(client):
    code = room_code(client)
    song = client.post("/api/songs", files={"file": ("Grupo Inventado - Cancion de Prueba.wav", make_wav(), "audio/wav")}).json()
    with client.websocket_connect("/ws/host") as host, client.websocket_connect(f"/ws/mic?room={code}") as mic:
        mic.send_json({"type": "hello", "name": "Leo", "color": "#4ade80"})
        receive_until(mic, "welcome")
        t = now_ms()
        host.send_json(
            {"type": "playback", "song_id": song["id"], "playing": True, "position_ms": 1000, "server_ms": t}
        )
        # The phone starts receiving the lyrics status of the current song.
        status = receive_until(mic, "status")
        assert status["song_title"] == "Cancion de Prueba" and status["playing"]

        frames = [
            {"t": t + 300, "f0": 440.0, "conf": 0.95, "rms": 0.2},  # sung A4
            {"t": t + 320, "f0": 440.0, "conf": 0.2, "rms": 0.2},  # unclear pitch
            {"t": t + 340, "f0": 440.0, "conf": 0.95, "rms": 0.001},  # below the noise gate
        ]
        mic.send_json({"type": "pitch", "frames": frames})
        live = receive_until(host, "pitch_live")["players"][0]["frames"]
        assert [f["midi"] for f in live] == [69.0, None, None]
        # song time = position + elapsed - default mic latency (100 ms)
        assert live[0]["song_ms"] == pytest.approx(1000 + 300 - 100)
