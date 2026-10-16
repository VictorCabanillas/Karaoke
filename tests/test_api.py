import shutil

import pytest

from tests.conftest import FakeLrclib, make_wav


def upload(client, name="Grupo Inventado - Cancion de Prueba.wav", data=None):
    return client.post("/api/songs", files={"file": (name, data or make_wav(), "audio/wav")})


def test_upload_reads_metadata_and_finds_lyrics(client):
    r = upload(client)
    assert r.status_code == 200, r.text
    song = r.json()
    assert song["title"] == "Cancion de Prueba"
    assert song["artist"] == "Grupo Inventado"
    assert abs(song["duration_s"] - 2.0) < 0.05
    assert song["status"] == "lyrics_ready"
    assert song["lyrics_source"] == "lrclib_synced"

    lyrics = client.get(f"/api/songs/{song['id']}/lyrics").json()
    assert lyrics["lines"][0]["words"][0]["text"] == "La"

    audio = client.get(f"/api/songs/{song['id']}/audio/original")
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    assert client.get(f"/api/songs/{song['id']}/audio/vocals").status_code == 404
    assert [s["id"] for s in client.get("/api/songs").json()] == [song["id"]]


def test_upload_without_lyrics_then_choose(client, fake_lrclib: FakeLrclib):
    song = upload(client, name="Otro - Desconocida.wav").json()
    assert song["status"] == "imported"
    assert song["lyrics_source"] is None
    assert client.get(f"/api/songs/{song['id']}/lyrics").status_code == 404

    candidates = client.get(f"/api/songs/{song['id']}/lyrics/search", params={"q": "prueba"}).json()
    assert candidates[0]["id"] == 7 and candidates[0]["has_synced"]
    chosen = client.post(f"/api/songs/{song['id']}/lyrics", json={"lrclib_id": 7}).json()
    assert chosen["status"] == "lyrics_ready"


def test_offset_patch_is_saved_and_bounded(client):
    song = upload(client).json()
    r = client.patch(f"/api/songs/{song['id']}", json={"offset_ms": -350})
    assert r.json()["offset_ms"] == -350
    assert client.get(f"/api/songs/{song['id']}").json()["offset_ms"] == -350
    assert client.patch(f"/api/songs/{song['id']}", json={"offset_ms": 5000}).status_code == 422


def test_rejects_unknown_format(client):
    assert upload(client, name="notas.txt", data=b"hola").status_code == 400


def test_delete(client):
    song = upload(client).json()
    assert client.delete(f"/api/songs/{song['id']}").status_code == 204
    assert client.get(f"/api/songs/{song['id']}").status_code == 404


@pytest.mark.ffmpeg
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_upload_is_normalized_to_mp3_with_ffmpeg(client):
    song = upload(client).json()
    audio = client.get(f"/api/songs/{song['id']}/audio/original")
    assert audio.status_code == 200
    assert audio.content[:3] == b"ID3" or audio.content[:2] == b"\xff\xfb"


def lyrics_payload(lines):
    return {
        "source": "lrclib_synced",
        "synced": True,
        "lines": [
            {
                "start_ms": s,
                "end_ms": e,
                "words": [{"text": t, "start_ms": s, "end_ms": e, "estimated": True} for t in text.split()],
            }
            for s, e, text in lines
        ],
    }


def test_save_edited_lyrics(client):
    song = upload(client).json()
    r = client.put(f"/api/songs/{song['id']}/lyrics", json=lyrics_payload([(1000, 2000, "hola mundo"), (2500, 3000, "adios")]))
    assert r.status_code == 200, r.text
    assert r.json()["lyrics_source"] == "manual"
    saved = client.get(f"/api/songs/{song['id']}/lyrics").json()
    assert saved["source"] == "manual" and [l["start_ms"] for l in saved["lines"]] == [1000, 2500]


def test_edited_lyrics_must_be_in_order_and_not_empty(client):
    song = upload(client).json()
    bad = lyrics_payload([(3000, 4000, "uno"), (1000, 2000, "dos")])
    assert client.put(f"/api/songs/{song['id']}/lyrics", json=bad).status_code == 422
    empty = lyrics_payload([(0, 100, " ")])
    assert client.put(f"/api/songs/{song['id']}/lyrics", json=empty).status_code == 422


def test_paste_plain_lyrics(client):
    song = upload(client, name="Otro - Desconocida.wav").json()
    r = client.post(f"/api/songs/{song['id']}/lyrics/text", json={"text": "primera linea\n\nsegunda linea", "align": False})
    assert r.json()["lyrics_source"] == "lrclib_lines_only" and r.json()["status"] == "lyrics_ready"
    lyrics = client.get(f"/api/songs/{song['id']}/lyrics").json()
    assert not lyrics["synced"] and len(lyrics["lines"]) == 2


def test_delete_lyrics(client):
    song = upload(client).json()
    client.put(f"/api/songs/{song['id']}/lyrics", json=lyrics_payload([(1000, 2000, "hola mundo")]))
    r = client.delete(f"/api/songs/{song['id']}/lyrics")
    assert r.status_code == 200
    assert r.json()["lyrics_source"] is None and r.json()["status"] == "imported"
    assert client.get(f"/api/songs/{song['id']}/lyrics").status_code == 404
    # Without the processing installed it cannot transcribe them again.
    assert client.delete(f"/api/songs/{song['id']}/lyrics", params={"retranscribe": True}).status_code == 400
