"""Command-line front end: `savewatch --cli ...` or `python -m savewatch --cli`.

Runs the same engine as the window, without Qt, so it can be scripted or
run from a scheduler.  With no flags it uses the saved settings from the GUI.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

from .config import Settings
from .core import is_inside, load_state, run_backup_cycle, summarize_stats


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="savewatch --cli",
        description="Watch a save folder and keep timestamped copies of anything that changes.",
    )
    p.add_argument("--source", help="folder to watch (default: saved GUI setting)")
    p.add_argument("--backup", help="backup destination (default: saved GUI setting)")
    p.add_argument("--interval", type=float, help="minutes between checks (default: saved setting)")
    p.add_argument("--keep", type=int, help="versions to keep per save, 0 = unlimited")
    p.add_argument("--once", action="store_true", help="run one backup cycle and exit")
    p.add_argument("--fresh", action="store_true",
                   help="ignore the saved state and back up every save once")
    return p


def _log(level: str, text: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    stream = sys.stderr if level in ("error", "warn", "alert") else sys.stdout
    print(f"{stamp} [{level:5}] {text}", file=stream, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    s = Settings.load()
    source = os.path.normpath(args.source or s.source or "")
    backup = os.path.normpath(args.backup or s.backup or "")
    interval = args.interval if args.interval is not None else s.interval_minutes
    keep = args.keep if args.keep is not None else s.keep

    if not source or not os.path.isdir(source):
        _log("error", f"source folder does not exist: {source or '(none)'}")
        return 2
    if not backup:
        _log("error", "no backup folder given (use --backup)")
        return 2
    if is_inside(backup, source):
        _log("error", "the backup folder must not be inside the folder being watched")
        return 2
    if interval <= 0:
        _log("error", "interval must be positive")
        return 2

    state = {"entries": {}} if args.fresh else load_state(backup)
    _log("info", f"watching {source} -> {backup} every {interval:g} min, keeping {keep or 'all'}")
    try:
        while True:
            stats = run_backup_cycle(source, backup, keep, state, _log)
            _log("info", summarize_stats(stats))
            if args.once:
                return 1 if stats["errors"] else 0
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        _log("info", "stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
