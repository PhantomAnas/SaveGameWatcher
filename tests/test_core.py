"""Unit tests for the backup engine (no Qt)."""

from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

from savewatch import core


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _logs() -> tuple[list[tuple[str, str]], core.LogFn]:
    entries: list[tuple[str, str]] = []
    return entries, lambda level, text: entries.append((level, text))


def _make_save(root: str, name: str, files: dict[str, str]) -> str:
    folder = os.path.join(root, name)
    for rel, text in files.items():
        _write(os.path.join(folder, rel), text)
    return folder


def _bump_mtime(path: str, seconds: float = 5.0) -> None:
    st = os.stat(path)
    os.utime(path, (st.st_atime + seconds, st.st_mtime + seconds))


# ---------------------------------------------------------------- helpers

def test_human_bytes():
    assert core.human_bytes(0) == "0 B"
    assert core.human_bytes(1023) == "1,023 B"
    assert core.human_bytes(1024) == "1.0 KB"
    assert core.human_bytes(5 * 1024 ** 3) == "5.0 GB"


def test_is_inside(tmp_path):
    src = tmp_path / "saves"
    assert core.is_inside(str(src), str(src))
    assert core.is_inside(str(src / "backup"), str(src))
    assert not core.is_inside(str(tmp_path / "backup"), str(src))
    # A sibling whose name merely starts with the parent's name is not inside.
    assert not core.is_inside(str(tmp_path / "saves2"), str(src))


# -------------------------------------------------------------- signature

def test_signature_changes_when_content_changes(tmp_path):
    save = _make_save(str(tmp_path), "slot1", {"a.sav": "one", "sub/b.sav": "two"})
    first = core.entry_signature(save)
    assert first["kind"] == "dir" and first["files"] == 2

    assert core.entry_signature(save) == first  # stable across calls

    _write(os.path.join(save, "a.sav"), "changed")
    _bump_mtime(os.path.join(save, "a.sav"))
    assert core.entry_signature(save)["sig"] != first["sig"]


def test_signature_sees_added_and_removed_files_and_empty_dirs(tmp_path):
    save = _make_save(str(tmp_path), "slot1", {"a.sav": "one"})
    base = core.entry_signature(save)["sig"]

    _write(os.path.join(save, "new.sav"), "x")
    added = core.entry_signature(save)["sig"]
    assert added != base

    os.remove(os.path.join(save, "new.sav"))
    assert core.entry_signature(save)["sig"] == base

    os.makedirs(os.path.join(save, "empty"))
    assert core.entry_signature(save)["sig"] != base


def test_signature_for_file_and_missing(tmp_path):
    f = tmp_path / "single.sav"
    f.write_text("data")
    sig = core.entry_signature(str(f))
    assert sig["kind"] == "file" and sig["files"] == 1 and sig["bytes"] == 4
    assert core.entry_signature(str(tmp_path / "nope")) is None


# ----------------------------------------------------------------- copying

def test_copy_entry_reuses_unchanged_files_with_hardlinks(tmp_path):
    save = _make_save(str(tmp_path), "slot", {"a.sav": "aaa", "b.sav": "bbb", "deep/c.sav": "ccc"})
    v1 = str(tmp_path / "v1")
    tally = core.copy_entry(save, v1)
    assert tally == {"linked": 0, "written": 3, "written_bytes": 9}

    _write(os.path.join(save, "a.sav"), "AAAA")
    _bump_mtime(os.path.join(save, "a.sav"))
    v2 = str(tmp_path / "v2")
    tally = core.copy_entry(save, v2, prev=v1)
    assert tally["written"] == 1 and tally["linked"] == 2
    assert open(os.path.join(v2, "a.sav")).read() == "AAAA"
    assert open(os.path.join(v2, "deep", "c.sav")).read() == "ccc"
    # The reused files are the same inode as the previous version.
    assert os.path.samefile(os.path.join(v1, "b.sav"), os.path.join(v2, "b.sav"))
    assert not os.path.samefile(os.path.join(v1, "a.sav"), os.path.join(v2, "a.sav"))


def test_copy_entry_single_file(tmp_path):
    f = tmp_path / "quick.sav"
    f.write_text("q")
    staging = str(tmp_path / "staging")
    tally = core.copy_entry(str(f), staging)
    assert tally["written"] == 1
    assert (tmp_path / "staging" / "quick.sav").read_text() == "q"


# ---------------------------------------------------------------- versions

def test_version_stamp_avoids_collisions(tmp_path):
    now = datetime(2026, 1, 2, 3, 4, 5)
    first = core.version_stamp(str(tmp_path), now)
    assert first == "2026-01-02_03-04-05"
    os.makedirs(tmp_path / first)
    second = core.version_stamp(str(tmp_path), now)
    assert second == "2026-01-02_03-04-05_1"
    os.makedirs(tmp_path / second)
    assert core.version_stamp(str(tmp_path), now) == "2026-01-02_03-04-05_2"


def test_prune_keeps_newest_and_ignores_foreign_folders(tmp_path):
    names = ["2026-01-01_00-00-00", "2026-01-02_00-00-00", "2026-01-03_00-00-00",
             "2026-01-03_00-00-00_1", "2026-01-04_00-00-00"]
    for n in names:
        os.makedirs(tmp_path / n)
    os.makedirs(tmp_path / "not-a-version")
    (tmp_path / "2026-09-09_09-09-09").write_text("a file, not a folder")

    entries, log = _logs()
    removed = core.prune_versions(str(tmp_path), keep=2, log=log)
    assert removed == names[:3]
    assert sorted(core.list_versions(str(tmp_path))) == names[3:]
    assert (tmp_path / "not-a-version").is_dir()
    assert (tmp_path / "2026-09-09_09-09-09").is_file()
    assert all(level == "prune" for level, _ in entries)


def test_prune_zero_keeps_everything(tmp_path):
    for n in ("2026-01-01_00-00-00", "2026-01-02_00-00-00"):
        os.makedirs(tmp_path / n)
    _, log = _logs()
    assert core.prune_versions(str(tmp_path), keep=0, log=log) == []
    assert len(core.list_versions(str(tmp_path))) == 2


def test_latest_version(tmp_path):
    assert core.latest_version(str(tmp_path)) is None
    for n in ("2026-01-02_00-00-00", "2026-01-01_00-00-00"):
        os.makedirs(tmp_path / n)
    assert core.latest_version(str(tmp_path)) == str(tmp_path / "2026-01-02_00-00-00")


# ------------------------------------------------------------------- state

def test_state_round_trip_and_bad_file(tmp_path):
    root = str(tmp_path)
    assert core.load_state(root) == {"entries": {}}
    core.save_state(root, {"entries": {"x": {"sig": "1"}}, "source": "s"})
    assert core.load_state(root)["entries"] == {"x": {"sig": "1"}}
    with open(os.path.join(root, core.STATE_FILENAME), "w") as fh:
        fh.write("{not json")
    assert core.load_state(root) == {"entries": {}}


# ------------------------------------------------------------------- cycle

def test_full_cycle_new_changed_unchanged_missing(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_save(str(src), "slotA", {"a.sav": "1"})
    _make_save(str(src), "slotB", {"b.sav": "2"})
    state = {"entries": {}}
    entries, log = _logs()

    stats = core.run_backup_cycle(str(src), str(dst), keep=5, state=state, log=log)
    assert stats["copied"] == 2 and stats["errors"] == 0 and stats["checked"] == 2
    assert len(core.list_versions(str(dst / "slotA"))) == 1
    assert (dst / core.STATE_FILENAME).exists()
    assert (dst / core.LOG_FILENAME).exists()
    assert not any(n.endswith(core.STAGING_SUFFIX) for n in os.listdir(dst / "slotA"))

    # Nothing changed: no new versions.
    stats = core.run_backup_cycle(str(src), str(dst), keep=5, state=state, log=log)
    assert stats["copied"] == 0
    assert len(core.list_versions(str(dst / "slotA"))) == 1

    # One changed, one deleted from the source: new version, old ones kept.
    _write(str(src / "slotA" / "a.sav"), "changed")
    _bump_mtime(str(src / "slotA" / "a.sav"))
    import shutil
    shutil.rmtree(src / "slotB")
    time.sleep(1.1)  # distinct timestamp folder
    stats = core.run_backup_cycle(str(src), str(dst), keep=5, state=state, log=log)
    assert stats["copied"] == 1 and stats["removed"] == 1
    assert len(core.list_versions(str(dst / "slotA"))) == 2
    assert len(core.list_versions(str(dst / "slotB"))) == 1
    assert "slotB" not in state["entries"]
    assert any(level == "alert" for level, _ in entries)
    history = (dst / core.LOG_FILENAME).read_text()
    assert "MISSING  slotB" in history and "BACKUP   slotA" in history


def test_cycle_prunes_to_keep(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_save(str(src), "slot", {"f.sav": "0"})
    state = {"entries": {}}
    _, log = _logs()
    for i in range(1, 5):
        _write(str(src / "slot" / "f.sav"), str(i))
        _bump_mtime(str(src / "slot" / "f.sav"), seconds=10 * i)
        core.run_backup_cycle(str(src), str(dst), keep=2, state=state, log=log)
    assert len(core.list_versions(str(dst / "slot"))) == 2


def test_cycle_missing_source_reports_error(tmp_path):
    entries, log = _logs()
    stats = core.run_backup_cycle(str(tmp_path / "gone"), str(tmp_path / "dst"),
                                  keep=1, state={"entries": {}}, log=log)
    assert stats["errors"] == 1 and entries[0][0] == "error"


def test_cycle_honours_cancel(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    for n in ("a", "b", "c"):
        _make_save(str(src), n, {"f": n})
    _, log = _logs()
    stats = core.run_backup_cycle(str(src), str(dst), keep=1, state={"entries": {}},
                                  log=log, cancelled=lambda: True)
    assert stats["copied"] == 0


def test_summarize_stats():
    s = core.empty_stats()
    s["checked"] = 3
    assert core.summarize_stats(s) == "3 saves checked - no changes."
    s.update(copied=1, bytes=2048, linked=4, written_bytes=1024, errors=1)
    out = core.summarize_stats(s)
    assert "1 backed up (2.0 KB)" in out and "4 files reused" in out and "1 error(s)" in out


def test_describe_copy_error():
    import shutil
    err = shutil.Error([("C:/x/a.sav", "D:/y/a.sav", "[Errno 13] Permission denied"),
                        ("C:/x/b.sav", "D:/y/b.sav", "[Errno 13] Permission denied")])
    text = core.describe_copy_error(err)
    assert "a.sav" in text and "+1 more" in text
    perm = PermissionError(13, "denied", "C:/x/locked.sav")
    assert "locked.sav" in core.describe_copy_error(perm)
    assert core.describe_copy_error(RuntimeError("boom")) == "boom"


@pytest.mark.parametrize("name,ok", [
    ("2026-01-02_03-04-05", True),
    ("2026-01-02_03-04-05_7", True),
    ("2026-01-02", False),
    ("_staging.incomplete", False),
])
def test_version_regex(name, ok):
    assert bool(core.VERSION_RE.match(name)) is ok
