"""Restore, undo points and backup browsing (no Qt)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest

from savewatch import core


def _write(path, text: str) -> None:
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _bump(path, seconds: float) -> None:
    st = os.stat(path)
    os.utime(path, (st.st_atime + seconds, st.st_mtime + seconds))


def _backup(src, dst, state):
    return core.run_backup_cycle(str(src), str(dst), keep=0, state=state, log=lambda *_: None)


@pytest.fixture
def two_versions(tmp_path):
    """src/slot with versions v1 ('one') and v2 ('two') in dst/slot."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "slot" / "game.sav", "one")
    _write(src / "slot" / "sub" / "meta.dat", "m")
    state = {"entries": {}}
    _backup(src, dst, state)
    v1 = core.list_versions(str(dst / "slot"))[0]
    os.rename(dst / "slot" / v1, dst / "slot" / "2026-01-01_10-00-00")
    _write(src / "slot" / "game.sav", "two")
    _bump(src / "slot" / "game.sav", 30)
    _backup(src, dst, state)
    versions = core.list_versions(str(dst / "slot"))
    assert len(versions) == 2
    return src, dst, versions


def test_restore_puts_old_version_back_and_keeps_undo(two_versions):
    src, dst, versions = two_versions
    _write(src / "slot" / "game.sav", "three")  # what the player has now
    _write(src / "slot" / "extra.sav", "added later")

    res = core.restore_version(str(src), str(dst), "slot", versions[0])

    assert _read(src / "slot" / "game.sav") == "one"
    assert _read(src / "slot" / "sub" / "meta.dat") == "m"
    assert not (src / "slot" / "extra.sav").exists()  # exact copy of the version
    # Nothing temporary left behind in the game's folder.
    assert sorted(os.listdir(src)) == ["slot"]
    # Restored files are real copies, never links into the backup.
    assert not os.path.samefile(src / "slot" / "game.sav",
                                dst / "slot" / versions[0] / "game.sav")

    points = core.list_undo_points(str(dst), "slot")
    assert len(points) == 1 and points[0]["id"] == res["undo_id"]
    assert points[0]["reason"] == f"before restoring {versions[0]}"
    undo_data = dst / core.UNDO_DIRNAME / "slot" / res["undo_id"]
    assert _read(undo_data / "game.sav") == "three"
    assert _read(undo_data / "extra.sav") == "added later"
    assert "RESTORE  slot" in _read(dst / core.LOG_FILENAME)


def test_restore_is_not_backed_up_again_as_a_change(two_versions):
    src, dst, versions = two_versions
    core.restore_version(str(src), str(dst), "slot", versions[0])
    stats = _backup(src, dst, core.load_state(str(dst)))
    assert stats["copied"] == 0


def test_undo_brings_back_the_replaced_save_and_can_be_redone(two_versions):
    src, dst, versions = two_versions
    _write(src / "slot" / "game.sav", "three")
    res = core.restore_version(str(src), str(dst), "slot", versions[0])
    assert _read(src / "slot" / "game.sav") == "one"

    undo = core.undo_restore(str(src), str(dst), "slot", res["undo_id"])
    assert _read(src / "slot" / "game.sav") == "three"
    points = core.list_undo_points(str(dst), "slot")
    # The used point is gone; a redo point holding "one" replaced it.
    assert [p["id"] for p in points] == [undo["undo_id"]]
    assert _read(dst / core.UNDO_DIRNAME / "slot" / undo["undo_id"] / "game.sav") == "one"

    core.undo_restore(str(src), str(dst), "slot", undo["undo_id"])
    assert _read(src / "slot" / "game.sav") == "one"


def test_restore_a_save_that_was_deleted_and_undo_removes_it(two_versions):
    src, dst, versions = two_versions
    import shutil
    shutil.rmtree(src / "slot")

    res = core.restore_version(str(src), str(dst), "slot", versions[-1])
    assert _read(src / "slot" / "game.sav") == "two"
    point = core.list_undo_points(str(dst), "slot")[0]
    assert point["absent"] is True

    core.undo_restore(str(src), str(dst), "slot", res["undo_id"])
    assert not (src / "slot").exists()


def test_restore_single_file_save(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "quick.sav", "A")
    state = {"entries": {}}
    _backup(src, dst, state)
    version = core.list_versions(str(dst / "quick.sav"))[0]
    _write(src / "quick.sav", "B")

    core.restore_version(str(src), str(dst), "quick.sav", version)
    assert (src / "quick.sav").is_file() and _read(src / "quick.sav") == "A"
    point = core.list_undo_points(str(dst), "quick.sav")[0]
    assert point["kind"] == "file"
    core.undo_restore(str(src), str(dst), "quick.sav", point["id"])
    assert _read(src / "quick.sav") == "B"


def test_failed_swap_leaves_the_live_save_untouched(two_versions, monkeypatch):
    src, dst, versions = two_versions
    _write(src / "slot" / "game.sav", "precious")
    real_replace = os.replace

    def locked(a, b):  # the game holds the folder open
        if os.path.abspath(str(a)) == os.path.abspath(str(src / "slot")):
            raise PermissionError(13, "in use", str(a))
        return real_replace(a, b)

    monkeypatch.setattr(core.os, "replace", locked)
    with pytest.raises(PermissionError):
        core.restore_version(str(src), str(dst), "slot", versions[0])
    monkeypatch.undo()

    assert _read(src / "slot" / "game.sav") == "precious"
    assert sorted(os.listdir(src)) == ["slot"]
    assert core.list_undo_points(str(dst), "slot") == []


def test_restore_rejects_unknown_version(two_versions):
    src, dst, _ = two_versions
    with pytest.raises(FileNotFoundError):
        core.restore_version(str(src), str(dst), "slot", "2020-01-01_00-00-00")
    with pytest.raises(FileNotFoundError):
        core.restore_version(str(src), str(dst), "slot", "../../etc")


def test_scanner_ignores_restore_leftovers(tmp_path):
    _write(tmp_path / "slot" / "a.sav", "x")
    _write(tmp_path / ("slot.old" + core.SOURCE_TEMP_SUFFIX) / "a.sav", "x")
    assert list(core.scan_source(str(tmp_path))) == ["slot"]


def test_purge_undo_points_by_age(two_versions):
    src, dst, versions = two_versions
    first = core.restore_version(str(src), str(dst), "slot", versions[0])["undo_id"]
    meta = dst / core.UNDO_DIRNAME / "slot" / (first + ".json")
    old_id = "2020-01-01_00-00-00"
    os.rename(dst / core.UNDO_DIRNAME / "slot" / first, dst / core.UNDO_DIRNAME / "slot" / old_id)
    os.rename(meta, dst / core.UNDO_DIRNAME / "slot" / (old_id + ".json"))
    second = core.restore_version(str(src), str(dst), "slot", versions[1])["undo_id"]

    assert core.purge_undo_points(str(dst), 0) == 0  # 0 = keep forever
    assert core.purge_undo_points(str(dst), 7) == 1
    assert [p["id"] for p in core.list_undo_points(str(dst))] == [second]


def test_list_backup_entries_and_details(two_versions):
    _, dst, versions = two_versions
    os.makedirs(dst / core.UNDO_DIRNAME / "slot")
    os.makedirs(dst / "empty-folder")
    entries = core.list_backup_entries(str(dst))
    assert [e["name"] for e in entries] == ["slot"]
    assert entries[0]["versions"] == 2 and entries[0]["latest"] == versions[-1]

    info = core.version_details(str(dst), "slot", "2026-01-01_10-00-00")
    assert info["created"] == datetime(2026, 1, 1, 10, 0, 0)
    assert info["files"] == 2 and info["bytes"] == 4


def test_humanize_age():
    now = datetime(2026, 11, 22, 12, 0, 0)
    ago = lambda **kw: core.humanize_age(now - timedelta(**kw), now)  # noqa: E731
    assert ago(seconds=10) == "just now"
    assert ago(minutes=1) == "1 minute ago"
    assert ago(minutes=5) == "5 minutes ago"
    assert ago(hours=3) == "3 hours ago"
    assert ago(days=1, hours=2) == "yesterday"
    assert ago(days=4) == "4 days ago"
    assert ago(days=21) == "3 weeks ago"
    assert ago(days=400) == "1 year ago"
    assert core.humanize_age(None) == "unknown"


def test_undo_meta_is_plain_json(two_versions):
    src, dst, versions = two_versions
    res = core.restore_version(str(src), str(dst), "slot", versions[0])
    with open(dst / core.UNDO_DIRNAME / "slot" / (res["undo_id"] + ".json")) as fh:
        meta = json.load(fh)
    assert meta["entry"] == "slot" and meta["kind"] == "dir" and meta["files"] == 2
