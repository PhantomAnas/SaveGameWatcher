"""Named manual backups ("checkpoints")."""

from __future__ import annotations

import os
import shutil
from datetime import datetime

import pytest

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


def _cycle(src, dst, keep=0):
    return core.run_backup_cycle(str(src), str(dst), keep=keep, state=core.load_state(str(dst)),
                                 log=lambda *_: None)


@pytest.fixture
def game(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "Slot1" / "save.dat", "act1")
    _write(src / "quick.sav", "q1")
    return src, dst


def test_checkpoint_snapshots_every_save(game):
    src, dst = game
    cp = core.create_checkpoint(str(src), str(dst), "  Before the   big choice ",
                                now=datetime(2026, 11, 22, 20, 0, 0))
    assert cp["id"] == "2026-11-22_20-00-00"
    assert cp["title"] == "Before the big choice"
    assert sorted(e["name"] for e in cp["entries"]) == ["Slot1", "quick.sav"]
    assert cp["errors"] == []
    for e in cp["entries"]:
        assert os.path.isdir(dst / e["name"] / e["version"])

    listed = core.load_checkpoints(str(dst))
    assert [c["title"] for c in listed] == ["Before the big choice"]
    assert listed[0]["created_time"] == datetime(2026, 11, 22, 20, 0, 0)
    assert core.find_checkpoint(str(dst), "before the BIG choice")["id"] == cp["id"]
    assert "CHECKPOINT 'Before the big choice'" in _read(dst / core.LOG_FILENAME)


def test_checkpoint_counts_as_backed_up(game):
    src, dst = game
    core.create_checkpoint(str(src), str(dst), "cp")
    assert _cycle(src, dst)["copied"] == 0


def test_checkpoint_versions_survive_pruning(game):
    src, dst = game
    cp = core.create_checkpoint(str(src), str(dst), "keep me",
                                now=datetime(2020, 1, 1, 0, 0, 0))
    pinned = next(e["version"] for e in cp["entries"] if e["name"] == "Slot1")
    for i in range(1, 5):
        _change(src / "Slot1" / "save.dat", f"act{i + 1}", 10 * i)
        _cycle(src, dst, keep=2)
    versions = core.list_versions(str(dst / "Slot1"))
    assert pinned in versions
    assert len(versions) == 3  # 2 kept by keep-N + the pinned one
    assert core.pinned_versions(str(dst), "Slot1") == {pinned: "keep me"}

    core.delete_checkpoint(str(dst), cp["id"])
    assert core.load_checkpoints(str(dst)) == []
    _change(src / "Slot1" / "save.dat", "act9", 100)
    _cycle(src, dst, keep=2)
    assert pinned not in core.list_versions(str(dst / "Slot1"))


def test_restore_checkpoint_brings_all_saves_back_with_undo(game):
    src, dst = game
    cp = core.create_checkpoint(str(src), str(dst), "Before the boss")
    _change(src / "Slot1" / "save.dat", "dead", 30)
    _change(src / "quick.sav", "dead too", 30)
    _write(src / "NewSlot" / "x.dat", "made later")

    res = core.restore_checkpoint(str(src), str(dst), cp["id"])
    assert sorted(res["restored"]) == ["Slot1", "quick.sav"] and res["errors"] == []
    assert _read(src / "Slot1" / "save.dat") == "act1"
    assert _read(src / "quick.sav") == "q1"
    assert _read(src / "NewSlot" / "x.dat") == "made later"  # never deleted

    points = core.list_undo_points(str(dst), "Slot1")
    assert points[0]["reason"] == "before restoring checkpoint 'Before the boss'"
    core.undo_restore(str(src), str(dst), "Slot1", points[0]["id"])
    assert _read(src / "Slot1" / "save.dat") == "dead"


def test_restore_checkpoint_reports_missing_versions(game):
    src, dst = game
    cp = core.create_checkpoint(str(src), str(dst), "cp")
    gone = next(e for e in cp["entries"] if e["name"] == "quick.sav")
    shutil.rmtree(dst / "quick.sav" / gone["version"])
    res = core.restore_checkpoint(str(src), str(dst), cp["id"])
    assert res["restored"] == ["Slot1"] and len(res["errors"]) == 1


def test_checkpoint_ids_unique_rename_and_errors(game, tmp_path):
    src, dst = game
    now = datetime(2026, 1, 1, 12, 0, 0)
    a = core.create_checkpoint(str(src), str(dst), "", now=now)
    b = core.create_checkpoint(str(src), str(dst), "second", now=now)
    assert a["id"] != b["id"] and a["title"].startswith("Checkpoint 01 Jan 2026")
    core.rename_checkpoint(str(dst), a["id"], "renamed")
    assert core.find_checkpoint(str(dst), a["id"])["title"] == "renamed"

    empty = tmp_path / "empty"
    os.makedirs(empty)
    with pytest.raises(ValueError):
        core.create_checkpoint(str(empty), str(dst), "x")
    with pytest.raises(FileNotFoundError):
        core.restore_checkpoint(str(src), str(dst), "nope")
    with pytest.raises(FileNotFoundError):
        core.delete_checkpoint(str(dst), "nope")
    # The checkpoints file is not mistaken for a save slot.
    assert sorted(e["name"] for e in core.list_backup_entries(str(dst))) == ["Slot1", "quick.sav"]


def test_corrupt_checkpoint_file_is_ignored(tmp_path):
    (tmp_path / core.CHECKPOINTS_FILENAME).write_text("{oops")
    assert core.load_checkpoints(str(tmp_path)) == []
    (tmp_path / core.CHECKPOINTS_FILENAME).write_text('{"checkpoints": [1, {"id": "x"}]}')
    assert core.load_checkpoints(str(tmp_path)) == []
