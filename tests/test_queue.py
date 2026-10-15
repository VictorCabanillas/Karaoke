from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from processing.queue import Pipeline, ProcessingError, ProcessingQueue
from processing.transcribe import AsrWord
from server.library import Library
from server.main import create_app
from server.models import Song
from tests.conftest import make_wav


class FakePipeline(Pipeline):
    """Writes small placeholder files instead of running Demucs/CREPE/Whisper."""

    def __init__(self, words=None, fail: str | None = None):
        super().__init__()
        self.asr = words if words is not None else []
        self.fail = fail
        self.separations = 0

    def check(self) -> str:
        if self.fail == "deps":
            raise ProcessingError("Faltan dependencias de procesado (torch)")
        return "cpu"

    def separate(self, audio: Path, out_dir: Path, device: str, report) -> None:
        self.separations += 1
        report(0.5, "Separando voz e instrumental")
        (out_dir / "vocals.wav").write_bytes(make_wav(1))
        (out_dir / "instrumental.wav").write_bytes(make_wav(1))
        report(1.0, "Separación terminada")

    def melody(self, vocals: Path, device: str, report) -> dict:
        if self.fail == "melody":
            raise RuntimeError("boom")
        return {"hop_ms": 10, "midi": [None, 60.0, 60.5, None]}

    def words(self, vocals: Path, device: str, report, prompt):
        report(1.0, "Transcribiendo la voz")
        return self.asr


def add_song(lib: Library, tmp_path: Path) -> str:
    song_id = lib.new_id()
    folder = lib.song_dir(song_id)
    folder.mkdir(parents=True)
    (folder / "original.wav").write_bytes(make_wav(1))
    lib.add(Song(id=song_id, title="Prueba", duration_s=1.0), audio_file="original.wav")
    return song_id


@pytest.fixture
def lib(tmp_path):
    library = Library(tmp_path)
    yield library
    library.close()


def run(lib, pipeline, song_id):
    updates = []
    q = ProcessingQueue(lib, on_update=updates.append, pipeline=pipeline)
    q.start()
    q.enqueue(song_id)
    assert q.wait_idle(5)
    q.stop()
    return updates


def test_transcribes_when_there_are_no_lyrics(lib, tmp_path):
    song_id = add_song(lib, tmp_path)
    words = [AsrWord("hola", 0, 300), AsrWord("mundo", 300, 700)]
    updates = run(lib, FakePipeline(words), song_id)

    song = lib.get(song_id)
    assert song.status == "processed" and song.has_stems and song.has_melody
    assert song.lyrics_source == "whisper"
    assert lib.load_json(song_id, "melody.json")["midi"][1] == 60.0
    assert updates[0].progress_label == "En cola"
    progress = [u.progress for u in updates]
    assert progress == sorted(progress)


def test_missing_dependencies_mark_error(lib, tmp_path):
    song_id = add_song(lib, tmp_path)
    run(lib, FakePipeline(fail="deps"), song_id)
    song = lib.get(song_id)
    assert song.status == "error"
    assert "dependencias" in song.error


def test_failure_keeps_song_playable_and_can_retry(lib, tmp_path):
    song_id = add_song(lib, tmp_path)
    pipeline = FakePipeline(fail="melody")
    run(lib, pipeline, song_id)
    assert lib.get(song_id).status == "error"
    pipeline.fail = None
    run(lib, pipeline, song_id)
    assert lib.get(song_id).status == "processed"
    assert pipeline.separations == 1  # stems from the first attempt are reused


def test_process_endpoint_and_melody(settings):
    from tests.conftest import FakeLrclib

    app = create_app(settings, lrclib=FakeLrclib(), pipeline=FakePipeline())
    with TestClient(app) as client:
        song = client.post(
            "/api/songs", files={"file": ("Grupo Inventado - Cancion de Prueba.wav", make_wav(), "audio/wav")}
        ).json()
        assert client.get(f"/api/songs/{song['id']}/melody").status_code == 404
        with client.websocket_connect("/ws/host") as ws:
            r = client.post(f"/api/songs/{song['id']}/process")
            assert r.json()["status"] == "processing"
            assert app.state.queue.wait_idle(5)
            seen = []
            while not seen or seen[-1]["song"]["status"] != "processed":
                seen.append(ws.receive_json())
            assert all(m["type"] == "song" for m in seen)
        melody = client.get(f"/api/songs/{song['id']}/melody").json()
        assert melody == {"hop_ms": 10, "midi": [None, 60.0, 60.5, None]}
        assert client.get(f"/api/songs/{song['id']}/audio/vocals").status_code == 200
        final = client.get(f"/api/songs/{song['id']}").json()
        assert final["status"] == "processed"
        # Synced lyrics were kept (the fake ASR returned no words to align).
        assert final["lyrics_source"] == "lrclib_synced"
        info = client.get("/api/system").json()
        assert set(info) >= {"device", "processing_available", "ffmpeg", "https"}
