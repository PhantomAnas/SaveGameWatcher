"""Render the window with demo data and save the README screenshots.

    python scripts/screenshot.py

Writes docs/screenshot.png (Watch tab), docs/backups.png (Backups & Restore
tab), docs/checkpoints.png (Checkpoints tab) and docs/picker.png (game picker).
Uses a throw-away settings file and a temp folder full of fake backups,
so the real user config is untouched.  Paths shown on screen are placeholders.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

# On Windows the real platform is used (the window flashes for a moment) because
# the offscreen backend has no access to the system fonts.  Elsewhere go offscreen.
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import savewatch.config as config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="savewatch-shot-")
config.config_path = lambda: os.path.join(tmp, "settings.json")  # type: ignore[assignment]

from savewatch import autostart, core  # noqa: E402
from savewatch.app import SaveWatchWindow, setup_app  # noqa: E402

autostart.is_enabled = lambda: False  # never touch the registry for a screenshot
autostart.set_enabled = lambda enabled: None

NOW = datetime.now().replace(microsecond=0)
FAKE_SRC = r"C:\Users\you\Documents\The Witcher 3\gamesaves"
FAKE_DST = r"D:\SaveBackups\The Witcher 3"
MAIN_SLOT = "ManualSave_12.sav"


def make_version(backup: str, slot: str, when: datetime, size: int) -> None:
    # The Witcher 3 saves are single files, stored as <version>/<file name>.
    folder = os.path.join(backup, slot, when.strftime(core.STAMP_FORMAT))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, slot), "wb") as fh:
        fh.truncate(size)  # sparse: right size, no real bytes


def build_demo_backup() -> str:
    backup = os.path.join(tmp, "backup")
    saves = {
        "AutoSave_3": [(0, 2, 10), (0, 1, 30), (0, 0, 45), (0, 0, 9)],
        "ManualSave_12": [(9, 0, 0), (6, 3, 0), (4, 1, 20), (2, 5, 0), (1, 0, 40),
                          (0, 5, 10), (0, 3, 2), (0, 1, 12)],
        "ManualSave_13": [(3, 4, 0), (1, 2, 30), (0, 6, 15)],
        "QuickSave": [(0, 4, 0), (0, 0, 22)],
    }
    # Every Witcher 3 save is a .sav plus a .png thumbnail, written together.
    plan = {}
    for base, ages in saves.items():
        plan[f"{base}.sav"] = ages
        plan[f"{base}.png"] = ages
    for slot, ages in plan.items():
        for i, (d, h, m) in enumerate(ages):
            size = 4_300_000 + 90_000 * i if slot.endswith(".sav") else 310_000
            make_version(backup, slot, NOW - timedelta(days=d, hours=h, minutes=m), size)
    checkpoints = []
    for title, (d, h, m) in (("Before the Bloody Baron's choice", (6, 3, 0)),
                             ("Before the Battle of Kaer Morhen", (2, 5, 0)),
                             ("Before the final battle", (0, 5, 10))):
        when = NOW - timedelta(days=d, hours=h, minutes=m)
        stamp = when.strftime(core.STAMP_FORMAT)
        for slot in plan:
            make_version(backup, slot, when, 4_700_000 if slot.endswith(".sav") else 310_000)
        checkpoints.append({"id": stamp, "title": title, "created": when.isoformat(),
                            "entries": [{"name": s, "version": stamp} for s in plan],
                            "files": len(plan), "bytes": 4 * (4_700_000 + 310_000)})
    with open(os.path.join(backup, core.CHECKPOINTS_FILENAME), "w") as fh:
        json.dump({"checkpoints": checkpoints}, fh)
    undo_dir = os.path.join(backup, core.UNDO_DIRNAME, MAIN_SLOT)
    for minutes, reason in ((26, "before restoring 2026-11-20_19-02-11"),
                            (190, "before restoring checkpoint 'Before the final battle'")):
        stamp = (NOW - timedelta(minutes=minutes)).strftime(core.STAMP_FORMAT)
        os.makedirs(os.path.join(undo_dir, stamp), exist_ok=True)
        with open(os.path.join(undo_dir, stamp + ".json"), "w") as fh:
            json.dump({"entry": MAIN_SLOT, "reason": reason, "absent": False,
                       "kind": "file", "files": 1, "bytes": 4_820_000}, fh)
    return backup


app = setup_app(sys.argv[0])
backup = build_demo_backup()

s = config.Settings()
s.download_covers = False  # screenshots use the initials tiles, no downloaded art
witcher = s.add_profile("The Witcher 3", source=FAKE_SRC, backup=backup, interval_minutes=5, keep=10)
s.add_profile("Elden Ring", source=r"C:\Users\you\AppData\Roaming\EldenRing\76561198000000000",
              backup=r"D:\SaveBackups\Elden Ring")
s.add_profile("Cyberpunk 2077", source=r"C:\Users\you\Saved Games\CD Projekt Red\Cyberpunk 2077",
              backup=r"D:\SaveBackups\Cyberpunk 2077", interval_minutes=10)
s.add_profile("Stardew Valley", source=r"C:\Users\you\AppData\Roaming\StardewValley\Saves",
              backup=r"D:\SaveBackups\Stardew Valley")
s.add_profile("Hades II", source=r"C:\Users\you\Saved Games\Hades II",
              backup=r"D:\SaveBackups\Hades II", keep=25)
s.active_profile = witcher.id

win = SaveWatchWindow(s)
win.log_view.clear()
win.log("info", f"[The Witcher 3] watching {FAKE_SRC}")
win.log("info", rf"Backing up to {FAKE_DST} - every 5 min, keeping 10 versions (8 saves already known).")
win.log("ok", "new: 'QuickSave.png' -> 2026-11-22_14-30-05 (1 file, 302 KB)")
win.log("ok", "new: 'QuickSave.sav' -> 2026-11-22_14-30-05 (1 file, 4.4 MB)")
win.log("ok", "changed: 'ManualSave_12.png' -> 2026-11-22_14-35-05 (1 file, 302 KB)")
win.log("ok", "changed: 'ManualSave_12.sav' -> 2026-11-22_14-35-05 (1 file, 4.6 MB)")
win.log("prune", "removed old version ManualSave_12.sav/2026-11-12_20-10-33")
win.log("ok", "restored 'ManualSave_12.sav' to version 2026-11-20_19-02-11 (1 file, 4.5 MB) - previous save kept as undo point 2026-11-22_14-41-50")
win.log("ok", "also restored 'ManualSave_12.png' from the same moment")
win.log("ok", "checkpoint 'Before the final battle' saved: 8 saves, 8 files, 19.1 MB")
win.log("alert", "'AutoSave_1.sav' disappeared from the source - its backups are kept")
win.log("info", "16 saves checked - 2 backed up (4.9 MB).")

# Look like a running watch without starting one.
win.running = True
win.watched_id = witcher.id
win._update_profile_rows()
win.lock_hint.show()
win._show_running_buttons(True)
win._set_status("WATCHING  -  NEXT CHECK 04:12", "watching")
win.pulse.stop()
win._on_pulse(0.85)
win.dst_edit.setText(FAKE_DST)  # display only; the real demo folder is in %TEMP%

win.show()
for _ in range(5):
    app.processEvents()

docs = os.path.join(ROOT, "docs")
os.makedirs(docs, exist_ok=True)
win.grab().save(os.path.join(docs, "screenshot.png"))

win.tabs[1].click()
for _ in range(5):
    app.processEvents()
for i in range(win.slot_list.count()):
    if win.slot_list.item(i).data(0x0100) == MAIN_SLOT:
        win.slot_list.setCurrentRow(i)
win.version_list.setCurrentRow(2)
for _ in range(5):
    app.processEvents()
win.grab().save(os.path.join(docs, "backups.png"))

win.tabs[2].click()
for _ in range(5):
    app.processEvents()
win.cp_list.setCurrentRow(1)
for _ in range(5):
    app.processEvents()
win.grab().save(os.path.join(docs, "checkpoints.png"))

# The game picker, with made-up detection results (real ones would show this
# PC's own paths).
import savewatch.picker as picker_mod  # noqa: E402
from savewatch.catalog import Candidate  # noqa: E402


def fake_detect(game, folders=None):
    if "Witcher" not in game.name:
        return []
    return [Candidate("All versions", "", FAKE_SRC, True, 2757, NOW - timedelta(minutes=12))]


def fake_name_search(name, folders=None):
    return [Candidate("Found by name", "", r"C:\Users\you\AppData\Roaming\The Witcher 3", True, 1,
                      NOW - timedelta(days=330))]


picker_mod.detect = fake_detect
picker_mod.search_by_name = fake_name_search
_real_isdir = os.path.isdir
os.path.isdir = lambda p: str(p).startswith(r"C:\Users\you") or _real_isdir(p)  # demo paths
win.covers.enabled = False
picker = picker_mod.GamePicker(win, win.covers, game_id="the-witcher-3-wild-hunt")
picker._search_by_name()
picker.show()
for _ in range(5):
    app.processEvents()
for i in range(picker.cand_list.count()):
    if "gamesaves" in picker.candidates[picker.cand_list.item(i).data(0x0100)].path:
        picker.cand_list.setCurrentRow(i)
picker.game_list.scrollToItem(picker.game_list.currentItem())
for _ in range(5):
    app.processEvents()
picker.grab().save(os.path.join(docs, "picker.png"))
picker.close()

win.running = False
win.quit_app()
shutil.rmtree(tmp, ignore_errors=True)
print("wrote docs/screenshot.png, docs/backups.png, docs/checkpoints.png and docs/picker.png")
