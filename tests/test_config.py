import json

from savewatch.config import GameProfile, Settings, guess_game_name, safe_folder_name


def test_settings_round_trip(tmp_path):
    path = str(tmp_path / "cfg" / "settings.json")
    s = Settings(full_backup_on_start=False, minimize_to_tray=False,
                 start_minimized=True, auto_start_watching=True, undo_days=3)
    a = s.add_profile("Elden Ring", source="C:/saves/er", backup="D:/bk/er",
                      interval_minutes=2.5, keep=3)
    s.add_profile("Stardew Valley", source="C:/saves/sv", backup="D:/bk/sv")
    s.active_profile = a.id
    s.save(path)
    loaded = Settings.load(path)
    assert loaded == s
    assert loaded.active().name == "Elden Ring"


def test_settings_defaults_on_missing_or_corrupt(tmp_path):
    path = str(tmp_path / "settings.json")
    assert Settings.load(path) == Settings()
    with open(path, "w") as fh:
        fh.write("[]")
    assert Settings.load(path) == Settings()
    with open(path, "w") as fh:
        json.dump({"profiles": [{"name": "X", "keep": "7", "interval_minutes": "1.5"},
                                "garbage", {"keep": "not a number"}],
                   "undo_days": "bad", "unknown_key": 1}, fh)
    s = Settings.load(path)
    assert len(s.profiles) == 1
    assert s.profiles[0].keep == 7 and s.profiles[0].interval_minutes == 1.5
    assert s.undo_days == 7
    assert s.active_profile == s.profiles[0].id


def test_legacy_single_folder_settings_become_a_profile(tmp_path):
    path = str(tmp_path / "settings.json")
    with open(path, "w") as fh:
        json.dump({"source": r"C:\Users\me\AppData\Local\Larian Studios\Baldur's Gate 3"
                             r"\PlayerProfiles\Public\Savegames\Story",
                   "backup": r"D:\SaveBackups\BG3", "interval_minutes": 2.0, "keep": 4,
                   "minimize_to_tray": False}, fh)
    s = Settings.load(path)
    assert len(s.profiles) == 1
    p = s.active()
    assert p.name == "Baldur's Gate 3"
    assert p.backup == r"D:\SaveBackups\BG3" and p.keep == 4 and p.interval_minutes == 2.0
    assert s.minimize_to_tray is False


def test_find_remove_and_ensure(tmp_path):
    s = Settings()
    assert s.active() is None
    first = s.ensure_profile()
    assert s.active() is first
    b = s.add_profile("Hades II")
    assert s.find("hades ii") is b and s.find(b.id) is b and s.find("nope") is None
    s.active_profile = b.id
    s.remove_profile(b.id)
    assert s.active() is first and s.active_profile == first.id


def test_suggest_backup_and_names():
    s = Settings()
    assert s.suggest_backup("Hades") == ""
    s.add_profile("BG3", backup=r"D:\SaveBackups\BG3")
    assert s.suggest_backup('Wh:at?') .endswith("What")
    assert safe_folder_name("  ...  ") == "Game"
    assert guess_game_name(r"C:\Users\me\Saved Games\CD Projekt Red\Cyberpunk 2077") == "Cyberpunk 2077"
    assert guess_game_name(r"C:\Users\me\AppData\Roaming\EldenRing\76561198000000000") == "EldenRing"
    assert guess_game_name("") == ""


def test_profile_from_dict_rejects_junk():
    assert GameProfile.from_dict(None) is None
    assert GameProfile.from_dict({"keep": "x"}) is None
    p = GameProfile.from_dict({"name": "  ", "id": "abc"})
    assert p.name == "New game" and p.id == "abc"
