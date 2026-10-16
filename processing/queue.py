"""Background processing queue: one song at a time, in order, with progress reports."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

from processing import device as device_mod
from server.library import INSTRUMENTAL_FILE, MELODY_FILE, VOCALS_FILE, Library
from server.models import Song

log = logging.getLogger(__name__)

# (start, end) share of the overall progress bar for each step.
STEP_SEPARATE = (0.0, 0.55)
STEP_MELODY = (0.55, 0.7)
STEP_WORDS = (0.7, 1.0)


class ProcessingError(Exception):
    pass


class Pipeline:
    """The processing steps. Each heavy step is a method so tests can replace it."""

    def __init__(self, whisper_model: str = ""):
        self.whisper_model = whisper_model

    def check(self) -> str:
        missing = device_mod.missing_modules()
        if missing:
            raise ProcessingError(
                "Faltan dependencias de procesado (" + ", ".join(missing) + "). "
                "Instálalas con: uv sync --extra processing"
            )
        return device_mod.detect_device()

    def separate(self, audio: Path, out_dir: Path, device: str, report) -> None:
        from processing.separate import separate

        separate(audio, out_dir, device, report)

    def melody(self, vocals: Path, device: str, report) -> dict:
        from processing.melody import extract_melody

        return extract_melody(vocals, device, report)

    def words(self, vocals: Path, device: str, report, prompt: str | None, mix: Path | None = None):
        from processing.transcribe import transcribe_words

        return transcribe_words(vocals, device, report, self.whisper_model, prompt, mix)


class ProcessingQueue:
    def __init__(
        self,
        library: Library,
        on_update: Callable[[Song], None] = lambda song: None,
        pipeline: Pipeline | None = None,
    ):
        self.library = library
        self.on_update = on_update
        self.pipeline = pipeline or Pipeline()
        self._jobs: queue.Queue[str | None] = queue.Queue()
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.current: str | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="processing", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._jobs.put(None)
            self._thread.join(timeout=5)
            self._thread = None

    def pending(self) -> list[str]:
        with self._lock:
            return list(self._pending)

    def enqueue(self, song_id: str) -> Song | None:
        song = self.library.get(song_id)
        if song is None or song.origin != "file":
            return song
        with self._lock:
            if song_id in self._pending or song_id == self.current:
                return song
            self._pending.append(song_id)
        song = self._set(song_id, status="processing", progress=0.0, progress_label="En cola", error=None)
        self._jobs.put(song_id)
        return song

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Test helper: wait until the queue is empty."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._pending and self.current is None:
                    return True
            time.sleep(0.02)
        return False

    # --- worker -------------------------------------------------------------

    def _set(self, song_id: str, **fields) -> Song | None:
        song = self.library.update(song_id, **fields)
        if song is not None:
            self.on_update(song)
        return song

    def _run(self) -> None:
        while True:
            song_id = self._jobs.get()
            if song_id is None:
                return
            with self._lock:
                if song_id in self._pending:
                    self._pending.remove(song_id)
                self.current = song_id
            try:
                if self.library.get(song_id) is not None:
                    self._process(song_id)
            except Exception as exc:  # report any failure on the song itself
                log.exception("Processing failed for %s", song_id)
                message = str(exc) if isinstance(exc, ProcessingError) else f"Error al procesar: {exc}"
                self._set(song_id, status="error", error=message, progress=0.0, progress_label="")
            finally:
                with self._lock:
                    self.current = None

    def _step_reporter(self, song_id: str, step: tuple[float, float]):
        lo, hi = step
        last = [-1.0]

        def report(fraction: float, label: str) -> None:
            value = lo + (hi - lo) * max(0.0, min(1.0, fraction))
            if value - last[0] >= 0.01 or fraction >= 1.0:
                last[0] = value
                self._set(song_id, progress=round(value, 3), progress_label=label)

        return report

    def _process(self, song_id: str) -> None:
        from processing.transcribe import align_lyrics, lyrics_from_words, lyrics_prompt

        lib = self.library
        device = self.pipeline.check()
        audio = lib.audio_path(song_id)
        if audio is None:
            raise ProcessingError("No se encuentra el archivo de audio")
        folder = lib.song_dir(song_id)
        label = {"cuda": "GPU", "mps": "GPU (Apple)", "cpu": "CPU"}.get(device, device)
        self._set(song_id, status="processing", progress=0.0, progress_label=f"Empezando ({label})")

        vocals = folder / VOCALS_FILE
        if not (vocals.exists() and (folder / INSTRUMENTAL_FILE).exists()):
            self.pipeline.separate(audio, folder, device, self._step_reporter(song_id, STEP_SEPARATE))
        self._set(song_id, has_stems=1)

        if lib.load_json(song_id, MELODY_FILE) is None:
            melody = self.pipeline.melody(vocals, device, self._step_reporter(song_id, STEP_MELODY))
            lib.save_json(song_id, MELODY_FILE, melody)
        self._set(song_id, has_melody=1)

        song = lib.get(song_id)
        lyrics = lib.load_lyrics(song_id)
        if lyrics is not None and lyrics.source == "whisper":
            lyrics = None  # reprocessing: transcribe again instead of aligning the old transcription
        if not (song and song.instrumental and lyrics is None):
            report = self._step_reporter(song_id, STEP_WORDS)
            asr = self.pipeline.words(vocals, device, report, lyrics_prompt(lyrics), audio)
            duration_ms = round(song.duration_s * 1000) if song else None
            if lyrics is not None:
                aligned = align_lyrics(lyrics, asr, duration_ms)
                if aligned is not None:
                    lib.save_lyrics(song_id, aligned, instrumental=bool(song and song.instrumental))
                else:
                    log.info("Alignment of %s not reliable; keeping line timing", song_id)
            elif asr:
                lib.save_lyrics(song_id, lyrics_from_words(asr))

        self._set(song_id, status="processed", progress=1.0, progress_label="", error=None)
