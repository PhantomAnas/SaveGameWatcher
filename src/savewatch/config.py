"""User settings, stored as JSON in the per-user config directory.

Shared by the GUI and the CLI, so `savewatch --cli` with no arguments runs
whatever the window was last configured with.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

from platformdirs import user_config_dir

APP_NAME = "SaveWatch"


def config_path() -> str:
    return os.path.join(user_config_dir(APP_NAME, appauthor=False), "settings.json")


@dataclass
class Settings:
    source: str = ""
    backup: str = ""
    interval_minutes: float = 5.0
    keep: int = 10
    full_backup_on_start: bool = True
    minimize_to_tray: bool = True
    start_minimized: bool = False
    auto_start_watching: bool = False

    @classmethod
    def load(cls, path: str | None = None) -> "Settings":
        path = path or config_path()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f.name: f.type for f in fields(cls)}
        clean = {}
        for key, value in data.items():
            if key in known:
                clean[key] = value
        try:
            s = cls(**clean)
            s.interval_minutes = float(s.interval_minutes)
            s.keep = int(s.keep)
            return s
        except (TypeError, ValueError):
            return cls()

    def save(self, path: str | None = None) -> None:
        path = path or config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, path)
