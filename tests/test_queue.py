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

    def words(self, vocals: Path, device: str, report, prompt, mix=None):
        self.mix = mix
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
                msg = ws.receive_json()
                if msg["type"] == "song":
                    seen.append(msg)
        melody = client.get(f"/api/songs/{song['id']}/melody").json()
        assert melody == {"hop_ms": 10, "midi": [None, 60.0, 60.5, None]}
        assert client.get(f"/api/songs/{song['id']}/audio/vocals").status_code == 200
        final = client.get(f"/api/songs/{song['id']}").json()
        assert final["status"] == "processed"
        # Synced lyrics were kept (the fake ASR returned no words to align).
        assert final["lyrics_source"] == "lrclib_synced"
        info = client.get("/api/system").json()
        assert set(info) >= {"device", "processing_available", "ffmpeg", "https"}


def test_reprocessing_transcribes_again_and_passes_the_mix(lib, tmp_path):
    song_id = add_song(lib, tmp_path)
    pipeline = FakePipeline([AsrWord("vieja", 0, 300)])
    run(lib, pipeline, song_id)
    assert lib.load_lyrics(song_id).lines[0].words[0].text == "vieja"
    assert pipeline.mix.name == "original.wav"
    pipeline.asr = [AsrWord("nueva", 0, 300)]
    run(lib, pipeline, song_id)
    assert lib.load_lyrics(song_id).lines[0].words[0].text == "nueva"


def test_pasted_plain_lyrics_are_aligned_and_melody_is_reused(lib, tmp_path):
    from processing.lrc import plain_lyrics

    song_id = add_song(lib, tmp_path)
    pipeline = FakePipeline([AsrWord("hola", 500, 900), AsrWord("mundo", 900, 1400)])
    run(lib, pipeline, song_id)
    lib.save_json(song_id, "melody.json", {"hop_ms": 10, "midi": [61.0]})
    lib.save_lyrics(song_id, plain_lyrics("hola mundo"))
    pipeline.fail = "melody"  # the melody must not be computed again
    run(lib, pipeline, song_id)
    lyrics = lib.load_lyrics(song_id)
    assert lib.get(song_id).status == "processed"
    assert lyrics.source == "lrclib_aligned" and lyrics.lines[0].start_ms == 500


def test_jobs_interrupted_by_a_shutdown_resume_on_start(settings, monkeypatch):
    from processing import device as device_mod

    settings.data_dir.mkdir(parents=True)
    lib = Library(settings.data_dir)
    song_id = add_song(lib, settings.data_dir)
    lib.update(song_id, status="processing", progress=0.4, progress_label="Separando voz e instrumental")
    lib.close()

    monkeypatch.setattr(device_mod, "missing_modules", lambda: [])
    from tests.conftest import FakeLrclib

    app = create_app(settings, lrclib=FakeLrclib(), pipeline=FakePipeline())
    with TestClient(app):
        assert app.state.queue.wait_idle(5)
        assert app.state.library.get(song_id).status == "processed"


def test_processing_fixes_shifted_lyrics_and_leaves_a_note(lib, tmp_path):
    from tests.test_lyrics_check import lyrics as invented_lyrics, sung

    song_id = add_song(lib, tmp_path)
    lib.update(song_id, duration_s=60.0)
    lib.save_lyrics(song_id, invented_lyrics())
    updates = run(lib, FakePipeline(sung(shift_ms=3500)), song_id)
    labels = {u.progress_label for u in updates}
    assert "Comprobando la letra con la voz" in labels and "Transcribiendo la voz" not in labels

    song = lib.get(song_id)
    assert song.status == "processed"
    assert "3.5 s adelantada" in song.lyrics_note
    assert abs(lib.load_lyrics(song_id).lines[0].start_ms - 13_500) < 100

    # Choosing other lyrics clears the note of the old ones.
    lib.save_lyrics(song_id, invented_lyrics())
    assert lib.get(song_id).lyrics_note is None


def test_pipeline_reads_the_model_at_each_job():
    chosen = ["small"]
    pipeline = Pipeline(lambda: chosen[0])
    seen = []
    import processing.transcribe as transcribe

    original = transcribe.transcribe_words
    transcribe.transcribe_words = lambda vocals, device, report, model, prompt, mix: seen.append(model) or []
    try:
        pipeline.words(Path("v.wav"), "cpu", lambda *a: None, None)
        chosen[0] = "large-v3"
        pipeline.words(Path("v.wav"), "cpu", lambda *a: None, None)
    finally:
        transcribe.transcribe_words = original
    assert seen == ["small", "large-v3"]


def test_deleted_lyrics_are_transcribed_again(settings, monkeypatch):
    from processing import device as device_mod
    from tests.conftest import FakeLrclib

    monkeypatch.setattr(device_mod, "missing_modules", lambda: [])
    pipeline = FakePipeline([AsrWord("transcrita", 0, 400)])
    app = create_app(settings, lrclib=FakeLrclib(), pipeline=pipeline)
    with TestClient(app) as client:
        song = client.post("/api/songs", files={"file": ("Grupo Inventado - Cancion de Prueba.wav", make_wav(), "audio/wav")}).json()
        lyrics = {"source": "manual", "synced": True, "lines": [{"start_ms": 0, "end_ms": 900, "words": [
            {"text": "editada", "start_ms": 0, "end_ms": 900, "estimated": True}]}]}  # fmt: skip
        client.put(f"/api/songs/{song['id']}/lyrics", json=lyrics)
        r = client.delete(f"/api/songs/{song['id']}/lyrics", params={"retranscribe": True})
        assert r.json()["status"] == "processing"
        assert app.state.queue.wait_idle(5)
        assert client.get(f"/api/songs/{song['id']}").json()["lyrics_source"] == "whisper"
        assert client.get(f"/api/songs/{song['id']}/lyrics").json()["lines"][0]["words"][0]["text"] == "transcrita"
