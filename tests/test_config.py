from savewatch.config import Settings


def test_settings_round_trip(tmp_path):
    path = str(tmp_path / "cfg" / "settings.json")
    s = Settings(source="C:/saves", backup="D:/bk", interval_minutes=2.5, keep=3,
                 full_backup_on_start=False, minimize_to_tray=False,
                 start_minimized=True, auto_start_watching=True)
    s.save(path)
    assert Settings.load(path) == s


def test_settings_defaults_on_missing_or_corrupt(tmp_path):
    path = str(tmp_path / "settings.json")
    assert Settings.load(path) == Settings()
    with open(path, "w") as fh:
        fh.write("[]")
    assert Settings.load(path) == Settings()
    with open(path, "w") as fh:
        fh.write('{"keep": "7", "unknown_key": 1, "interval_minutes": "1.5"}')
    s = Settings.load(path)
    assert s.keep == 7 and s.interval_minutes == 1.5
