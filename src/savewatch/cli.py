"""Command-line front end: `savewatch --cli ...` or `python -m savewatch --cli`.

Runs the same engine as the window, without Qt, so it can be scripted or
run from a scheduler.  With no flags it uses the profile selected in the GUI.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

from .config import Settings
from .core import (
    create_checkpoint,
    find_checkpoint,
    human_bytes,
    humanize_age,
    is_inside,
    list_backup_entries,
    list_undo_points,
    list_versions,
    load_checkpoints,
    load_state,
    parse_stamp,
    restore_checkpoint,
    restore_version,
    run_backup_cycle,
    summarize_stats,
    undo_restore,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="savewatch --cli",
        description="Watch a save folder and keep timestamped copies of anything that changes.",
    )
    p.add_argument("--profile", help="game profile to use, by name or id (default: the one "
                                     "selected in the window)")
    p.add_argument("--list-profiles", action="store_true", help="list game profiles and exit")
    p.add_argument("--source", help="folder to watch (overrides the profile)")
    p.add_argument("--backup", help="backup destination (overrides the profile)")
    p.add_argument("--interval", type=float, help="minutes between checks")
    p.add_argument("--keep", type=int, help="versions to keep per save, 0 = unlimited")
    p.add_argument("--once", action="store_true", help="run one backup cycle and exit")
    p.add_argument("--fresh", action="store_true",
                   help="ignore the saved state and back up every save once")
    p.add_argument("--list", action="store_true",
                   help="list backed-up saves and their versions, then exit")
    p.add_argument("--restore", metavar="SAVE",
                   help="restore SAVE from the backup (newest version unless --version)")
    p.add_argument("--version", dest="version", metavar="STAMP",
                   help="version folder to restore, e.g. 2026-11-22_14-30-05")
    p.add_argument("--undo", metavar="SAVE",
                   help="undo the last restore of SAVE (uses its newest undo point)")
    p.add_argument("--checkpoint", metavar="TITLE",
                   help="save a named checkpoint of every save right now, then exit")
    p.add_argument("--list-checkpoints", action="store_true", help="list checkpoints and exit")
    p.add_argument("--restore-checkpoint", metavar="TITLE_OR_ID",
                   help="put every save of a checkpoint back (each gets an undo point)")
    return p


def _log(level: str, text: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    stream = sys.stderr if level in ("error", "warn", "alert") else sys.stdout
    print(f"{stamp} [{level:5}] {text}", file=stream, flush=True)


def _list_backups(backup: str) -> int:
    entries = list_backup_entries(backup)
    if not entries:
        print(f"no backups in {backup}")
        return 0
    for e in entries:
        print(f"{e['name']}  ({e['versions']} versions)")
        for v in reversed(list_versions(os.path.join(backup, e["name"]))):
            print(f"    {v}   {humanize_age(parse_stamp(v))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    s = Settings.load()

    if args.list_profiles:
        if not s.profiles:
            print("no profiles yet - create one in the window")
        for p in s.profiles:
            mark = "*" if p.id == s.active_profile else " "
            print(f"{mark} {p.name}  [{p.id}]\n    {p.source or '(no save folder)'}\n"
                  f"    -> {p.backup or '(no backup folder)'}")
        return 0

    if args.profile:
        profile = s.find(args.profile)
        if profile is None:
            _log("error", f"no profile named '{args.profile}' (see --list-profiles)")
            return 2
    else:
        profile = s.active()

    source = os.path.normpath(args.source or (profile.source if profile else "") or "")
    backup = os.path.normpath(args.backup or (profile.backup if profile else "") or "")
    interval = args.interval if args.interval is not None else (
        profile.interval_minutes if profile else 5.0)
    keep = args.keep if args.keep is not None else (profile.keep if profile else 10)

    if source in ("", ".") or not os.path.isdir(source):
        _log("error", f"source folder does not exist: {source if source != '.' else '(none)'}")
        return 2
    if backup in ("", "."):
        _log("error", "no backup folder given (use --backup)")
        return 2
    if is_inside(backup, source) or is_inside(source, backup):
        _log("error", "the backup folder and the folder being watched must not contain each other")
        return 2

    if args.list:
        return _list_backups(backup)

    if args.list_checkpoints:
        items = load_checkpoints(backup)
        if not items:
            print(f"no checkpoints in {backup}")
        for cp in items:
            print(f"{cp['title']}  [{cp['id']}]  {humanize_age(cp['created_time'])}, "
                  f"{len(cp['entries'])} saves, {human_bytes(cp.get('bytes', 0))}")
        return 0

    try:
        if args.checkpoint is not None:
            cp = create_checkpoint(source, backup, args.checkpoint, _log)
            return 1 if cp["errors"] else 0
        if args.restore_checkpoint:
            cp = find_checkpoint(backup, args.restore_checkpoint)
            if cp is None:
                _log("error", f"no checkpoint '{args.restore_checkpoint}' (see --list-checkpoints)")
                return 2
            res = restore_checkpoint(source, backup, cp["id"], _log)
            return 1 if res["errors"] else 0
        if args.restore:
            version = args.version or (list_versions(os.path.join(backup, args.restore)) or [None])[-1]
            if not version:
                _log("error", f"no backups of '{args.restore}' in {backup}")
                return 2
            res = restore_version(source, backup, args.restore, version, _log)
            _log("info", f"{res['files']} files, {human_bytes(res['bytes'])} restored; "
                         f"undo with: --undo \"{args.restore}\"")
            return 0
        if args.undo:
            points = list_undo_points(backup, args.undo)
            if not points:
                _log("error", f"nothing to undo for '{args.undo}'")
                return 2
            undo_restore(source, backup, args.undo, points[0]["id"], _log)
            return 0
    except (OSError, ValueError) as exc:
        _log("error", str(exc))
        return 1

    if interval <= 0:
        _log("error", "interval must be positive")
        return 2

    state = {"entries": {}} if args.fresh else load_state(backup)
    label = f"[{profile.name}] " if profile and not args.source else ""
    _log("info", f"{label}watching {source} -> {backup} every {interval:g} min, "
                 f"keeping {keep or 'all'}")
    try:
        while True:
            stats = run_backup_cycle(source, backup, keep, state, _log, undo_days=s.undo_days)
            _log("info", summarize_stats(stats))
            if args.once:
                return 1 if stats["errors"] else 0
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        _log("info", "stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
