"""Saves made of several files, and saves written while being copied."""

from __future__ import annotations

import os
from datetime import datetime

from savewatch import core


def _write(path, text: str) -> None:
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _change(path, text: str, bump: float) -> None:
    _write(path, text)
    st = os.stat(path)
    os.utime(path, (st.st_atime + bump, st.st_mtime + bump))


def _cycle(src, dst):
    return core.run_backup_cycle(str(src), str(dst), keep=0, state=core.load_state(str(dst)),
                                 log=lambda *_: None)


def _age_versions(dst) -> None:
    """Rename every version made so far as if it was taken long ago, so the
    next cycle's versions are clearly a later moment."""
    when = datetime(2026, 1, 1, 12, 0, 0)
    for entry in os.listdir(dst):
        folder = dst / entry
        if not folder.is_dir() or entry.startswith("."):
            continue
        for v in core.list_versions(str(folder)):
            if v.startswith("2026-01-01"):
                continue
            os.rename(folder / v, folder / when.strftime(core.STAMP_FORMAT))


def test_one_cycle_stamps_all_its_versions_alike(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    for name in ("a.sav", "a.png", "b.sav", "index.dat"):
        _write(src / name, name)
    _cycle(src, dst)
    stamps = {core.list_versions(str(dst / n))[0] for n in ("a.sav", "a.png", "b.sav", "index.dat")}
    assert len(stamps) == 1


def test_companion_files_are_restored_from_the_same_moment(tmp_path):
    """The Witcher 3 style: ManualSave_12.sav + ManualSave_12.png."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "ManualSave_12.sav", "save v1")
    _write(src / "ManualSave_12.png", "shot v1")
    _write(src / "ManualSave_13.sav", "other v1")
    _cycle(src, dst)
    _age_versions(dst)
    v1 = core.list_versions(str(dst / "ManualSave_12.sav"))[0]

    _change(src / "ManualSave_12.sav", "save v2", 60)
    _change(src / "ManualSave_12.png", "shot v2", 60)
    _change(src / "ManualSave_13.sav", "other v2", 60)
    _cycle(src, dst)

    assert core.related_saves(str(dst), "ManualSave_12.sav", v1) == [("ManualSave_12.png", v1)]
    res = core.restore_version(str(src), str(dst), "ManualSave_12.sav", v1)
    assert res["related"] == ["ManualSave_12.png"] and res["errors"] == []
    assert _read(src / "ManualSave_12.sav") == "save v1"
    assert _read(src / "ManualSave_12.png") == "shot v1"
    assert _read(src / "ManualSave_13.sav") == "other v2"  # different save: untouched

    # One undo puts both files back.
    undo = core.undo_restore(str(src), str(dst), "ManualSave_12.sav", res["undo_id"])
    assert undo["count"] == 2
    assert _read(src / "ManualSave_12.sav") == "save v2"
    assert _read(src / "ManualSave_12.png") == "shot v2"


def test_restore_without_companions(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "s.sav", "1")
    _write(src / "s.png", "1")
    _cycle(src, dst)
    v = core.list_versions(str(dst / "s.sav"))[0]
    _change(src / "s.png", "2", 60)
    res = core.restore_version(str(src), str(dst), "s.sav", v, with_related=False)
    assert res["related"] == [] and _read(src / "s.png") == "2"


def test_companion_uses_the_version_it_had_at_that_moment(tmp_path):
    """The screenshot did not change in the later cycle, so its older version
    is the right one; a companion created later is left alone."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "slot.sav", "s1")
    _write(src / "slot.png", "p1")
    _cycle(src, dst)
    _age_versions(dst)
    _change(src / "slot.sav", "s2", 60)
    _cycle(src, dst)
    newest = core.list_versions(str(dst / "slot.sav"))[-1]
    assert core.related_saves(str(dst), "slot.sav", newest) == [
        ("slot.png", core.list_versions(str(dst / "slot.png"))[0])]

    first = core.list_versions(str(dst / "slot.sav"))[0]
    _write(src / "slot.meta", "new")
    _cycle(src, dst)
    assert [n for n, _ in core.related_saves(str(dst), "slot.sav", first)] == ["slot.png"]


def test_restore_everything_to_a_moment(tmp_path):
    """Index-file games: every file goes back to the same moment."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "index.dat", "index: slot1")
    _write(src / "slot1.sav", "slot1 v1")
    _cycle(src, dst)
    _age_versions(dst)
    moment = core.list_versions(str(dst / "index.dat"))[0]

    _change(src / "index.dat", "index: slot1, slot2", 60)
    _change(src / "slot1.sav", "slot1 v2", 60)
    _write(src / "slot2.sav", "slot2")
    _cycle(src, dst)

    res = core.restore_point_in_time(str(src), str(dst), moment)
    assert sorted(res["restored"]) == ["index.dat", "slot1.sav"]
    assert _read(src / "index.dat") == "index: slot1"
    assert _read(src / "slot1.sav") == "slot1 v1"
    assert _read(src / "slot2.sav") == "slot2"  # newer than the moment: never deleted

    point = core.list_undo_points(str(dst), "index.dat")[0]
    undo = core.undo_restore(str(src), str(dst), "index.dat", point["id"])
    assert undo["count"] == 2
    assert _read(src / "index.dat") == "index: slot1, slot2"
    assert _read(src / "slot1.sav") == "slot1 v2"


def test_save_written_during_the_copy_is_copied_again(tmp_path, monkeypatch):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "slot" / "data.sav", "old")
    real_copy = core.copy_entry
    calls = {"n": 0}

    def game_writes_mid_copy(s, staging, prev=None):
        tally = real_copy(s, staging, prev)
        calls["n"] += 1
        if calls["n"] == 1:  # the game finishes its save while we copy
            _change(src / "slot" / "data.sav", "new", 30)
        return tally

    monkeypatch.setattr(core, "copy_entry", game_writes_mid_copy)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    stats = _cycle(src, dst)

    assert calls["n"] == 2 and stats["copied"] == 1
    version = core.list_versions(str(dst / "slot"))[0]
    assert _read(dst / "slot" / version / "data.sav") == "new"
    assert not any(n.endswith(core.STAGING_SUFFIX) for n in os.listdir(dst / "slot"))
    assert _cycle(src, dst)["copied"] == 0  # settled: nothing left to do


def test_save_that_never_holds_still_is_retried_next_check(tmp_path, monkeypatch):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "busy.sav", "0")
    real_copy = core.copy_entry
    counter = {"n": 0}

    def always_writing(s, staging, prev=None):
        tally = real_copy(s, staging, prev)
        counter["n"] += 1
        _change(src / "busy.sav", str(counter["n"]), 10 * counter["n"])
        return tally

    monkeypatch.setattr(core, "copy_entry", always_writing)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    logs: list[tuple[str, str]] = []
    state = {"entries": {}}
    core.run_backup_cycle(str(src), str(dst), keep=0, state=state,
                          log=lambda lvl, msg: logs.append((lvl, msg)))
    assert counter["n"] == 3  # gave up after 3 attempts...
    assert len(core.list_versions(str(dst / "busy.sav"))) == 1  # ...but kept the last copy
    assert any(lvl == "warn" and "still being written" in msg for lvl, msg in logs)

    monkeypatch.setattr(core, "copy_entry", real_copy)
    stats = core.run_backup_cycle(str(src), str(dst), keep=0, state=state, log=lambda *_: None)
    assert stats["copied"] == 1  # the game is done now: copied again


def test_entry_stem():
    assert core.entry_stem("ManualSave_12.sav") == "manualsave_12"
    assert core.entry_stem("ManualSave_12.PNG") == "manualsave_12"
    assert core.entry_stem("Slot1") == core.entry_stem("slot1.meta") == "slot1"
    assert core.entry_stem(".hidden") == ".hidden"
