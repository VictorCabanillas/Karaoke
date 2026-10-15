"""Runtime configuration. Every value can be overridden with a KARAOKE_* environment variable."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(f"KARAOKE_{name}", default)


@dataclass
class Settings:
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))
    # Port that phones should use in the QR code (e.g. the Vite port during development).
    public_port: int = field(default_factory=lambda: int(_env("PUBLIC_PORT", _env("PORT", "8000"))))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT_DIR / "data"))))
    certs_dir: Path = field(default_factory=lambda: Path(_env("CERTS_DIR", str(ROOT_DIR / "certs"))))
    web_dist: Path = field(default_factory=lambda: ROOT_DIR / "web" / "dist")
    lrclib_url: str = field(default_factory=lambda: _env("LRCLIB_URL", "https://lrclib.net"))
    user_agent: str = "karaoke-amigos/0.1 (https://github.com/VictorCabanillas/Karaoke)"
    # Default microphone input latency (ms) until the player calibrates it.
    default_mic_latency_ms: int = field(default_factory=lambda: int(_env("MIC_LATENCY_MS", "100")))
    # Whisper model sizes; empty means "pick automatically from the device".
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", ""))
    spotify_client_id: str = field(default_factory=lambda: _env("SPOTIFY_CLIENT_ID", ""))
    spotify_redirect_uri: str = field(
        default_factory=lambda: _env("SPOTIFY_REDIRECT_URI", "https://127.0.0.1:8000/api/spotify/callback")
    )

    @property
    def songs_dir(self) -> Path:
        return self.data_dir / "songs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.sqlite3"

    @property
    def cert_file(self) -> Path:
        return self.certs_dir / "cert.pem"

    @property
    def key_file(self) -> Path:
        return self.certs_dir / "key.pem"

    @property
    def https(self) -> bool:
        return self.cert_file.exists() and self.key_file.exists()


def lan_ip() -> str:
    """Best guess of this machine's IP on the local network (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


settings = Settings()
