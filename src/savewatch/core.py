"""Backup engine for SaveWatch.

Pure functions, no Qt.  Everything here is unit-testable on its own and is
shared by the GUI (`savewatch.app`) and the command line (`savewatch.cli`).

How a cycle works
-----------------
The source folder is scanned and every *top-level* entry (file or folder) is
fingerprinted by the sizes and mtimes of everything beneath it.  Any entry
whose fingerprint differs from the previous cycle is copied into the backup
folder as a new timestamped version::

    <source>/ManualSave-5/...        ->  <backup>/ManualSave-5/2026-09-23_14-30-05/...
    <source>/ManualSave-6/  (new)    ->  <backup>/ManualSave-6/2026-09-23_14-30-05/...

Files that did not change since the previous version are hard-linked instead
of copied, so a 250-file save slot is not rewritten because one file changed.
Deletions are never mirrored: if a save disappears from the source its old
versions stay in the backup folder, which is the whole point of the tool.

How a restore works
-------------------
Restoring a version never destroys anything that cannot be brought back::

    1. the live save is copied into  <backup>/.savewatch_undo/<save>/<stamp>/
       (plus a <stamp>.json describing it) - this is the undo point
    2. the chosen version is copied next to the live save under a temp name
    3. live save -> temp "old" name, temp "new" copy -> live name (renames)
    4. the "old" copy is deleted - it is safe in the undo area

Restored files are real copies, never hard links, so a game writing into
them can not reach back into the backup.  Undo points expire after a number
of days (`purge_undo_points`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
from datetime import datetime, timedelta
from typing import Callable

STATE_FILENAME = ".savewatch_state.json"
LOG_FILENAME = "SaveWatch_history.log"
UNDO_DIRNAME = ".savewatch_undo"
STAGING_SUFFIX = ".incomplete"
# Temporary names used inside the *source* folder during a restore.  The
# scanner ignores them so a crash mid-restore can never be backed up as a save.
SOURCE_TEMP_SUFFIX = ".savewatch-tmp"
VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?$")
STAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"

LogFn = Callable[[str, str], None]


def _no_log(level: str, text: str) -> None:
    pass


def human_bytes(n: float) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < step or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} TB"


# --------------------------------------------------------------- scanning

def entry_signature(path: str) -> dict | None:
    """Fingerprint one top-level entry (file or folder).

    Uses size + mtime rather than hashing content: save files can be hundreds
    of MB and this runs on a timer.  Returns None if the entry vanished or is
    unreadable.
    """
    try:
        if os.path.isfile(path):
            st = os.stat(path)
            digest = hashlib.sha1(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()
            return {"kind": "file", "sig": digest, "files": 1, "bytes": st.st_size}

        if not os.path.isdir(path):
            return None

        parts: list[str] = []
        total = 0
        count = 0
        for root, dirs, files in os.walk(path):
            dirs.sort()
            rel_root = os.path.relpath(root, path)
            # Empty folders are part of the identity too.
            if not files:
                parts.append(f"{rel_root}/|dir")
            for name in sorted(files):
                fp = os.path.join(root, name)
                try:
                    st = os.stat(fp)
                except OSError:
                    parts.append(f"{os.path.join(rel_root, name)}|unreadable")
                    continue
                parts.append(f"{os.path.join(rel_root, name)}|{st.st_size}|{st.st_mtime_ns}")
                total += st.st_size
                count += 1
        digest = hashlib.sha1("\n".join(parts).encode("utf-8", "replace")).hexdigest()
        return {"kind": "dir", "sig": digest, "files": count, "bytes": total}
    except OSError:
        return None


def scan_source(root: str) -> dict[str, dict]:
    """Signature of every top-level entry inside `root`."""
    out: dict[str, dict] = {}
    for name in sorted(os.listdir(root)):
        if name.endswith(SOURCE_TEMP_SUFFIX):
            continue  # leftover of an interrupted restore, not a save
        sig = entry_signature(os.path.join(root, name))
        if sig is not None:
            out[name] = sig
    return out


# ---------------------------------------------------------------- copying

def _copy_file_resilient(src: str, dst: str, attempts: int = 4, delay: float = 0.5) -> None:
    """copy2 with retries - the game may be mid-write and holding a lock."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            shutil.copy2(src, dst)
            return
        except (PermissionError, OSError) as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(delay)
    assert last is not None
    raise last


def _unchanged(src_stat: os.stat_result, prev_stat: os.stat_result) -> bool:
    """Same size and same mtime => the byte content is the same file.

    copy2 preserves mtime exactly, so an untouched file's previous backup has
    an identical timestamp.  A small tolerance covers filesystems that round.
    """
    return (src_stat.st_size == prev_stat.st_size
            and abs(src_stat.st_mtime_ns - prev_stat.st_mtime_ns) <= 1000)


def _place_file(src: str, dst: str, prev: str | None, tally: dict) -> None:
    """Hard-link the file from the previous version if it did not change.

    A hard link is a second name for bytes already on disk: it costs no space
    and no I/O.  Falls back to a real copy whenever linking is not possible
    (backup on a different drive, exFAT stick, etc.).
    """
    if prev:
        try:
            if _unchanged(os.stat(src), os.stat(prev)):
                os.link(prev, dst)
                tally["linked"] += 1
                return
        except OSError:
            pass  # different volume / no hard-link support -> just copy it
    _copy_file_resilient(src, dst)
    tally["written"] += 1
    try:
        tally["written_bytes"] += os.stat(dst).st_size
    except OSError:
        pass


def copy_entry(src: str, staging: str, prev: str | None = None) -> dict:
    """Copy one top-level entry into `staging`, reusing `prev` where possible."""
    tally = {"linked": 0, "written": 0, "written_bytes": 0}

    if os.path.isfile(src):
        os.makedirs(staging, exist_ok=True)
        name = os.path.basename(src)
        prev_file = os.path.join(prev, name) if prev else None
        _place_file(src, os.path.join(staging, name), prev_file, tally)
        return tally

    for root, dirs, files in os.walk(src):
        dirs.sort()
        rel = os.path.relpath(root, src)
        target_dir = staging if rel == "." else os.path.join(staging, rel)
        os.makedirs(target_dir, exist_ok=True)  # keeps empty folders too
        prev_dir = None
        if prev:
            prev_dir = prev if rel == "." else os.path.join(prev, rel)
            if not os.path.isdir(prev_dir):
                prev_dir = None
        for name in sorted(files):
            _place_file(
                os.path.join(root, name),
                os.path.join(target_dir, name),
                os.path.join(prev_dir, name) if prev_dir else None,
                tally,
            )
    return tally


# --------------------------------------------------------------- versions

def list_versions(entry_dir: str) -> list[str]:
    """Version folder names inside `entry_dir`, oldest first."""
    try:
        return sorted(d for d in os.listdir(entry_dir)
                      if VERSION_RE.match(d) and os.path.isdir(os.path.join(entry_dir, d)))
    except OSError:
        return []


def latest_version(entry_dir: str) -> str | None:
    """Newest existing version folder for an entry, or None."""
    versions = list_versions(entry_dir)
    return os.path.join(entry_dir, versions[-1]) if versions else None


def version_stamp(entry_dir: str, now: datetime | None = None) -> str:
    base = (now or datetime.now()).strftime(STAMP_FORMAT)
    name = base
    n = 1
    while os.path.exists(os.path.join(entry_dir, name)):
        name = f"{base}_{n}"
        n += 1
    return name


def prune_versions(entry_dir: str, keep: int, log: LogFn) -> list[str]:
    """Delete all but the newest `keep` versions.  0 keeps everything.

    Returns the names of the versions that were removed.
    """
    removed: list[str] = []
    if keep <= 0:
        return removed
    versions = list_versions(entry_dir)
    for old in versions[:-keep] if len(versions) > keep else []:
        try:
            shutil.rmtree(os.path.join(entry_dir, old))
            removed.append(old)
            log("prune", f"removed old version {os.path.basename(entry_dir)}/{old}")
        except OSError as exc:
            log("warn", f"could not prune {old}: {exc}")
    return removed


def describe_copy_error(exc: Exception) -> str:
    """Turn a copy failure into one readable line."""
    if isinstance(exc, shutil.Error) and exc.args and isinstance(exc.args[0], list):
        items = exc.args[0]
        first = items[0]
        src = os.path.basename(first[0]) if len(first) > 0 else "?"
        why = str(first[2]) if len(first) > 2 else str(exc)
        why = why.split(": ", 1)[0] if "Errno" in why else why
        more = f" (+{len(items) - 1} more)" if len(items) > 1 else ""
        return f"{why} on '{src}'{more} - likely still open in the game"
    if isinstance(exc, PermissionError):
        name = os.path.basename(getattr(exc, "filename", "") or "")
        return f"permission denied on '{name}' - likely still open in the game"
    return str(exc)


# ------------------------------------------------------------------ state

def load_state(backup_root: str) -> dict:
    path = os.path.join(backup_root, STATE_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"entries": {}}


def save_state(backup_root: str, state: dict) -> None:
    path = os.path.join(backup_root, STATE_FILENAME)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


def append_history(backup_root: str, line: str) -> None:
    try:
        with open(os.path.join(backup_root, LOG_FILENAME), "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n")
    except OSError:
        pass


# ------------------------------------------------------------------ cycle

def empty_stats() -> dict:
    return {"copied": 0, "bytes": 0, "errors": 0, "removed": 0, "checked": 0,
            "written_bytes": 0, "linked": 0}


def is_inside(child: str, parent: str) -> bool:
    """True if `child` is `parent` or lives anywhere beneath it."""
    c = os.path.normcase(os.path.normpath(os.path.abspath(child)))
    p = os.path.normcase(os.path.normpath(os.path.abspath(parent)))
    return c == p or c.startswith(p + os.sep)


def run_backup_cycle(source: str, backup_root: str, keep: int, state: dict,
                     log: LogFn, cancelled: Callable[[], bool] = lambda: False,
                     undo_days: int | None = None) -> dict:
    """One pass.  Mutates `state` and returns a stats dict (see `empty_stats`).

    `undo_days` (when given) also expires restore undo points older than that.
    """
    stats = empty_stats()

    if not os.path.isdir(source):
        log("error", f"source folder is gone: {source}")
        stats["errors"] += 1
        return stats
    os.makedirs(backup_root, exist_ok=True)
    if undo_days is not None:
        purge_undo_points(backup_root, undo_days, log)

    current = scan_source(source)
    previous = state.setdefault("entries", {})
    stats["checked"] = len(current)

    for name in sorted(previous):
        if name not in current:
            log("alert", f"'{name}' disappeared from the source - its backups are kept")
            append_history(backup_root, f"MISSING  {name} (backups retained)")
            previous.pop(name, None)
            stats["removed"] += 1

    for name, sig in current.items():
        if cancelled():
            log("info", "stopped mid-cycle")
            break
        old = previous.get(name)
        if old and old.get("sig") == sig["sig"]:
            continue

        reason = "new" if old is None else "changed"
        entry_dir = os.path.join(backup_root, name)
        staging = os.path.join(entry_dir, "_staging" + STAGING_SUFFIX)
        try:
            os.makedirs(entry_dir, exist_ok=True)
            if os.path.exists(staging):
                shutil.rmtree(staging, ignore_errors=True)
            tally = copy_entry(os.path.join(source, name), staging, latest_version(entry_dir))
            final = os.path.join(entry_dir, version_stamp(entry_dir))
            os.replace(staging, final)
        except Exception as exc:  # noqa: BLE001 - one bad entry must not kill the cycle
            detail = describe_copy_error(exc)
            stats["errors"] += 1
            log("error", f"could not back up '{name}': {detail} - will retry next check")
            append_history(backup_root, f"ERROR    {name}: {detail}")
            shutil.rmtree(staging, ignore_errors=True)
            continue

        # Re-fingerprint: the game may have written more while we copied, and
        # storing the *pre-copy* signature would hide that from the next pass.
        previous[name] = entry_signature(os.path.join(source, name)) or sig
        stats["copied"] += 1
        stats["bytes"] += sig["bytes"]
        stats["written_bytes"] += tally["written_bytes"]
        stats["linked"] += tally["linked"]

        detail = f"{sig['files']} files, {human_bytes(sig['bytes'])}"
        if tally["linked"]:
            detail += (f" - only {tally['written']} rewritten "
                       f"({human_bytes(tally['written_bytes'])}), {tally['linked']} reused")
        log("ok", f"{reason}: '{name}' -> {os.path.basename(final)} ({detail})")
        append_history(backup_root, f"BACKUP   {name} -> {os.path.basename(final)} ({detail})")
        prune_versions(entry_dir, keep, log)

    state["entries"] = previous
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    state["source"] = source
    save_state(backup_root, state)
    return stats


def summarize_stats(stats: dict) -> str:
    """One-line summary of a cycle for logs and the tray tooltip."""
    bits = [f"{stats['checked']} saves checked"]
    if stats["copied"]:
        bits.append(f"{stats['copied']} backed up ({human_bytes(stats['bytes'])})")
        if stats["linked"]:
            bits.append(f"{human_bytes(stats['written_bytes'])} actually written, "
                        f"{stats['linked']} files reused from the last version")
    else:
        bits.append("no changes")
    if stats["errors"]:
        bits.append(f"{stats['errors']} error(s)")
    return " - ".join(bits) + "."


# --------------------------------------------------------------- browsing

def parse_stamp(name: str) -> datetime | None:
    """Creation time encoded in a version / undo folder name."""
    if not VERSION_RE.match(name):
        return None
    try:
        return datetime.strptime(name[:19], STAMP_FORMAT)
    except ValueError:
        return None


def humanize_age(then: datetime | None, now: datetime | None = None) -> str:
    """'just now', '5 minutes ago', 'yesterday', '3 weeks ago' ..."""
    if then is None:
        return "unknown"
    secs = int(((now or datetime.now()) - then).total_seconds())
    if secs < 0:
        return "just now"

    def unit(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'} ago"

    if secs < 45:
        return "just now"
    if secs < 3600:
        return unit(max(1, round(secs / 60)), "minute")
    if secs < 86400:
        return unit(secs // 3600, "hour")
    days = secs // 86400
    if days == 1:
        return "yesterday"
    if days < 14:
        return unit(days, "day")
    if days < 60:
        return unit(days // 7, "week")
    if days < 365:
        return unit(days // 30, "month")
    return unit(days // 365, "year")


def folder_stats(path: str) -> tuple[int, int]:
    """(file count, total bytes) of everything beneath `path`."""
    files = total = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += os.stat(os.path.join(root, name)).st_size
                files += 1
            except OSError:
                pass
    return files, total


def list_backup_entries(backup_root: str) -> list[dict]:
    """Every save that has at least one version, A-Z.

    Each item: name, versions (count), latest (folder name), latest_time.
    """
    out: list[dict] = []
    try:
        names = os.listdir(backup_root)
    except OSError:
        return out
    for name in sorted(names, key=str.lower):
        path = os.path.join(backup_root, name)
        if name == UNDO_DIRNAME or not os.path.isdir(path):
            continue
        versions = list_versions(path)
        if not versions:
            continue
        out.append({"name": name, "versions": len(versions), "latest": versions[-1],
                    "latest_time": parse_stamp(versions[-1])})
    return out


def version_details(backup_root: str, name: str, version: str) -> dict:
    """Everything the UI shows about one version."""
    path = os.path.join(backup_root, name, version)
    files, size = folder_stats(path)
    return {"name": name, "version": version, "path": path,
            "created": parse_stamp(version), "files": files, "bytes": size}


# ---------------------------------------------------------------- restore

def _make_writable_and_retry(func, path, _exc) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_path(path: str) -> None:
    """Delete a file or a whole folder, read-only files included."""
    if os.path.isdir(path) and not os.path.islink(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_make_writable_and_retry)
        else:  # pragma: no cover - exercised on 3.10/3.11
            shutil.rmtree(path, onerror=_make_writable_and_retry)
    elif os.path.lexists(path):
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass
        os.remove(path)


def entry_kind(source: str, name: str, data_dir: str) -> str:
    """'file' or 'dir': what shape the save takes in the source folder.

    A single-file save is stored as <version>/<name>; a folder save stores the
    folder's contents.  Only a folder holding exactly one file of its own name
    is ambiguous, and then the live save (if any) decides.
    """
    try:
        items = os.listdir(data_dir)
    except OSError:
        return "dir"
    single = items == [name] and os.path.isfile(os.path.join(data_dir, name))
    if not single:
        return "dir"
    return "dir" if os.path.isdir(os.path.join(source, name)) else "file"


def _undo_dir(backup_root: str, name: str) -> str:
    return os.path.join(backup_root, UNDO_DIRNAME, name)


def create_undo_point(source: str, backup_root: str, name: str, reason: str) -> str:
    """Snapshot the live save `name` into the undo area.  Returns the undo id.

    If the save does not exist right now the undo point records that, and
    undoing it removes the save again.
    """
    live = os.path.join(source, name)
    undo_dir = _undo_dir(backup_root, name)
    os.makedirs(undo_dir, exist_ok=True)
    stamp = version_stamp(undo_dir)
    while os.path.exists(os.path.join(undo_dir, stamp + ".json")):
        stamp = version_stamp(undo_dir, datetime.now() + timedelta(seconds=1))

    meta = {"entry": name, "created": datetime.now().isoformat(timespec="seconds"),
            "reason": reason, "absent": not os.path.lexists(live), "kind": None,
            "files": 0, "bytes": 0}
    if not meta["absent"]:
        staging = os.path.join(undo_dir, "_staging" + STAGING_SUFFIX)
        if os.path.exists(staging):
            remove_path(staging)
        try:
            # Unchanged files are hard-linked from the newest backup version:
            # both live in the backup area, so that is safe and free.
            copy_entry(live, staging, latest_version(os.path.join(backup_root, name)))
            os.replace(staging, os.path.join(undo_dir, stamp))
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        meta["kind"] = "file" if os.path.isfile(live) else "dir"
        meta["files"], meta["bytes"] = folder_stats(os.path.join(undo_dir, stamp))

    with open(os.path.join(undo_dir, stamp + ".json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    return stamp


def list_undo_points(backup_root: str, name: str | None = None) -> list[dict]:
    """Undo points, newest first.  Limited to one save when `name` is given."""
    root = os.path.join(backup_root, UNDO_DIRNAME)
    try:
        entries = [name] if name is not None else sorted(os.listdir(root))
    except OSError:
        return []
    out: list[dict] = []
    for entry in entries:
        folder = os.path.join(root, entry)
        try:
            files = os.listdir(folder)
        except OSError:
            continue
        for fn in files:
            stamp = fn[:-5]
            if not fn.endswith(".json") or not VERSION_RE.match(stamp):
                continue
            try:
                with open(os.path.join(folder, fn), "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            meta["id"] = stamp
            meta["entry"] = entry
            meta["created_time"] = parse_stamp(stamp)
            out.append(meta)
    out.sort(key=lambda m: (m["created_time"] or datetime.min, m["id"]), reverse=True)
    return out


def delete_undo_point(backup_root: str, name: str, undo_id: str) -> None:
    folder = _undo_dir(backup_root, name)
    data = os.path.join(folder, undo_id)
    if os.path.isdir(data):
        remove_path(data)
    meta = os.path.join(folder, undo_id + ".json")
    if os.path.exists(meta):
        os.remove(meta)
    try:
        os.rmdir(folder)  # only succeeds once it is empty
        os.rmdir(os.path.dirname(folder))
    except OSError:
        pass


def purge_undo_points(backup_root: str, max_age_days: int, log: LogFn = _no_log,
                      now: datetime | None = None) -> int:
    """Delete undo points older than `max_age_days` (0 keeps them forever)."""
    if max_age_days <= 0:
        return 0
    cutoff = (now or datetime.now()) - timedelta(days=max_age_days)
    removed = 0
    for point in list_undo_points(backup_root):
        created = point["created_time"]
        if created is not None and created < cutoff:
            try:
                delete_undo_point(backup_root, point["entry"], point["id"])
                removed += 1
                log("prune", f"expired undo point {point['entry']}/{point['id']}")
            except OSError as exc:
                log("warn", f"could not remove undo point {point['id']}: {exc}")
    return removed


def _swap_into_source(source: str, name: str, data_dir: str | None, kind: str | None) -> None:
    """Make source/<name> look exactly like `data_dir` (None = remove it).

    The new copy is fully written before the live save is touched, and the
    live save is only renamed (not deleted) until the new one is in place, so
    a failure at any step leaves the original save where it was.
    """
    live = os.path.join(source, name)
    new_tmp = live + ".new" + SOURCE_TEMP_SUFFIX
    old_tmp = live + ".old" + SOURCE_TEMP_SUFFIX
    for leftover in (new_tmp, old_tmp):
        if os.path.lexists(leftover):
            remove_path(leftover)

    if data_dir is not None:
        try:
            if kind == "file":
                _copy_file_resilient(os.path.join(data_dir, name), new_tmp)
            else:
                copy_entry(data_dir, new_tmp)  # no `prev`: real copies, never links
        except Exception:
            if os.path.lexists(new_tmp):
                remove_path(new_tmp)
            raise

    had_live = os.path.lexists(live)
    if had_live:
        try:
            os.replace(live, old_tmp)
        except Exception:
            if os.path.lexists(new_tmp):
                remove_path(new_tmp)
            raise
    try:
        if data_dir is not None:
            os.replace(new_tmp, live)
    except Exception:
        if had_live:
            os.replace(old_tmp, live)  # put the original back
        if os.path.lexists(new_tmp):
            remove_path(new_tmp)
        raise

    if had_live:
        try:
            remove_path(old_tmp)
        except OSError:
            pass  # ignored by the scanner; cleaned up by the next restore


def _remember_restored(source: str, backup_root: str, name: str) -> None:
    """Record the restored save as known, so the next check does not back it
    up again as if it were a brand-new change."""
    state = load_state(backup_root)
    sig = entry_signature(os.path.join(source, name))
    if sig is None:
        state["entries"].pop(name, None)
    else:
        state["entries"][name] = sig
    save_state(backup_root, state)


def restore_version(source: str, backup_root: str, name: str, version: str,
                    log: LogFn = _no_log) -> dict:
    """Put backup version `version` of save `name` back into the source folder.

    The current save is kept as an undo point first.  Returns
    {"name", "version", "undo_id", "files", "bytes"}.
    """
    if not os.path.isdir(source):
        raise FileNotFoundError(f"source folder is gone: {source}")
    data_dir = os.path.join(backup_root, name, version)
    if not VERSION_RE.match(version) or not os.path.isdir(data_dir):
        raise FileNotFoundError(f"no backup version '{version}' for '{name}'")

    kind = entry_kind(source, name, data_dir)
    undo_id = create_undo_point(source, backup_root, name, f"before restoring {version}")
    try:
        _swap_into_source(source, name, data_dir, kind)
    except Exception:
        delete_undo_point(backup_root, name, undo_id)  # nothing changed, nothing to undo
        raise
    _remember_restored(source, backup_root, name)

    files, size = folder_stats(data_dir)
    log("ok", f"restored '{name}' to version {version} ({files} files, {human_bytes(size)}) "
              f"- previous save kept as undo point {undo_id}")
    append_history(backup_root, f"RESTORE  {name} <- {version} (undo point {undo_id})")
    return {"name": name, "version": version, "undo_id": undo_id, "files": files, "bytes": size}


def undo_restore(source: str, backup_root: str, name: str, undo_id: str,
                 log: LogFn = _no_log) -> dict:
    """Bring back the save as it was when undo point `undo_id` was taken.

    What is live right now becomes a new undo point (so an undo can itself be
    undone) and the used undo point is deleted.
    """
    if not os.path.isdir(source):
        raise FileNotFoundError(f"source folder is gone: {source}")
    points = {p["id"]: p for p in list_undo_points(backup_root, name)}
    meta = points.get(undo_id)
    if meta is None:
        raise FileNotFoundError(f"no undo point '{undo_id}' for '{name}'")

    data_dir = None if meta.get("absent") else os.path.join(_undo_dir(backup_root, name), undo_id)
    if data_dir is not None and not os.path.isdir(data_dir):
        raise FileNotFoundError(f"undo point '{undo_id}' is missing its files")
    kind = meta.get("kind") or (entry_kind(source, name, data_dir) if data_dir else None)

    new_id = create_undo_point(source, backup_root, name, f"before undoing {undo_id}")
    try:
        _swap_into_source(source, name, data_dir, kind)
    except Exception:
        delete_undo_point(backup_root, name, new_id)
        raise
    delete_undo_point(backup_root, name, undo_id)
    _remember_restored(source, backup_root, name)

    what = "removed again (it did not exist then)" if data_dir is None else "put back"
    log("ok", f"undo: '{name}' as of {undo_id} {what} - redo point {new_id}")
    append_history(backup_root, f"UNDO     {name} <- undo point {undo_id} (redo point {new_id})")
    return {"name": name, "undo_id": new_id, "restored": undo_id}
