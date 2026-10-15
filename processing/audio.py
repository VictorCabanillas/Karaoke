"""ffmpeg helpers. ffmpeg is optional: without it the original file is played as-is."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

BROWSER_FRIENDLY = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".webm"}


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def normalize(src: Path, dst: Path) -> bool:
    """Loudness-normalize `src` into an MP3 at `dst`. Returns False if ffmpeg is missing or fails."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return False
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k",
        str(dst),
    ]  # fmt: skip
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600, creationflags=flags)
    except (subprocess.SubprocessError, OSError):
        dst.unlink(missing_ok=True)
        return False
    return dst.exists()
