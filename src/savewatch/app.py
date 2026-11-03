"""PySide6 window, system-tray icon and worker thread for SaveWatch."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import traceback
from datetime import datetime

from . import __version__, autostart
from .config import APP_NAME, Settings
from .core import (
    empty_stats,
    human_bytes,
    is_inside,
    load_state,
    run_backup_cycle,
    summarize_stats,
)
from .theme import FONT_FAMILY, LEVEL_COLOR, STYLESHEET


# --------------------------------------------------------------------------
# DPI awareness must be set BEFORE QApplication is constructed, otherwise
# Windows hands us a bitmap-stretched (blurry) window on scaled displays.
# --------------------------------------------------------------------------
def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    for attempt in (
        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            attempt()
            return
        except Exception:  # noqa: BLE001 - try the next, older API
            continue


enable_dpi_awareness()

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal  # noqa: E402
from PySide6.QtGui import QAction, QFont, QIcon, QTextCursor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

WIN_W, WIN_H = 1280, 760


def resource_path(name: str) -> str:
    """Path to a bundled asset, both from source and inside a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"))
    return os.path.join(base, name)


def app_icon() -> QIcon:
    for name in ("icon.ico", "icon.png"):
        path = resource_path(name)
        if os.path.exists(path):
            return QIcon(path)
    return QIcon()


# ==========================================================================
#  Worker thread
# ==========================================================================

class BackupWorker(QObject):
    message = Signal(str, str)      # level, text
    cycle_done = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self._cancel = threading.Event()
        self.source = ""
        self.backup = ""
        self.keep = 10
        self.state: dict = {"entries": {}}

    def cancel(self) -> None:
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    def run_cycle(self) -> None:
        try:
            stats = run_backup_cycle(
                self.source, self.backup, self.keep, self.state,
                lambda lvl, msg: self.message.emit(lvl, msg),
                self._cancel.is_set,
            )
        except Exception:  # noqa: BLE001
            self.message.emit("error", "unexpected failure:\n" + traceback.format_exc())
            stats = empty_stats()
            stats["errors"] = 1
        self.cycle_done.emit(stats)


# ==========================================================================
#  Window
# ==========================================================================

class SaveWatchWindow(QWidget):
    # Emitted on the GUI thread, delivered on the worker thread (queued
    # connection).  This is what guarantees copying never blocks the window.
    request_cycle = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - game save backup watcher")
        self.setFixedSize(WIN_W, WIN_H)
        self.setStyleSheet(STYLESHEET)
        self.setWindowIcon(app_icon())

        self.settings = settings
        self.running = False
        self.busy = False
        self.quitting = False
        self.seconds_left = 0

        self._build_ui()
        self._build_tray()

        # Worker lives on its own thread so a big copy never freezes the window.
        self.thread = QThread(self)
        self.worker = BackupWorker()
        self.worker.moveToThread(self.thread)
        self.worker.message.connect(self.log)
        self.worker.cycle_done.connect(self.on_cycle_done)
        self.request_cycle.connect(self.worker.run_cycle)
        self.thread.start()

        # Single-shot timer restarted after each cycle finishes, so a slow
        # copy can never overlap with the next one.
        self.cycle_timer = QTimer(self)
        self.cycle_timer.setSingleShot(True)
        self.cycle_timer.timeout.connect(self.trigger_cycle)

        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self.on_tick)

        self._apply_settings()
        self.log("info", "Ready. Pick the save folder, a backup folder, then press Start.")

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        sub = QLabel("Watches a save folder and keeps timestamped copies of anything that changes. "
                     "Deleted saves are never removed from the backup.")
        sub.setObjectName("Sub")
        sub.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(sub)

        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(20, 18, 20, 18)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        def field_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("Field")
            return lbl

        grid.addWidget(field_label("Source folder to watch"), 0, 0)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("The game's save folder, e.g. ...\\Saved Games\\<game>")
        self.src_btn = QPushButton("Browse...")
        self.src_btn.clicked.connect(lambda: self._browse(self.src_edit, "Select the folder to watch"))
        grid.addWidget(self.src_edit, 1, 0)
        grid.addWidget(self.src_btn, 1, 1)

        grid.addWidget(field_label("Backup folder (destination)"), 2, 0)
        self.dst_edit = QLineEdit()
        self.dst_edit.setPlaceholderText("Anywhere outside the source folder, ideally another drive")
        self.dst_btn = QPushButton("Browse...")
        self.dst_btn.clicked.connect(lambda: self._browse(self.dst_edit, "Select the backup folder"))
        grid.addWidget(self.dst_edit, 3, 0)
        grid.addWidget(self.dst_btn, 3, 1)

        row = QHBoxLayout()
        row.setSpacing(24)

        ival_box = QVBoxLayout()
        ival_box.setSpacing(6)
        ival_box.addWidget(field_label("Check every (minutes)"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 1440.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setSingleStep(1.0)
        self.interval_spin.setValue(5.0)
        self.interval_spin.setFixedWidth(160)
        ival_box.addWidget(self.interval_spin)
        row.addLayout(ival_box)

        keep_box = QVBoxLayout()
        keep_box.setSpacing(6)
        keep_box.addWidget(field_label("Versions to keep per save (0 = unlimited)"))
        self.keep_spin = QSpinBox()
        self.keep_spin.setRange(0, 999)
        self.keep_spin.setValue(10)
        self.keep_spin.setFixedWidth(160)
        keep_box.addWidget(self.keep_spin)
        row.addLayout(keep_box)
        row.addStretch(1)
        grid.addLayout(row, 4, 0, 1, 2)

        self.startup_chk = QCheckBox("Take a full backup immediately when Start is pressed")
        self.startup_chk.setChecked(True)
        grid.addWidget(self.startup_chk, 5, 0, 1, 2)

        opts = QHBoxLayout()
        opts.setSpacing(24)
        self.tray_chk = QCheckBox("Keep running in the tray when the window is closed")
        self.autowatch_chk = QCheckBox("Start watching automatically on launch")
        self.autostart_chk = QCheckBox("Start with Windows (minimized)")
        self.autostart_chk.setEnabled(autostart.supported())
        if not autostart.supported():
            self.autostart_chk.setToolTip("Only available on Windows")
        self.autostart_chk.toggled.connect(self._on_autostart_toggled)
        for chk in (self.tray_chk, self.autowatch_chk, self.autostart_chk):
            opts.addWidget(chk)
        opts.addStretch(1)
        grid.addLayout(opts, 6, 0, 1, 2)

        grid.setColumnStretch(0, 1)
        outer.addWidget(card)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.start_btn = QPushButton("Start watching")
        self.start_btn.setObjectName("Start")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self.start_watching)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("Stop")
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_watching)
        self.now_btn = QPushButton("Back up now")
        self.now_btn.setMinimumHeight(40)
        self.now_btn.clicked.connect(self.backup_now)
        self.open_btn = QPushButton("Open backup folder")
        self.open_btn.setMinimumHeight(40)
        self.open_btn.clicked.connect(self.open_backup_folder)

        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.now_btn)
        controls.addWidget(self.open_btn)
        controls.addStretch(1)
        self.status_lbl = QLabel("Idle")
        self.status_lbl.setObjectName("Sub")
        self.status_lbl.setMinimumWidth(230)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls.addWidget(self.status_lbl)
        outer.addLayout(controls)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        outer.addWidget(self.log_view, 1)

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        self.tray_show = QAction("Show window", menu)
        self.tray_show.triggered.connect(self.show_window)
        self.tray_toggle = QAction("Start watching", menu)
        self.tray_toggle.triggered.connect(self._tray_toggle_watch)
        self.tray_now = QAction("Back up now", menu)
        self.tray_now.triggered.connect(self.backup_now)
        self.tray_open = QAction("Open backup folder", menu)
        self.tray_open.triggered.connect(self.open_backup_folder)
        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(self.quit_app)
        for act in (self.tray_show, self.tray_toggle, self.tray_now, self.tray_open):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._set_tray_status("Idle")
        self.tray.show()

    def _set_tray_status(self, text: str) -> None:
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME} - {text}")

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _tray_toggle_watch(self) -> None:
        if self.running:
            self.stop_watching()
        else:
            self.start_watching(from_tray=True)

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _browse(self, edit: QLineEdit, caption: str) -> None:
        start = edit.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, caption, start)
        if path:
            edit.setText(os.path.normpath(path))

    # ------------------------------------------------------------ logging
    def log(self, level: str, text: str) -> None:
        color = LEVEL_COLOR.get(level, "#e6e8ee")
        stamp = datetime.now().strftime("%H:%M:%S")
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace("\n", "<br>&nbsp;&nbsp;&nbsp;&nbsp;"))
        self.log_view.appendHtml(
            f'<span style="color:#5b6478">{stamp}</span> '
            f'<span style="color:{color}">{safe}</span>'
        )
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        if self.tray and level in ("error", "alert") and self.isHidden():
            self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Warning, 6000)

    # ----------------------------------------------------------- settings
    def _apply_settings(self) -> None:
        s = self.settings
        self.src_edit.setText(s.source)
        self.dst_edit.setText(s.backup)
        self.interval_spin.setValue(s.interval_minutes)
        self.keep_spin.setValue(s.keep)
        self.startup_chk.setChecked(s.full_backup_on_start)
        self.tray_chk.setChecked(s.minimize_to_tray and self.tray is not None)
        self.autowatch_chk.setChecked(s.auto_start_watching)
        self.autostart_chk.blockSignals(True)
        self.autostart_chk.setChecked(autostart.is_enabled())
        self.autostart_chk.blockSignals(False)

    def _collect_settings(self) -> None:
        s = self.settings
        s.source = self.src_edit.text().strip()
        s.backup = self.dst_edit.text().strip()
        s.interval_minutes = self.interval_spin.value()
        s.keep = self.keep_spin.value()
        s.full_backup_on_start = self.startup_chk.isChecked()
        s.minimize_to_tray = self.tray_chk.isChecked()
        s.auto_start_watching = self.autowatch_chk.isChecked()

    def _save_settings(self) -> None:
        self._collect_settings()
        try:
            self.settings.save()
        except OSError as exc:
            self.log("warn", f"could not save settings: {exc}")

    def _on_autostart_toggled(self, checked: bool) -> None:
        try:
            autostart.set_enabled(checked)
            self.log("info", "Will start with Windows." if checked else "Removed from Windows start-up.")
        except OSError as exc:
            self.log("warn", f"could not update start-up entry: {exc}")
            self.autostart_chk.blockSignals(True)
            self.autostart_chk.setChecked(not checked)
            self.autostart_chk.blockSignals(False)

    # ------------------------------------------------------------ control
    def _warn(self, text: str, from_tray: bool = False) -> None:
        if from_tray and self.tray:
            self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Warning, 6000)
        else:
            QMessageBox.warning(self, APP_NAME, text)

    def _validate(self, from_tray: bool = False) -> tuple[str, str] | None:
        src_text = self.src_edit.text().strip()
        dst_text = self.dst_edit.text().strip()
        src = os.path.normpath(src_text) if src_text else ""
        dst = os.path.normpath(dst_text) if dst_text else ""
        if not src or not os.path.isdir(src):
            self._warn("The source folder does not exist.", from_tray)
            return None
        if not dst:
            self._warn("Pick a backup folder.", from_tray)
            return None
        # Backing up into the folder you are watching would copy the copies.
        if is_inside(dst, src):
            self._warn("The backup folder must not be inside the folder being watched.", from_tray)
            return None
        try:
            os.makedirs(dst, exist_ok=True)
        except OSError as exc:
            self._warn(f"Cannot create the backup folder:\n{exc}", from_tray)
            return None
        return src, dst

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for w in (self.src_edit, self.dst_edit, self.src_btn, self.dst_btn,
                  self.interval_spin, self.keep_spin, self.startup_chk):
            w.setEnabled(enabled)

    def _configure_worker(self, src: str, dst: str) -> None:
        self.worker.source = src
        self.worker.backup = dst
        self.worker.keep = self.keep_spin.value()
        self.worker.state = load_state(dst)
        self.worker.reset_cancel()

    def start_watching(self, from_tray: bool = False) -> None:
        if self.running:
            return
        paths = self._validate(from_tray)
        if not paths:
            return
        src, dst = paths
        self._save_settings()
        self._configure_worker(src, dst)

        if self.startup_chk.isChecked():
            self.worker.state = {"entries": {}}
            self.log("info", "Start-up full backup requested - every save will be copied once.")

        known = len(self.worker.state.get("entries", {}))
        self.running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_inputs_enabled(False)
        if self.tray:
            self.tray_toggle.setText("Stop watching")
        self.log("info", f"Watching {src}")
        self.log("info", f"Backing up to {dst} - every {self.interval_spin.value():g} min, "
                         f"keeping {self.keep_spin.value() or 'all'} versions "
                         f"({known} saves already known).")
        self.trigger_cycle()

    def stop_watching(self) -> None:
        self.running = False
        self.worker.cancel()
        self.cycle_timer.stop()
        self.tick_timer.stop()
        self.stop_btn.setEnabled(False)
        if self.tray:
            self.tray_toggle.setText("Start watching")
        if not self.busy:
            self._back_to_idle()
            self.log("info", "Stopped.")
        else:
            self._set_status("Finishing current copy...")
            self.log("info", "Stopping after the current copy finishes...")

    def _set_status(self, text: str) -> None:
        self.status_lbl.setText(text)
        self._set_tray_status(text)

    def _back_to_idle(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.now_btn.setEnabled(True)
        self._set_inputs_enabled(True)
        self._set_status("Idle")

    def backup_now(self) -> None:
        if self.busy:
            return
        if not self.running:
            paths = self._validate(from_tray=self.isHidden())
            if not paths:
                return
            src, dst = paths
            self._save_settings()
            self._configure_worker(src, dst)
        self.cycle_timer.stop()
        self.trigger_cycle()

    def trigger_cycle(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.now_btn.setEnabled(False)
        if self.tray:
            self.tray_now.setEnabled(False)
        self.tick_timer.stop()
        self._set_status("Scanning...")
        # Queued across threads - returns immediately, GUI stays responsive.
        self.request_cycle.emit()

    def on_cycle_done(self, stats: dict) -> None:
        self.busy = False
        self.now_btn.setEnabled(True)
        if self.tray:
            self.tray_now.setEnabled(True)
        self.log("info" if not stats["errors"] else "warn", summarize_stats(stats))

        if self.quitting:
            self.quit_app()
            return
        if self.running:
            ms = max(1000, int(self.interval_spin.value() * 60_000))
            self.seconds_left = ms // 1000
            self.cycle_timer.start(ms)
            self.tick_timer.start()
            self.on_tick()
        else:
            self._back_to_idle()
            self.log("info", "Stopped.")

    def on_tick(self) -> None:
        if not self.running or self.busy:
            return
        self.seconds_left = max(0, self.seconds_left - 1)
        m, s = divmod(self.seconds_left, 60)
        self._set_status(f"Watching - next check in {m:02d}:{s:02d}")

    def open_backup_folder(self) -> None:
        path = os.path.normpath(self.dst_edit.text().strip() or ".")
        if self.dst_edit.text().strip() and os.path.isdir(path):
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self._warn("That backup folder does not exist yet.", from_tray=self.isHidden())

    # ------------------------------------------------------------- close
    def quit_app(self) -> None:
        """Stop the worker and leave.  Waits for an in-flight copy to finish."""
        self.quitting = True
        self._save_settings()
        self.running = False
        self.cycle_timer.stop()
        self.tick_timer.stop()
        if self.busy:
            self.worker.cancel()
            self._set_status("Finishing current copy, then quitting...")
            return
        if self.tray:
            self.tray.hide()
        self.thread.quit()
        self.thread.wait(5000)
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.tray_chk.isChecked() and self.tray and not self.quitting:
            event.ignore()
            self.hide()
            self._save_settings()
            if self.running:
                self.tray.showMessage(APP_NAME, "Still watching in the background. "
                                      "Right-click the tray icon to quit.",
                                      QSystemTrayIcon.MessageIcon.Information, 4000)
            return
        event.accept()
        self.quit_app()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    start_minimized = "--minimized" in argv

    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont(FONT_FAMILY, 10))
    app.setWindowIcon(app_icon())

    settings = Settings.load()
    win = SaveWatchWindow(settings)
    if start_minimized and win.tray and settings.minimize_to_tray:
        win.hide()
    else:
        win.show()
    if settings.auto_start_watching and settings.source and settings.backup:
        QTimer.singleShot(300, lambda: win.start_watching(from_tray=win.isHidden()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
