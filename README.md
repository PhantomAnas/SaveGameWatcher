# SaveGameWatcher

**Free, open-source game save backup tool for Windows (Python, PySide6): automatically keeps timestamped versions of your save files so a corrupted, overwritten or deleted save is always one click away. Works with any game that saves to a folder.**

SaveWatch: a versioned backup watcher for game save folders. Give each game a profile (its save folder and a backup folder); every few minutes SaveWatch re-scans and copies each save that changed into a new timestamped version. Deleted saves are **never** removed from the backup, and any version can be put back with one click, with an undo in case you change your mind.
This helps prevent corrupt save games or lost due to power failures or crashes, or if you want to rewind in choice based games.

![SaveWatch: game profiles and the watcher](docs/screenshot.png)

![SaveWatch: browsing versions and restoring a save](docs/backups.png)

## Why

Most games keep a handful of rolling autosaves and silently overwrite them. SaveWatch keeps its own history beside the game:

```
<backup>/
  ManualSave-5/
    2026-09-23_14-30-05/   ← every version is a complete, restorable copy
    2026-09-23_14-35-05/
  QuickSave-1/
    2026-09-23_14-31-12/
  .savewatch_undo/         ← saves replaced by a restore, kept for N days
  SaveWatch_history.log
```

## Features

- **Game profiles**: one profile per game, each with its own save folder, backup folder, check interval and versions to keep. Pick a save folder and the game's name is filled in for you (`...\Baldur's Gate 3\PlayerProfiles\Public\Savegames\Story` → *Baldur's Gate 3*).
- **Backups & Restore browser**: every save slot, every version with its date, time and age ("3 hours ago"), file count and size.
- **Safe one-click restore**: the save you have now is copied to an undo point first, the old version is fully copied next to it, then the two are swapped. If the game has a file locked, nothing is changed.
- **Undo** any restore (and undo the undo). Undo points expire after 7 days by default (configurable, or keep forever).
- **Change detection by fingerprint**: size and mtime of every file under each save slot, so a 300 MB save folder is scanned in milliseconds.
- **Hard-link dedup**: files that did not change since the last version are hard-linked, not copied. A 250-file slot where one file changed costs one file of disk.
- **Keep-N pruning** per save (0 = unlimited).
- **Never mirrors deletions**; a save that vanishes from the game is flagged, its versions stay.
- **Lock-tolerant**: retries files the game is still writing and moves on, nothing partial is ever left behind (versions are staged and renamed atomically).
- **System tray**: keeps watching when the window is closed, with Start/Stop and Back up now in the tray menu.
- **Start with Windows** (minimized, optional auto-start watching).
- **Command line mode** for scripts and schedulers: `savewatch --cli`.
- All copying runs on a worker thread; the window never freezes.

## Install

**Windows exe** (no Python needed): download `SaveWatch.exe` from the [latest release](https://github.com/PhantomAnas/SaveGameWatcher/releases/latest) and run it. It is built by GitHub Actions from the tagged source.

**From source** (Windows, macOS, Linux):

```
git clone https://github.com/PhantomAnas/SaveGameWatcher
cd SaveGameWatcher
python -m venv .venv && .venv\Scripts\activate      # or: source .venv/bin/activate
pip install -e .
savewatch
```

Or skip the install and run `python launcher.py` from the checkout (that Python needs `pip install -r requirements.txt`).

## Use

1. **+ New game** in the sidebar, then pick the game's **save folder** (for example `%USERPROFILE%\Saved Games\<game>` or `%LOCALAPPDATA%\<game>\Saved`). The name and a backup folder are suggested for you.
2. **Backup folder**: anywhere outside the save folder, ideally another drive. Every game needs its own backup folder.
3. Pick the check interval and how many versions to keep per save, then **Start watching**.

### Restoring a save

1. Close the game.
2. Open **Backups & Restore**, pick the save slot, then the version (newest first, with its date and how long ago it was made).
3. **Restore this version**. Your current save is kept as an undo point; it is listed under **Undo points** for that slot. **Undo** puts it back.

Settings (profiles included) are saved to the per-user config directory (on Windows: `%LOCALAPPDATA%\SaveWatch\settings.json`). Settings from 0.1.x are turned into a profile automatically.

### Command line

```
savewatch --cli                                  # watch the profile selected in the window
savewatch --cli --profile "Elden Ring" --once    # one cycle for a named profile, then exit
savewatch --cli --list-profiles
savewatch --cli --profile "Elden Ring" --list    # saves and their versions
savewatch --cli --profile "Elden Ring" --restore ER0000.sl2 --version 2026-11-22_14-30-05
savewatch --cli --profile "Elden Ring" --undo ER0000.sl2
savewatch --cli --source "C:\...\Savegames" --backup "D:\SaveBackups\Game" --interval 5 --keep 10
savewatch --cli --once --fresh                   # ignore known state and copy every save once
```

`--restore` without `--version` restores the newest version. Exit code is 0 on success.

## Development

```
pip install -e ".[dev]"
pytest                      # engine, config and CLI tests, no display needed
pyinstaller savewatch.spec  # builds dist/SaveWatch.exe
python scripts/make_icon.py # regenerate the icon
python scripts/screenshot.py # regenerate docs/screenshot.png and docs/backups.png
```

Layout: `src/savewatch/core.py` is the engine (backup, restore and undo; pure functions, no Qt), `app.py` is the PySide6 window and tray, `theme.py` the neon stylesheet, `cli.py` the command line, `config.py` the settings file and game profiles, `autostart.py` the Windows Run-key entry.

## Notes

- Hard links need source and backup on an NTFS/ext4-style volume. On exFAT or across different drives SaveWatch silently falls back to full copies.
- The backup folder must not be inside the watched folder (the app refuses, otherwise it would back up its own backups).
- Restored files are real copies, never hard links, so a game writing to them can never change a backup.
- While a profile is being watched the other profiles are locked; stop watching to switch games.
- Nothing leaves your machine. No telemetry, no network access.

## License

MIT. PySide6 is used under the LGPL.
