"""User settings, stored as JSON in the per-user config directory.

Shared by the GUI and the CLI, so `savewatch --cli` with no arguments runs
whatever profile the window last had selected.

Every game gets its own *profile* (name, save folder, backup folder, check
interval, versions to keep).  Settings written by 0.1.x - a single source /
backup pair at the top level - are migrated into a profile on load.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, fields

from platformdirs import user_config_dir

APP_NAME = "SaveWatch"
DEFAULT_PROFILE_NAME = "New game"

# Path parts that never name the game itself, so the name guess skips them.
_GENERIC_PARTS = {
    "save", "saves", "savegame", "savegames", "gamesaves", "saved", "savedata", "savefiles",
    "saved games", "my games", "story", "profiles", "profile", "playerprofiles",
    "public", "remote", "local", "locallow", "roaming", "appdata", "documents",
    "users", "userdata", "steam", "win64", "windows", "wingdk", "data", "game",
    "games", "slots", "player", "players", "default",
}


def config_path() -> str:
    return os.path.join(user_config_dir(APP_NAME, appauthor=False), "settings.json")


def guess_game_name(path: str) -> str:
    """Best guess at a game's name from its save folder path.

    ...\\Documents\\The Witcher 3\\gamesaves -> The Witcher 3
    """
    parts = [p for p in re.split(r"[\\/]+", path.strip()) if p]
    for part in reversed(parts):
        low = part.lower()
        if low in _GENERIC_PARTS or part.isdigit() or part.endswith(":"):
            continue
        if re.fullmatch(r"[0-9a-f\-]{8,}", low):  # account ids / GUIDs
            continue
        return part
    return ""


def safe_folder_name(name: str) -> str:
    """`name` with characters Windows forbids in folder names removed."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(".")
    return cleaned or "Game"


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class GameProfile:
    name: str = DEFAULT_PROFILE_NAME
    source: str = ""
    backup: str = ""
    interval_minutes: float = 5.0
    keep: int = 10
    id: str = field(default_factory=_new_id)

    @classmethod
    def from_dict(cls, data: object) -> "GameProfile | None":
        if not isinstance(data, dict):
            return None
        known = {f.name for f in fields(cls)}
        try:
            p = cls(**{k: v for k, v in data.items() if k in known})
            p.name = str(p.name).strip() or DEFAULT_PROFILE_NAME
            p.source = str(p.source)
            p.backup = str(p.backup)
            p.interval_minutes = float(p.interval_minutes)
            p.keep = int(p.keep)
            p.id = str(p.id) or _new_id()
            return p
        except (TypeError, ValueError):
            return None


@dataclass
class Settings:
    profiles: list[GameProfile] = field(default_factory=list)
    active_profile: str = ""
    full_backup_on_start: bool = True
    minimize_to_tray: bool = True
    start_minimized: bool = False
    auto_start_watching: bool = False
    undo_days: int = 7          # how long restore undo points are kept, 0 = forever

    # ------------------------------------------------------------ profiles
    def active(self) -> GameProfile | None:
        for p in self.profiles:
            if p.id == self.active_profile:
                return p
        return self.profiles[0] if self.profiles else None

    def find(self, key: str) -> GameProfile | None:
        """Profile by id, or by name (case-insensitive)."""
        for p in self.profiles:
            if p.id == key:
                return p
        low = key.strip().lower()
        for p in self.profiles:
            if p.name.lower() == low:
                return p
        return None

    def add_profile(self, name: str = DEFAULT_PROFILE_NAME, **kwargs) -> GameProfile:
        p = GameProfile(name=name, **kwargs)
        self.profiles.append(p)
        return p

    def remove_profile(self, profile_id: str) -> None:
        self.profiles = [p for p in self.profiles if p.id != profile_id]
        if self.active_profile == profile_id:
            self.active_profile = self.profiles[0].id if self.profiles else ""

    def suggest_backup(self, name: str) -> str:
        """Backup folder for a new game next to the ones already in use."""
        for p in self.profiles:
            if p.backup:
                parent = os.path.dirname(os.path.normpath(p.backup))
                if parent:
                    return os.path.join(parent, safe_folder_name(name))
        return ""

    def ensure_profile(self) -> GameProfile:
        """There is always at least one profile and a valid active one."""
        if not self.profiles:
            self.add_profile()
        active = self.active()
        assert active is not None
        self.active_profile = active.id
        return active

    # ----------------------------------------------------------------- I/O
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

        s = cls()
        for key in ("full_backup_on_start", "minimize_to_tray", "start_minimized",
                    "auto_start_watching"):
            if isinstance(data.get(key), bool):
                setattr(s, key, data[key])
        try:
            s.undo_days = max(0, int(data.get("undo_days", s.undo_days)))
        except (TypeError, ValueError):
            pass

        raw = data.get("profiles")
        if isinstance(raw, list):
            s.profiles = [p for p in (GameProfile.from_dict(d) for d in raw) if p]
        elif data.get("source") or data.get("backup"):
            # 0.1.x settings: one folder pair at the top level.
            legacy = GameProfile.from_dict({
                "name": guess_game_name(str(data.get("source", ""))) or "My game",
                "source": data.get("source", ""), "backup": data.get("backup", ""),
                "interval_minutes": data.get("interval_minutes", 5.0),
                "keep": data.get("keep", 10),
            })
            if legacy:
                s.profiles = [legacy]
        s.active_profile = str(data.get("active_profile", ""))
        if s.profiles and not any(p.id == s.active_profile for p in s.profiles):
            s.active_profile = s.profiles[0].id
        return s

    def save(self, path: str | None = None) -> None:
        path = path or config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, path)
