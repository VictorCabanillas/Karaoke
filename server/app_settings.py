"""Settings changed from the app, stored in data/settings.json (environment variables are the defaults)."""

from __future__ import annotations

import threading
from pathlib import Path

from pydantic import ValidationError

from server.models import AppSettings


class SettingsStore:
    def __init__(self, path: Path, defaults: AppSettings | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._settings = defaults or AppSettings()
        if path.exists():
            try:
                self._settings = AppSettings.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, OSError):
                pass  # unreadable: keep the defaults

    def get(self) -> AppSettings:
        with self._lock:
            return self._settings.model_copy()

    def update(self, settings: AppSettings) -> AppSettings:
        with self._lock:
            self._settings = settings
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
            return self._settings.model_copy()
