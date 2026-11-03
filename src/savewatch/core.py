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
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime
from typing import Callable

STATE_FILENAME = ".savewatch_state.json"
LOG_FILENAME = "SaveWatch_history.log"
STAGING_SUFFIX = ".incomplete"
VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?$")

LogFn = Callable[[str, str], None]


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
    base = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
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
                     log: LogFn, cancelled: Callable[[], bool] = lambda: False) -> dict:
    """One pass.  Mutates `state` and returns a stats dict (see `empty_stats`)."""
    stats = empty_stats()

    if not os.path.isdir(source):
        log("error", f"source folder is gone: {source}")
        stats["errors"] += 1
        return stats
    os.makedirs(backup_root, exist_ok=True)

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
