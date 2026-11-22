"""Render the window with demo data and save the README screenshots.

    python scripts/screenshot.py

Writes docs/screenshot.png (Watch tab), docs/backups.png (Backups & Restore
tab) and docs/checkpoints.png (Checkpoints tab).  Uses a throw-away settings file and a temp folder full of fake backups,
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
FAKE_SRC = r"C:\Users\you\AppData\Local\Larian Studios\Baldur's Gate 3\PlayerProfiles\Public\Savegames\Story"
FAKE_DST = r"D:\SaveBackups\Baldur's Gate 3"


def make_version(backup: str, slot: str, when: datetime, size: int) -> None:
    folder = os.path.join(backup, slot, when.strftime(core.STAMP_FORMAT))
    os.makedirs(folder, exist_ok=True)
    for name, share in (("SaveGame.lsv", 0.86), ("SaveInfo.json", 0.01), ("Thumbnail.webp", 0.13)):
        with open(os.path.join(folder, name), "wb") as fh:
            fh.truncate(int(size * share))  # sparse: right size, no real bytes


def build_demo_backup() -> str:
    backup = os.path.join(tmp, "backup")
    plan = {
        "AutoSave-14": [(0, 2, 10), (0, 1, 30), (0, 0, 45), (0, 0, 9)],
        "Honour-Mode-Act2": [(3, 4, 0), (1, 2, 30), (0, 6, 15)],
        "ManualSave-7": [(9, 0, 0), (6, 3, 0), (4, 1, 20), (2, 5, 0), (1, 0, 40),
                         (0, 5, 10), (0, 3, 2), (0, 1, 12)],
        "QuickSave-3": [(0, 4, 0), (0, 0, 22)],
    }
    for slot, ages in plan.items():
        for i, (d, h, m) in enumerate(ages):
            make_version(backup, slot, NOW - timedelta(days=d, hours=h, minutes=m),
                         7_800_000 + 310_000 * i)
    checkpoints = []
    for title, (d, h, m) in (("Act 1 done - whole party alive", (6, 3, 0)),
                             ("Before the big choice in Act 2", (2, 5, 0)),
                             ("Before the final boss", (0, 5, 10))):
        when = NOW - timedelta(days=d, hours=h, minutes=m)
        stamp = when.strftime(core.STAMP_FORMAT)
        for slot in plan:
            make_version(backup, slot, when, 8_200_000)
        checkpoints.append({"id": stamp, "title": title, "created": when.isoformat(),
                            "entries": [{"name": s, "version": stamp} for s in plan],
                            "files": 12, "bytes": 4 * 8_200_000})
    with open(os.path.join(backup, core.CHECKPOINTS_FILENAME), "w") as fh:
        json.dump({"checkpoints": checkpoints}, fh)
    undo_dir = os.path.join(backup, core.UNDO_DIRNAME, "ManualSave-7")
    for minutes, reason in ((26, "before restoring 2026-11-20_19-02-11"),
                            (190, "before restoring 2026-11-18_21-44-37")):
        stamp = (NOW - timedelta(minutes=minutes)).strftime(core.STAMP_FORMAT)
        os.makedirs(os.path.join(undo_dir, stamp), exist_ok=True)
        with open(os.path.join(undo_dir, stamp + ".json"), "w") as fh:
            json.dump({"entry": "ManualSave-7", "reason": reason, "absent": False,
                       "kind": "dir", "files": 3, "bytes": 9_430_000}, fh)
    return backup


app = setup_app(sys.argv[0])
backup = build_demo_backup()

s = config.Settings()
bg3 = s.add_profile("Baldur's Gate 3", source=FAKE_SRC, backup=backup, interval_minutes=5, keep=10)
s.add_profile("Elden Ring", source=r"C:\Users\you\AppData\Roaming\EldenRing\76561198000000000",
              backup=r"D:\SaveBackups\Elden Ring")
s.add_profile("Cyberpunk 2077", source=r"C:\Users\you\Saved Games\CD Projekt Red\Cyberpunk 2077",
              backup=r"D:\SaveBackups\Cyberpunk 2077", interval_minutes=10)
s.add_profile("Stardew Valley", source=r"C:\Users\you\AppData\Roaming\StardewValley\Saves",
              backup=r"D:\SaveBackups\Stardew Valley")
s.add_profile("Hades II", source=r"C:\Users\you\Saved Games\Hades II",
              backup=r"D:\SaveBackups\Hades II", keep=25)
s.active_profile = bg3.id

win = SaveWatchWindow(s)
win.log_view.clear()
win.log("info", f"[Baldur's Gate 3] watching {FAKE_SRC}")
win.log("info", rf"Backing up to {FAKE_DST} - every 5 min, keeping 10 versions (14 saves already known).")
win.log("ok", "new: 'QuickSave-3' -> 2026-11-22_14-30-05 (12 files, 8.4 MB)")
win.log("ok", "changed: 'ManualSave-7' -> 2026-11-22_14-35-05 (12 files, 8.6 MB - only 2 rewritten (1.1 MB), 10 reused)")
win.log("prune", "removed old version ManualSave-7/2026-11-12_20-10-33")
win.log("ok", "restored 'ManualSave-7' to version 2026-11-20_19-02-11 (3 files, 9.4 MB) - previous save kept as undo point 2026-11-22_14-41-50")
win.log("ok", "checkpoint 'Before the final boss' saved: 4 saves, 12 files, 31.3 MB")
win.log("alert", "'AutoSave-1' disappeared from the source - its backups are kept")
win.log("info", "15 saves checked - 2 backed up (17.0 MB) - 1.1 MB actually written, 10 files reused from the last version.")

# Look like a running watch without starting one.
win.running = True
win.watched_id = bg3.id
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
    if win.slot_list.item(i).data(0x0100) == "ManualSave-7":
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

win.running = False
win.quit_app()
shutil.rmtree(tmp, ignore_errors=True)
print("wrote docs/screenshot.png, docs/backups.png and docs/checkpoints.png")
