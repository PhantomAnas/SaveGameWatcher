"""Render the main window off-screen and save docs/screenshot.png.

    python scripts/screenshot.py

Uses a throw-away settings file so the real user config is untouched.
"""

from __future__ import annotations

import os
import sys
import tempfile

# On Windows the real platform is used (the window flashes for a moment) because
# the offscreen backend has no access to the system fonts.  Elsewhere go offscreen.
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import savewatch.config as config  # noqa: E402

tmp = tempfile.mkdtemp(prefix="savewatch-shot-")
config.config_path = lambda: os.path.join(tmp, "settings.json")  # type: ignore[assignment]

from PySide6.QtWidgets import QApplication  # noqa: E402

from savewatch import autostart  # noqa: E402
from savewatch.app import SaveWatchWindow  # noqa: E402

autostart.is_enabled = lambda: False  # never touch the registry for a screenshot
autostart.set_enabled = lambda enabled: None

app = QApplication(sys.argv)
s = config.Settings(source=r"C:\Users\you\Saved Games\Baldur's Gate 3\PlayerProfiles\Public\Savegames\Story",
                    backup=r"D:\SaveBackups\Baldur's Gate 3", interval_minutes=5, keep=10)
win = SaveWatchWindow(s)
win.log("info", r"Watching C:\Users\you\Saved Games\Baldur's Gate 3\PlayerProfiles\Public\Savegames\Story")
win.log("info", r"Backing up to D:\SaveBackups\Baldur's Gate 3 - every 5 min, keeping 10 versions (14 saves already known).")
win.log("ok", "new: 'QuickSave-3' -> 2026-11-03_14-30-05 (12 files, 8.4 MB)")
win.log("ok", "changed: 'ManualSave-7' -> 2026-11-03_14-35-05 (12 files, 8.6 MB - only 2 rewritten (1.1 MB), 10 reused)")
win.log("prune", "removed old version ManualSave-7/2026-11-01_20-10-33")
win.log("alert", "'AutoSave-1' disappeared from the source - its backups are kept")
win.log("info", "15 saves checked - 2 backed up (17.0 MB) - 1.1 MB actually written, 10 files reused from the last version.")
win.status_lbl.setText("Watching - next check in 04:12")
win.show()
app.processEvents()
out = os.path.join(ROOT, "docs", "screenshot.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
win.grab().save(out)
win.quit_app()
print("wrote", out)
