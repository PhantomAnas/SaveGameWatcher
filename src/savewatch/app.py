"""PySide6 window, system-tray icon and worker thread for SaveWatch."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import traceback
from datetime import datetime

from . import __version__, autostart
from .config import (
    APP_NAME,
    DEFAULT_PROFILE_NAME,
    GameProfile,
    Settings,
    config_path,
    guess_game_name,
)
from .core import (
    create_checkpoint,
    delete_checkpoint,
    describe_copy_error,
    empty_stats,
    folder_stats,
    human_bytes,
    humanize_age,
    is_inside,
    list_backup_entries,
    list_undo_points,
    list_versions,
    load_checkpoints,
    load_state,
    parse_stamp,
    pinned_versions,
    plural,
    related_saves,
    rename_checkpoint,
    restore_checkpoint,
    restore_point_in_time,
    restore_version,
    run_backup_cycle,
    summarize_stats,
    undo_restore,
)
from .theme import (
    AVATAR_GRADIENTS,
    FONT_FAMILY,
    HEAD_FAMILIES,
    LEVEL_COLOR,
    PALETTE,
    STAMP_COLOR,
    STYLESHEET,
)


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

from PySide6.QtCore import (  # noqa: E402
    QEasingCurve,
    QObject,
    QPointF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (  # noqa: E402
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QRadialGradient,
    QTextCursor,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyleFactory,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

WIN_W, WIN_H = 1320, 830
ROLE = Qt.ItemDataRole.UserRole


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
#  Look helpers
# ==========================================================================

def head_font(px: int, weight: QFont.Weight = QFont.Weight.Bold, spacing: float = 0) -> QFont:
    """Display font for titles: Bahnschrift (ships with Windows 10+) or fallback."""
    f = QFont()
    f.setFamilies(HEAD_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def glow(widget: QWidget, color: str, radius: float = 26, alpha: int = 170) -> QGraphicsDropShadowEffect:
    """Neon glow: a coloured, un-offset drop shadow.  Leaf widgets only."""
    effect = QGraphicsDropShadowEffect(widget)
    c = QColor(color)
    c.setAlpha(alpha)
    effect.setColor(c)
    effect.setBlurRadius(radius)
    effect.setOffset(0, 0)
    widget.setGraphicsEffect(effect)
    return effect


def label(text: str = "", name: str | None = None, font: QFont | None = None,
          wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    if font is not None:
        lbl.setFont(font)
    lbl.setWordWrap(wrap)
    return lbl


def section(text: str) -> QLabel:
    return label(text.upper(), "Section", head_font(12, QFont.Weight.DemiBold, 1.6))


def card(name: str = "Card") -> QFrame:
    frame = QFrame()
    frame.setObjectName(name)
    return frame


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    return line


def stepper(spin: QSpinBox | QDoubleSpinBox, width: int = 110) -> QWidget:
    """[-] value [+] : a spin box with big touch-friendly buttons."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    # The built-in arrows are collapsed to zero width by the stylesheet.
    # (ButtonSymbols.NoButtons + any stylesheet padding squashes the text
    # field to 1px in Qt 6, so it is deliberately not used.)
    spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
    spin.setFixedWidth(width)
    for text, step in (("−", spin.stepDown), ("+", spin.stepUp)):
        b = QPushButton(text)
        b.setObjectName("Step")
        b.setFixedSize(38, 38)
        b.setAutoRepeat(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(step)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if text == "−":
            lay.addWidget(b)
            lay.addWidget(spin)
        else:
            lay.addWidget(b)
    lay.addStretch(1)
    return box


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def style_title_bar(widget: QWidget) -> None:
    """Dark caption + neon border on Windows 11 (silently ignored elsewhere)."""
    if sys.platform != "win32":
        return

    def colorref(hex_color: str) -> int:
        c = QColor(hex_color)
        return c.red() | (c.green() << 8) | (c.blue() << 16)

    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        for attr, value in ((20, 1),                                  # immersive dark mode
                            (35, colorref(PALETTE["bg_top"])),        # caption colour
                            (34, colorref(PALETTE["cyan"])),          # border colour
                            (36, colorref(PALETTE["text"]))):         # caption text
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def fmt_when(dt: datetime | None) -> str:
    return dt.strftime("%d %b %Y  %H:%M:%S") if dt else "?"


class ElidedLabel(QLabel):
    """Single-line label that shortens long paths with '...' instead of growing."""

    def __init__(self, text: str = "", name: str | None = None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle) -> None:
        super().__init__()
        if name:
            self.setObjectName(name)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text if len(text) > 40 else "")
        self._refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = max(20, self.width())
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, width))


class Backdrop(QWidget):
    """Window background: deep gradient, three soft neon lights and a faint grid."""

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0, QColor(PALETTE["bg_top"]))
        base.setColorAt(1, QColor(PALETTE["bg_bottom"]))
        p.fillRect(self.rect(), base)

        def light(x: float, y: float, r: float, color: str, alpha: int) -> None:
            g = QRadialGradient(QPointF(x, y), r)
            c = QColor(color)
            c.setAlpha(alpha)
            g.setColorAt(0, c)
            c.setAlpha(0)
            g.setColorAt(1, c)
            p.fillRect(self.rect(), g)

        light(w * 0.06, h * 0.02, w * 0.45, PALETTE["cyan"], 62)
        light(w * 0.98, h * 1.02, w * 0.50, PALETTE["magenta"], 58)
        light(w * 0.62, h * 0.30, w * 0.32, PALETTE["violet"], 34)

        pen = QPen(QColor(130, 150, 255, 9))
        pen.setWidth(1)
        p.setPen(pen)
        step = 34
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)
        p.end()


class Avatar(QLabel):
    """Rounded neon tile with a game's initials."""

    def __init__(self, size: int = 38) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(head_font(int(size * 0.42), QFont.Weight.Bold))

    def set_name(self, name: str) -> None:
        words = [w for w in name.replace("-", " ").split() if w[:1].isalnum()]
        initials = "".join(w[0] for w in words[:2]).upper() or "?"
        self.setText(initials)
        a, b = AVATAR_GRADIENTS[sum(map(ord, name)) % len(AVATAR_GRADIENTS)]
        self.setStyleSheet(
            "QLabel { color: #05060c; border-radius: 11px; "
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a}, stop:1 {b}); }}")


class RowWidget(QWidget):
    """Two-line list row: title + subtitle, optional leading widget and badge."""

    def __init__(self, title: str, subtitle: str = "", lead: QWidget | None = None,
                 title_font: QFont | None = None,
                 sub_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        self.lead = lead
        if lead is not None:
            lay.addWidget(lead)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = ElidedLabel(title, mode=Qt.TextElideMode.ElideRight)
        self.title.setFont(title_font or head_font(15, QFont.Weight.DemiBold))
        self.sub = ElidedLabel(subtitle, "Muted", sub_mode)
        self.sub.setFont(QFont(FONT_FAMILY, 9))
        text.addWidget(self.title)
        text.addWidget(self.sub)
        lay.addLayout(text, 1)
        self.badge = label("", "Badge")
        self.badge.hide()
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_badge(self, text: str, name: str = "Badge") -> None:
        self.badge.setText(text)
        self.badge.setObjectName(name)
        repolish(self.badge)
        self.badge.setVisible(bool(text))


# ==========================================================================
#  Worker thread
# ==========================================================================

class BackupWorker(QObject):
    message = Signal(str, str)      # level, text
    cycle_done = Signal(dict)
    job_done = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self._cancel = threading.Event()
        self.source = ""
        self.backup = ""
        self.keep = 10
        self.undo_days = 7
        self.state: dict = {"entries": {}}

    def cancel(self) -> None:
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    def _emit(self, level: str, text: str) -> None:
        self.message.emit(level, text)

    def run_cycle(self) -> None:
        try:
            stats = run_backup_cycle(self.source, self.backup, self.keep, self.state,
                                     self._emit, self._cancel.is_set, undo_days=self.undo_days)
        except Exception:  # noqa: BLE001
            self.message.emit("error", "unexpected failure:\n" + traceback.format_exc())
            stats = empty_stats()
            stats["errors"] = 1
        self.cycle_done.emit(stats)

    def run_job(self, job: dict) -> None:
        """Restore or undo, on this thread so it never overlaps a backup cycle."""
        result = dict(job)
        actions = {
            "restore": lambda: restore_version(job["source"], job["backup"], job["name"],
                                               job["ref"], self._emit),
            "undo": lambda: undo_restore(job["source"], job["backup"], job["name"],
                                         job["ref"], self._emit),
            "checkpoint": lambda: create_checkpoint(job["source"], job["backup"], job["ref"],
                                                    self._emit),
            "restore_checkpoint": lambda: restore_checkpoint(job["source"], job["backup"],
                                                             job["ref"], self._emit),
            "restore_all": lambda: restore_point_in_time(job["source"], job["backup"],
                                                         job["ref"], self._emit),
        }
        try:
            result.update(actions[job["kind"]]())
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["error"] = describe_copy_error(exc)
        # The restore rewrote the state file; keep the watcher's copy in sync.
        if self.backup and os.path.normcase(os.path.abspath(self.backup)) == \
                os.path.normcase(os.path.abspath(job["backup"])):
            self.state = load_state(self.backup)
        self.job_done.emit(result)


# ==========================================================================
#  Window
# ==========================================================================

class SaveWatchWindow(Backdrop):
    # Emitted on the GUI thread, delivered on the worker thread (queued
    # connection).  This is what guarantees copying never blocks the window.
    request_cycle = Signal()
    request_job = Signal(dict)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - game save backup watcher")
        self.resize(WIN_W, WIN_H)
        self.setMinimumSize(1180, 760)
        self.setWindowIcon(app_icon())

        self.settings = settings
        self.settings.ensure_profile()
        self.running = False
        self.busy = False
        self.quitting = False
        self.seconds_left = 0
        self.watched_id = ""
        self._loading = False
        self._stats_cache: dict[str, tuple[int, int]] = {}

        self._build_ui()
        self._build_tray()

        # Worker lives on its own thread so a big copy never freezes the window.
        self.thread = QThread(self)
        self.worker = BackupWorker()
        self.worker.moveToThread(self.thread)
        self.worker.message.connect(self.log)
        self.worker.cycle_done.connect(self.on_cycle_done)
        self.worker.job_done.connect(self.on_job_done)
        self.request_cycle.connect(self.worker.run_cycle)
        self.request_job.connect(self.worker.run_job)
        self.thread.start()

        # Single-shot timer restarted after each cycle finishes, so a slow
        # copy can never overlap with the next one.
        self.cycle_timer = QTimer(self)
        self.cycle_timer.setSingleShot(True)
        self.cycle_timer.timeout.connect(self.trigger_cycle)

        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self.on_tick)

        # "3 minutes ago" labels age; refresh them once a minute.
        self.age_timer = QTimer(self)
        self.age_timer.setInterval(60_000)
        self.age_timer.timeout.connect(self._refresh_ages)
        self.age_timer.start()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_settings)

        self._apply_settings()
        self._set_status("IDLE", "idle")
        self.log("info", "Ready. Pick a game on the left (or add one), set its save and backup "
                         "folders, then press Start watching.")

    # ------------------------------------------------------------ helpers
    @property
    def profile(self) -> GameProfile:
        p = self.settings.active()
        assert p is not None
        return p

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(18)
        root.addWidget(self._build_sidebar())

        main = QVBoxLayout()
        main.setSpacing(14)
        main.addLayout(self._build_header())
        main.addLayout(self._build_actions())
        main.addLayout(self._build_tabs())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_watch_page())
        self.pages.addWidget(self._build_backups_page())
        self.pages.addWidget(self._build_checkpoints_page())
        self.pages.addWidget(self._build_settings_page())
        main.addWidget(self.pages, 1)
        root.addLayout(main, 1)

    def _build_sidebar(self) -> QFrame:
        side = card("Sidebar")
        side.setFixedWidth(282)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 18, 16, 16)
        lay.setSpacing(10)

        brand = QHBoxLayout()
        brand.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(40, 40))
        glow(icon, PALETTE["cyan"], 24, 150)
        brand.addWidget(icon)
        names = QVBoxLayout()
        names.setSpacing(0)
        logo = label("SAVEWATCH", font=head_font(21, QFont.Weight.Bold, 3.0))
        logo.setStyleSheet(f"color: {PALETTE['cyan']};")
        glow(logo, PALETTE["cyan"], 22, 190)
        names.addWidget(logo)
        names.addWidget(label("game save backup watcher", "Muted", QFont(FONT_FAMILY, 9)))
        brand.addLayout(names, 1)
        lay.addLayout(brand)
        lay.addSpacing(10)

        head = QHBoxLayout()
        head.addWidget(section("Game profiles"))
        head.addStretch(1)
        self.profile_count = label("", "Dim", QFont(FONT_FAMILY, 9))
        head.addWidget(self.profile_count)
        lay.addLayout(head)

        self.profile_list = QListWidget()
        self.profile_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.profile_list.currentItemChanged.connect(self._on_profile_selected)
        lay.addWidget(self.profile_list, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.add_btn = QPushButton("+  New game")
        self.add_btn.setObjectName("Primary")
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self.add_profile)
        self.del_btn = QPushButton("Delete")
        self.del_btn.setObjectName("Danger")
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.clicked.connect(self.delete_profile)
        btns.addWidget(self.add_btn, 1)
        btns.addWidget(self.del_btn)
        lay.addLayout(btns)
        self.lock_hint = label("Stop watching to switch games.", "Hint", wrap=True)
        self.lock_hint.hide()
        lay.addWidget(self.lock_hint)
        lay.addWidget(label(f"v{__version__}  -  nothing leaves your PC", "Dim", QFont(FONT_FAMILY, 8)))
        return side

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.game_title = ElidedLabel("", mode=Qt.TextElideMode.ElideRight)
        self.game_title.setFont(head_font(32, QFont.Weight.Bold, 0.5))
        self.game_title.setStyleSheet("color: #ffffff;")
        self.game_title.setMinimumHeight(42)
        glow(self.game_title, PALETTE["cyan"], 30, 150)
        self.game_path = ElidedLabel("", "Muted")
        self.game_path.setFont(QFont(FONT_FAMILY, 10))
        titles.addWidget(self.game_title)
        titles.addWidget(self.game_path)
        row.addLayout(titles, 1)

        self.pill = label("IDLE", "Pill")
        self.pill.setFont(head_font(13, QFont.Weight.Bold, 1.2))
        self.pill.setProperty("state", "idle")
        self.pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pill.setMinimumWidth(250)
        self.pill_glow = glow(self.pill, PALETTE["lime"], 0, 0)
        row.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)

        self.pulse = QVariantAnimation(self)
        self.pulse.setStartValue(0.0)
        self.pulse.setKeyValueAt(0.5, 1.0)
        self.pulse.setEndValue(0.0)
        self.pulse.setDuration(2200)
        self.pulse.setLoopCount(-1)
        self.pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.pulse.valueChanged.connect(self._on_pulse)
        return row

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.chips = [label("", "Chip") for _ in range(3)]
        for chip in self.chips:
            row.addWidget(chip)
        row.addStretch(1)

        def button(text: str, name: str | None, slot) -> QPushButton:
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            b.setMinimumHeight(42)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        self.start_btn = button("▶  Start watching", "Primary", self.start_watching)
        self.start_glow = glow(self.start_btn, PALETTE["cyan"], 26, 140)
        self.stop_btn = button("■  Stop watching", "Danger", self.stop_watching)
        self.stop_btn.setEnabled(False)
        self.stop_btn.hide()  # Start and Stop share one spot
        self.checkpoint_btn = button("★  Save checkpoint", "Restore", self.save_checkpoint)
        self.checkpoint_btn.setToolTip("Back up every save right now under a name you choose, "
                                       "e.g. 'Before the big choice'. Never pruned.")
        self.now_btn = button("Back up now", None, self.backup_now)
        for b in (self.start_btn, self.stop_btn, self.checkpoint_btn, self.now_btn):
            row.addWidget(b)
        return row

    def _build_tabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(0)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tabs: list[QPushButton] = []
        for i, text in enumerate(("WATCH", "BACKUPS && RESTORE", "CHECKPOINTS", "SETTINGS")):
            b = QPushButton(text)
            b.setObjectName("Tab")
            b.setFont(head_font(14, QFont.Weight.Bold, 1.5))
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.tab_group.addButton(b, i)
            self.tabs.append(b)
            row.addWidget(b)
        self.tabs[0].setChecked(True)
        self.tab_group.idClicked.connect(self._on_tab)
        row.addStretch(1)
        return row

    def _build_watch_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        prof = card()
        grid = QGridLayout(prof)
        grid.setContentsMargins(20, 16, 20, 18)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)

        grid.addWidget(section("Game name"), 0, 0)
        grid.addWidget(section("Check every (minutes)"), 0, 1)
        grid.addWidget(section("Versions to keep  (0 = all)"), 0, 2)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. The Witcher 3")
        self.name_edit.textEdited.connect(self._on_name_edited)
        grid.addWidget(self.name_edit, 1, 0)
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 1440.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setSingleStep(1.0)
        self.interval_spin.valueChanged.connect(self._on_profile_field)
        grid.addWidget(stepper(self.interval_spin), 1, 1)
        self.keep_spin = QSpinBox()
        self.keep_spin.setRange(0, 999)
        self.keep_spin.valueChanged.connect(self._on_profile_field)
        grid.addWidget(stepper(self.keep_spin), 1, 2)

        grid.addWidget(section("Save folder to watch"), 2, 0, 1, 3)
        src_row = QHBoxLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText(r"The game's save folder, e.g. ...\Saved Games\<game>")
        self.src_edit.textEdited.connect(self._on_profile_field)
        self.src_btn = QPushButton("Browse...")
        self.src_btn.clicked.connect(self._browse_source)
        src_row.addWidget(self.src_edit, 1)
        src_row.addWidget(self.src_btn)
        grid.addLayout(src_row, 3, 0, 1, 3)

        grid.addWidget(section("Backup folder (destination)"), 4, 0, 1, 3)
        dst_row = QHBoxLayout()
        self.dst_edit = QLineEdit()
        self.dst_edit.setPlaceholderText("Anywhere outside the save folder, ideally another drive")
        self.dst_edit.textEdited.connect(self._on_profile_field)
        self.dst_btn = QPushButton("Browse...")
        self.dst_btn.clicked.connect(self._browse_backup)
        self.open_btn = QPushButton("Open")
        self.open_btn.setToolTip("Open the backup folder")
        self.open_btn.clicked.connect(self.open_backup_folder)
        dst_row.addWidget(self.dst_edit, 1)
        dst_row.addWidget(self.dst_btn)
        dst_row.addWidget(self.open_btn)
        grid.addLayout(dst_row, 5, 0, 1, 3)
        grid.setColumnStretch(0, 1)
        grid.setRowMinimumHeight(2, 26)
        grid.setRowMinimumHeight(4, 26)
        lay.addWidget(prof)

        log_card = card()
        log_lay = QVBoxLayout(log_card)
        log_lay.setContentsMargins(20, 14, 20, 18)
        log_lay.setSpacing(8)
        log_head = QHBoxLayout()
        log_head.addWidget(section("Activity"))
        log_head.addStretch(1)
        log_lay.addLayout(log_head)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_lay.addWidget(self.log_view, 1)
        lay.addWidget(log_card, 1)
        return page

    def _build_backups_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # --- save slots
        slots = card()
        slots.setFixedWidth(275)
        s_lay = QVBoxLayout(slots)
        s_lay.setContentsMargins(14, 14, 14, 14)
        s_lay.setSpacing(8)
        s_head = QHBoxLayout()
        s_head.addWidget(section("Save slots"))
        s_head.addStretch(1)
        self.slot_count = label("", "Dim", QFont(FONT_FAMILY, 9))
        s_head.addWidget(self.slot_count)
        s_lay.addLayout(s_head)
        self.slot_list = QListWidget()
        self.slot_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.slot_list.currentItemChanged.connect(self._on_slot_selected)
        s_lay.addWidget(self.slot_list, 1)
        self.slots_empty = label("No backups yet.\nStart watching or press Back up now.",
                                 "Muted", wrap=True)
        self.slots_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s_lay.addWidget(self.slots_empty, 1)
        lay.addWidget(slots)

        # --- versions of the selected slot
        vers = card()
        vers.setFixedWidth(300)
        v_lay = QVBoxLayout(vers)
        v_lay.setContentsMargins(14, 14, 14, 14)
        v_lay.setSpacing(8)
        v_head = QHBoxLayout()
        v_head.addWidget(section("Versions"))
        v_head.addStretch(1)
        self.version_count = label("", "Dim", QFont(FONT_FAMILY, 9))
        v_head.addWidget(self.version_count)
        v_lay.addLayout(v_head)
        self.version_list = QListWidget()
        self.version_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.version_list.currentItemChanged.connect(self._on_version_selected)
        v_lay.addWidget(self.version_list, 1)
        lay.addWidget(vers)

        # --- details + restore + undo
        det = card()
        d_lay = QVBoxLayout(det)
        d_lay.setContentsMargins(22, 16, 22, 18)
        d_lay.setSpacing(8)
        d_lay.addWidget(section("Selected version"))
        self.det_slot = ElidedLabel("", "Muted", Qt.TextElideMode.ElideRight)
        self.det_slot.setFont(head_font(15, QFont.Weight.DemiBold))
        d_lay.addWidget(self.det_slot)
        self.det_date = label("-", font=head_font(30, QFont.Weight.Bold, 0.5))
        self.det_date.setStyleSheet(f"color: {PALETTE['cyan']};")
        glow(self.det_date, PALETTE["cyan"], 26, 170)
        d_lay.addWidget(self.det_date)
        age_row = QHBoxLayout()
        self.det_time = label("", font=head_font(18, QFont.Weight.DemiBold, 1.0))
        age_row.addWidget(self.det_time)
        age_row.addSpacing(8)
        self.det_age = label("", "AgePill")
        age_row.addWidget(self.det_age)
        age_row.addStretch(1)
        d_lay.addLayout(age_row)
        d_lay.addSpacing(4)

        info = QGridLayout()
        info.setHorizontalSpacing(18)
        info.setVerticalSpacing(4)
        self.det_files = label("-", font=head_font(18, QFont.Weight.DemiBold))
        self.det_size = label("-", font=head_font(18, QFont.Weight.DemiBold))
        self.det_folder = label("-", font=head_font(18, QFont.Weight.DemiBold))
        for col, (title, value) in enumerate((("Files", self.det_files), ("Size", self.det_size),
                                              ("Folder", self.det_folder))):
            info.addWidget(label(title, "Muted", QFont(FONT_FAMILY, 9)), 0, col)
            info.addWidget(value, 1, col)
        info.setColumnStretch(2, 1)
        d_lay.addLayout(info)
        d_lay.addSpacing(6)

        act = QHBoxLayout()
        act.setSpacing(8)
        self.restore_btn = QPushButton("⟲  Restore this version")
        self.restore_btn.setObjectName("Restore")
        self.restore_btn.setMinimumHeight(44)
        self.restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_btn.clicked.connect(self.restore_selected)
        glow(self.restore_btn, PALETTE["magenta"], 26, 140)
        self.open_ver_btn = QPushButton("Open folder")
        self.open_ver_btn.setMinimumHeight(44)
        self.open_ver_btn.clicked.connect(self.open_selected_version)
        act.addWidget(self.restore_btn, 1)
        act.addWidget(self.open_ver_btn)
        d_lay.addLayout(act)
        self.restore_all_btn = QPushButton("⟲  Restore ALL saves to this moment")
        self.restore_all_btn.setObjectName("Small")
        self.restore_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_all_btn.setToolTip("Every file in the save folder goes back to how it was "
                                        "at this time - for games with an index or profile file.")
        self.restore_all_btn.clicked.connect(self.restore_all_selected)
        d_lay.addWidget(self.restore_all_btn)
        self.restore_note = label("", "Hint", wrap=True)
        d_lay.addWidget(self.restore_note)

        d_lay.addSpacing(6)
        d_lay.addWidget(divider())
        d_lay.addSpacing(4)
        u_head = QHBoxLayout()
        u_head.addWidget(section("Undo points"))
        u_head.addStretch(1)
        self.undo_btn = QPushButton("↶  Undo")
        self.undo_btn.setObjectName("Small")
        self.undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_btn.clicked.connect(self.undo_selected)
        u_head.addWidget(self.undo_btn)
        d_lay.addLayout(u_head)
        self.undo_list = QListWidget()
        self.undo_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.undo_list.currentItemChanged.connect(lambda *_: self._update_action_buttons())
        d_lay.addWidget(self.undo_list, 1)
        self.undo_empty = label("No undo points for this save. One is made automatically "
                                "every time you restore.", "Hint", wrap=True)
        d_lay.addWidget(self.undo_empty)
        lay.addWidget(det, 1)
        return page

    def _build_checkpoints_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        left = card()
        left.setFixedWidth(400)
        l_lay = QVBoxLayout(left)
        l_lay.setContentsMargins(14, 14, 14, 14)
        l_lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(section("Checkpoints"))
        head.addStretch(1)
        self.cp_count = label("", "Dim", QFont(FONT_FAMILY, 9))
        head.addWidget(self.cp_count)
        l_lay.addLayout(head)
        self.new_cp_btn = QPushButton("★  Save checkpoint now")
        self.new_cp_btn.setObjectName("Primary")
        self.new_cp_btn.setMinimumHeight(42)
        self.new_cp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_cp_btn.clicked.connect(self.save_checkpoint)
        l_lay.addWidget(self.new_cp_btn)
        self.cp_list = QListWidget()
        self.cp_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.cp_list.currentItemChanged.connect(self._on_checkpoint_selected)
        l_lay.addWidget(self.cp_list, 1)
        self.cp_empty = label("No checkpoints yet.\n\nBefore a big story choice or a boss fight, "
                              "press Save checkpoint and give it a name. Every save is backed up "
                              "and kept until you delete the checkpoint.", "Muted", wrap=True)
        self.cp_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l_lay.addWidget(self.cp_empty, 1)
        lay.addWidget(left)

        det = card()
        d = QVBoxLayout(det)
        d.setContentsMargins(22, 16, 22, 18)
        d.setSpacing(8)
        d.addWidget(section("Checkpoint"))
        self.cp_title = ElidedLabel("-", mode=Qt.TextElideMode.ElideRight)
        self.cp_title.setFont(head_font(30, QFont.Weight.Bold, 0.5))
        self.cp_title.setMinimumHeight(40)
        self.cp_title.setStyleSheet(f"color: {PALETTE['magenta']};")
        glow(self.cp_title, PALETTE["magenta"], 26, 160)
        d.addWidget(self.cp_title)
        when_row = QHBoxLayout()
        self.cp_when = label("", font=head_font(17, QFont.Weight.DemiBold, 0.8))
        when_row.addWidget(self.cp_when)
        when_row.addSpacing(8)
        self.cp_age = label("", "AgePill")
        when_row.addWidget(self.cp_age)
        when_row.addStretch(1)
        d.addLayout(when_row)
        d.addSpacing(4)
        info = QGridLayout()
        info.setHorizontalSpacing(28)
        info.setVerticalSpacing(4)
        self.cp_saves_n = label("-", font=head_font(18, QFont.Weight.DemiBold))
        self.cp_files = label("-", font=head_font(18, QFont.Weight.DemiBold))
        self.cp_size = label("-", font=head_font(18, QFont.Weight.DemiBold))
        for col, (title, value) in enumerate((("Saves", self.cp_saves_n), ("Files", self.cp_files),
                                              ("Size", self.cp_size))):
            info.addWidget(label(title, "Muted", QFont(FONT_FAMILY, 9)), 0, col)
            info.addWidget(value, 1, col)
        info.setColumnStretch(3, 1)
        d.addLayout(info)
        d.addSpacing(6)

        act = QHBoxLayout()
        act.setSpacing(8)
        self.cp_restore_btn = QPushButton("⟲  Restore checkpoint")
        self.cp_restore_btn.setObjectName("Restore")
        self.cp_restore_btn.setMinimumHeight(44)
        self.cp_restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cp_restore_btn.clicked.connect(self.restore_selected_checkpoint)
        glow(self.cp_restore_btn, PALETTE["magenta"], 26, 140)
        self.cp_rename_btn = QPushButton("Rename")
        self.cp_rename_btn.setMinimumHeight(44)
        self.cp_rename_btn.clicked.connect(self.rename_selected_checkpoint)
        self.cp_delete_btn = QPushButton("Delete")
        self.cp_delete_btn.setObjectName("Danger")
        self.cp_delete_btn.setMinimumHeight(44)
        self.cp_delete_btn.clicked.connect(self.delete_selected_checkpoint)
        act.addWidget(self.cp_restore_btn, 1)
        act.addWidget(self.cp_rename_btn)
        act.addWidget(self.cp_delete_btn)
        d.addLayout(act)
        d.addWidget(label("Restoring puts every save of this checkpoint back. Each save it replaces "
                          "is kept as an undo point first (Backups & Restore tab), and saves you "
                          "made after the checkpoint are never deleted. Close the game first.",
                          "Hint", wrap=True))
        d.addSpacing(6)
        d.addWidget(divider())
        d.addSpacing(4)
        d.addWidget(section("Saves in this checkpoint"))
        self.cp_saves = QListWidget()
        self.cp_saves.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.cp_saves.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        d.addWidget(self.cp_saves, 1)
        lay.addWidget(det, 1)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        box = card()
        b = QVBoxLayout(box)
        b.setContentsMargins(22, 18, 22, 20)
        b.setSpacing(12)
        b.addWidget(section("Behaviour  (all games)"))
        self.startup_chk = QCheckBox("Take a full backup immediately when Start is pressed")
        self.tray_chk = QCheckBox("Keep running in the tray when the window is closed")
        self.autowatch_chk = QCheckBox("Start watching the selected game automatically on launch")
        self.autostart_chk = QCheckBox("Start with Windows (minimized)")
        self.autostart_chk.setEnabled(autostart.supported())
        if not autostart.supported():
            self.autostart_chk.setToolTip("Only available on Windows")
        self.autostart_chk.toggled.connect(self._on_autostart_toggled)
        for chk in (self.startup_chk, self.tray_chk, self.autowatch_chk):
            chk.toggled.connect(self._schedule_save)
            b.addWidget(chk)
        b.addWidget(self.autostart_chk)

        b.addSpacing(8)
        b.addWidget(section("Restore safety"))
        undo_row = QHBoxLayout()
        undo_row.addWidget(label("Keep undo points for"))
        self.undo_days_spin = QSpinBox()
        self.undo_days_spin.setRange(0, 365)
        self.undo_days_spin.setSuffix(" days")
        self.undo_days_spin.setSpecialValueText("forever")
        self.undo_days_spin.valueChanged.connect(self._schedule_save)
        undo_row.addWidget(stepper(self.undo_days_spin, 120))
        undo_row.addStretch(1)
        b.addLayout(undo_row)
        b.addWidget(label("Before a backup is restored, the save it replaces is copied to an "
                          "undo point inside the backup folder (.savewatch_undo). Expired undo "
                          "points are cleaned up automatically during the next check.",
                          "Hint", wrap=True))
        lay.addWidget(box)

        about = card()
        a = QVBoxLayout(about)
        a.setContentsMargins(22, 18, 22, 20)
        a.setSpacing(8)
        a.addWidget(section("About"))
        a.addWidget(label(f"SaveWatch {__version__}  -  free and open source (MIT). "
                          "No telemetry, no network access.", wrap=True))
        cfg_row = QHBoxLayout()
        cfg_btn = QPushButton("Open settings folder")
        cfg_btn.setObjectName("Small")
        cfg_btn.clicked.connect(lambda: self._open_path(os.path.dirname(config_path())))
        cfg_row.addWidget(cfg_btn)
        cfg_row.addStretch(1)
        a.addLayout(cfg_row)
        lay.addWidget(about)
        lay.addStretch(1)
        return page

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
        self.tray_checkpoint = QAction("Save checkpoint...", menu)
        self.tray_checkpoint.triggered.connect(self.save_checkpoint)
        self.tray_open = QAction("Open backup folder", menu)
        self.tray_open.triggered.connect(self.open_backup_folder)
        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(self.quit_app)
        for act in (self.tray_show, self.tray_toggle, self.tray_now, self.tray_checkpoint,
                    self.tray_open):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _set_tray_status(self, text: str) -> None:
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME} - {self.profile.name}: {text}")

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

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        style_title_bar(self)

    def _on_tab(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 1:
            self._refresh_backups()
        elif index == 2:
            self._refresh_checkpoints()

    # ----------------------------------------------------------- profiles
    def _profile_subtitle(self, p: GameProfile) -> str:
        return p.source or "no save folder yet"

    def _fill_profile_list(self) -> None:
        self._loading = True
        self.profile_list.clear()
        for p in self.settings.profiles:
            avatar = Avatar()
            avatar.set_name(p.name)
            row = RowWidget(p.name, self._profile_subtitle(p), avatar,
                            sub_mode=Qt.TextElideMode.ElideMiddle)
            item = QListWidgetItem()
            item.setData(ROLE, p.id)
            item.setSizeHint(QSize(0, 62))
            self.profile_list.addItem(item)
            self.profile_list.setItemWidget(item, row)
            if p.id == self.settings.active_profile:
                self.profile_list.setCurrentItem(item)
        self._loading = False
        self._update_profile_rows()

    def _profile_row(self, profile_id: str) -> tuple[QListWidgetItem, RowWidget] | None:
        for i in range(self.profile_list.count()):
            item = self.profile_list.item(i)
            if item.data(ROLE) == profile_id:
                return item, self.profile_list.itemWidget(item)
        return None

    def _update_profile_rows(self) -> None:
        for p in self.settings.profiles:
            found = self._profile_row(p.id)
            if not found:
                continue
            _, row = found
            row.title.set_full_text(p.name)
            row.sub.set_full_text(self._profile_subtitle(p))
            if isinstance(row.lead, Avatar):
                row.lead.set_name(p.name)
            row.set_badge("LIVE" if self.running and p.id == self.watched_id else "")
        n = len(self.settings.profiles)
        self.profile_count.setText(f"{n} game{'s' if n != 1 else ''}")
        self.del_btn.setEnabled(n > 1 and not self.running)

    def _on_profile_selected(self, current: QListWidgetItem | None, _prev=None) -> None:
        if self._loading or current is None:
            return
        self.settings.active_profile = current.data(ROLE)
        self._load_profile_fields()
        self._schedule_save()

    def _load_profile_fields(self) -> None:
        p = self.profile
        self._loading = True
        self.name_edit.setText(p.name)
        self.src_edit.setText(p.source)
        self.dst_edit.setText(p.backup)
        self.interval_spin.setValue(p.interval_minutes)
        self.keep_spin.setValue(p.keep)
        self._loading = False
        self._stats_cache.clear()
        self._refresh_header()
        self._refresh_backups()
        self._refresh_checkpoints()

    def _on_name_edited(self, text: str) -> None:
        self.profile.name = text.strip() or DEFAULT_PROFILE_NAME
        self._update_profile_rows()
        self._refresh_header()
        self._schedule_save()

    def _on_profile_field(self, *_args) -> None:
        if self._loading:
            return
        p = self.profile
        p.source = self.src_edit.text().strip()
        p.backup = self.dst_edit.text().strip()
        p.interval_minutes = self.interval_spin.value()
        p.keep = self.keep_spin.value()
        self._update_profile_rows()
        self._refresh_header()
        self._schedule_save()

    def add_profile(self) -> None:
        if self.running:
            return
        p = self.settings.add_profile()
        self.settings.active_profile = p.id
        self._fill_profile_list()
        self._load_profile_fields()
        self.tabs[0].click()
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self._schedule_save()
        self.log("info", "New game profile added - name it and pick its save folder.")

    def delete_profile(self) -> None:
        if self.running or len(self.settings.profiles) < 2:
            return
        p = self.profile
        answer = QMessageBox.question(
            self, APP_NAME,
            f"Delete the profile '{p.name}'?\n\nOnly the profile is removed. "
            "Its backup folder and every backup in it stay on disk.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.settings.remove_profile(p.id)
        self._fill_profile_list()
        self._load_profile_fields()
        self._schedule_save()
        self.log("info", f"Profile '{p.name}' deleted (its backups were kept).")

    def _browse_source(self) -> None:
        start = self.src_edit.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select the game's save folder", start)
        if not path:
            return
        path = os.path.normpath(path)
        self.src_edit.setText(path)
        p = self.profile
        # Fresh profile: name it after the game and suggest a backup folder.
        if p.name in (DEFAULT_PROFILE_NAME, "My game", ""):
            guess = guess_game_name(path)
            if guess:
                self.name_edit.setText(guess)
                self._on_name_edited(guess)
        if not self.dst_edit.text().strip():
            self.dst_edit.setText(self.settings.suggest_backup(self.profile.name))
        self._on_profile_field()

    def _browse_backup(self) -> None:
        start = self.dst_edit.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select the backup folder", start)
        if path:
            self.dst_edit.setText(os.path.normpath(path))
            self._on_profile_field()

    def _refresh_header(self) -> None:
        p = self.profile
        self.game_title.set_full_text(p.name)
        self.game_path.set_full_text(p.source or "Set the save folder below to start backing up "
                                                 "this game.")
        self._refresh_chips()

    def _refresh_chips(self) -> None:
        p = self.profile
        entries = list_backup_entries(p.backup) if p.backup and os.path.isdir(p.backup) else []
        versions = sum(e["versions"] for e in entries)
        latest = max((e["latest_time"] for e in entries if e["latest_time"]), default=None)
        texts = [f"{len(entries)} save slot{'s' if len(entries) != 1 else ''}",
                 f"{versions} version{'s' if versions != 1 else ''}",
                 f"last backup {humanize_age(latest)}" if latest else "no backups yet"]
        for chip, text in zip(self.chips, texts):
            chip.setText(text)

    # ------------------------------------------------------------ backups
    def _selected(self, lst: QListWidget) -> str | None:
        item = lst.currentItem()
        return item.data(ROLE) if item else None

    def _refresh_backups(self) -> None:
        root = self.profile.backup
        entries = list_backup_entries(root) if root and os.path.isdir(root) else []
        keep = self._selected(self.slot_list)

        self._loading = True
        self.slot_list.clear()
        for e in entries:
            row = RowWidget(e["name"], self._slot_subtitle(e))
            item = QListWidgetItem()
            item.setData(ROLE, e["name"])
            item.setSizeHint(QSize(0, 56))
            self.slot_list.addItem(item)
            self.slot_list.setItemWidget(item, row)
        self._loading = False

        self.slot_count.setText(str(len(entries)) if entries else "")
        self.slot_list.setVisible(bool(entries))
        self.slots_empty.setVisible(not entries)
        target = 0
        for i in range(self.slot_list.count()):
            if self.slot_list.item(i).data(ROLE) == keep:
                target = i
        if entries:
            self.slot_list.setCurrentRow(target)
            self._on_slot_selected(self.slot_list.currentItem())
        else:
            self._on_slot_selected(None)

    def _slot_subtitle(self, e: dict) -> str:
        n = e["versions"]
        return f"{n} version{'s' if n != 1 else ''}  -  last {humanize_age(e['latest_time'])}"

    def _on_slot_selected(self, current: QListWidgetItem | None, _prev=None) -> None:
        if self._loading:
            return
        keep = self._selected(self.version_list)
        self._loading = True
        self.version_list.clear()
        name = current.data(ROLE) if current else None
        versions = list(reversed(list_versions(os.path.join(self.profile.backup, name)))) if name else []
        self._pins = pinned_versions(self.profile.backup, name) if name else {}
        for i, v in enumerate(versions):
            when = parse_stamp(v)
            title = when.strftime("%a %d %b  %H:%M:%S") if when else v
            sub = humanize_age(when)
            if v in self._pins:
                sub = f"★ {self._pins[v]}  -  {sub}"
            row = RowWidget(title, sub, title_font=head_font(15, QFont.Weight.DemiBold))
            if v in self._pins:
                row.set_badge("★ PINNED", "BadgeMagenta")
            elif i == 0:
                row.set_badge("LATEST", "BadgeCyan")
            item = QListWidgetItem()
            item.setData(ROLE, v)
            item.setSizeHint(QSize(0, 56))
            self.version_list.addItem(item)
            self.version_list.setItemWidget(item, row)
        self._loading = False
        self.version_count.setText(str(len(versions)) if versions else "")

        target = 0
        for i in range(self.version_list.count()):
            if self.version_list.item(i).data(ROLE) == keep:
                target = i
        if versions:
            self.version_list.setCurrentRow(target)
        self._on_version_selected(self.version_list.currentItem())
        self._fill_undo(name)

    def _version_stats(self, path: str) -> tuple[int, int]:
        # Versions never change once written, so their size is cached.
        if path not in self._stats_cache:
            self._stats_cache[path] = folder_stats(path)
        return self._stats_cache[path]

    def _on_version_selected(self, current: QListWidgetItem | None, _prev=None) -> None:
        if self._loading:
            return
        name = self._selected(self.slot_list)
        version = current.data(ROLE) if current else None
        if not name or not version:
            self.det_slot.set_full_text("Pick a save slot and a version" if name is None else name)
            self.det_date.setText("-")
            self.det_time.setText("")
            self.det_age.hide()
            for w in (self.det_files, self.det_size, self.det_folder):
                w.setText("-")
            self.restore_note.setText("")
            self._update_action_buttons()
            return
        path = os.path.join(self.profile.backup, name, version)
        when = parse_stamp(version)
        files, size = self._version_stats(path)
        pin = getattr(self, "_pins", {}).get(version)
        self.det_slot.set_full_text(f"{name}   ★ {pin}" if pin else name)
        self.det_date.setText(when.strftime("%d %b %Y") if when else version)
        self.det_time.setText(when.strftime("%A   %H:%M:%S") if when else "")
        self.det_age.setText(humanize_age(when))
        self.det_age.show()
        self.det_files.setText(f"{files:,}")
        self.det_size.setText(human_bytes(size))
        self.det_folder.setText(version)
        days = self.settings.undo_days
        keep = f"for {days} day{'s' if days != 1 else ''}" if days else "until you delete it"
        companions = [n for n, _ in related_saves(self.profile.backup, name, version)]
        with_them = (f" Its companion file{'s' if len(companions) != 1 else ''} "
                     f"{', '.join(companions[:3])}{'...' if len(companions) > 3 else ''} "
                     f"{'comes' if len(companions) == 1 else 'come'} back from the same moment."
                     if companions else "")
        self.restore_note.setText(
            f"Restoring copies this version back into the game's save folder. Your current "
            f"'{name}' is kept as an undo point {keep} first.{with_them} "
            "Close the game before restoring.")
        self._update_action_buttons()

    def _fill_undo(self, name: str | None) -> None:
        self.undo_list.clear()
        points = list_undo_points(self.profile.backup, name) if name and self.profile.backup else []
        for pt in points:
            when = pt["created_time"]
            what = pt.get("reason") or "before restore"
            size = ("save did not exist" if pt.get("absent")
                    else f"{plural(pt.get('files', 0), 'file')}, {human_bytes(pt.get('bytes', 0))}")
            stamp = when.strftime("%d %b %H:%M") if when else "?"
            row = RowWidget(what[:1].upper() + what[1:],
                            f"{stamp}  -  {humanize_age(when)}  -  {size}",
                            title_font=head_font(14, QFont.Weight.DemiBold))
            item = QListWidgetItem()
            item.setData(ROLE, pt["id"])
            item.setSizeHint(QSize(0, 54))
            self.undo_list.addItem(item)
            self.undo_list.setItemWidget(item, row)
        if points:
            self.undo_list.setCurrentRow(0)
        self.undo_list.setVisible(bool(points))
        self.undo_empty.setVisible(not points)
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        has_version = bool(self._selected(self.slot_list) and self._selected(self.version_list))
        self.restore_btn.setEnabled(has_version and not self.busy)
        self.restore_all_btn.setEnabled(has_version and not self.busy)
        self.open_ver_btn.setEnabled(has_version)
        self.undo_btn.setEnabled(self.undo_list.currentItem() is not None and not self.busy)
        if hasattr(self, "cp_list"):
            has_cp = self.cp_list.currentItem() is not None
            for b in (self.cp_restore_btn, self.cp_rename_btn, self.cp_delete_btn):
                b.setEnabled(has_cp and not self.busy)

    def _refresh_ages(self) -> None:
        self._refresh_chips()
        if self.pages.currentIndex() == 1:
            self._refresh_backups()
        elif self.pages.currentIndex() == 2:
            self._refresh_checkpoints()

    def _check_restore_paths(self) -> tuple[str, str] | None:
        p = self.profile
        src = os.path.normpath(p.source) if p.source else ""
        dst = os.path.normpath(p.backup) if p.backup else ""
        if not src or not os.path.isdir(src):
            self._warn("The game's save folder does not exist, so there is nowhere to restore to.")
            return None
        if not dst or not os.path.isdir(dst):
            self._warn("The backup folder does not exist.")
            return None
        return src, dst

    def restore_selected(self) -> None:
        name = self._selected(self.slot_list)
        version = self._selected(self.version_list)
        if not name or not version or self.busy:
            return
        paths = self._check_restore_paths()
        if not paths:
            return
        when = parse_stamp(version)
        live = os.path.exists(os.path.join(paths[0], name))
        days = self.settings.undo_days
        keep = f"kept for {days} days" if days else "kept until you delete it"
        current = (f"Your current '{name}' is saved as an undo point first ({keep}), "
                   "then replaced." if live else
                   f"'{name}' is not in the save folder right now; it will be created.")
        companions = [n for n, _ in related_saves(paths[1], name, version)]
        if companions:
            shown = ", ".join(companions[:5]) + (f" and {len(companions) - 5} more"
                                                 if len(companions) > 5 else "")
            current += (f"\n\nIts companion files are restored from the same moment too, so "
                        f"they match: {shown}.")
        box = QMessageBox(self)
        box.setWindowTitle("Restore save")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"<b>Restore '{name}'</b><br>to the version from "
                    f"<b>{fmt_when(when)}</b> ({humanize_age(when)})?")
        box.setInformativeText(f"{current}\n\nClose the game before restoring, or it may "
                               "overwrite the restored save.")
        ok = box.addButton("Restore", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(ok)
        box.exec()
        if box.clickedButton() is not ok:
            return
        self._run_job({"kind": "restore", "source": paths[0], "backup": paths[1],
                       "name": name, "ref": version})

    def restore_all_selected(self) -> None:
        version = self._selected(self.version_list)
        if not version or self.busy:
            return
        paths = self._check_restore_paths()
        if not paths:
            return
        when = parse_stamp(version)
        box = QMessageBox(self)
        box.setWindowTitle("Restore all saves")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"<b>Put every save back</b> the way it was at<br>"
                    f"<b>{fmt_when(when)}</b> ({humanize_age(when)})?")
        box.setInformativeText(
            "Use this for games that keep an index or profile file next to their saves: "
            "every file in the backup goes back to that same moment, so they all match.\n\n"
            "Each file that gets replaced is kept as an undo point first, and one Undo puts "
            "them all back. Saves created after that moment are not deleted.\n\n"
            "Close the game before restoring.")
        ok = box.addButton("Restore all", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(ok)
        box.exec()
        if box.clickedButton() is not ok:
            return
        self._run_job({"kind": "restore_all", "source": paths[0], "backup": paths[1],
                       "name": fmt_when(when), "ref": version})

    def undo_selected(self) -> None:
        name = self._selected(self.slot_list)
        item = self.undo_list.currentItem()
        if not name or item is None or self.busy:
            return
        paths = self._check_restore_paths()
        if not paths:
            return
        undo_id = item.data(ROLE)
        point = next((p for p in list_undo_points(paths[1], name) if p["id"] == undo_id), {})
        group = point.get("group")
        others = [p["entry"] for p in list_undo_points(paths[1])
                  if group and p.get("group") == group and p["entry"] != name]
        also = (f"\n\nThis restore also replaced {plural(len(others), 'other file')} "
                f"({', '.join(others[:5])}{'...' if len(others) > 5 else ''}); "
                "they are put back too." if others else "")
        answer = QMessageBox.question(
            self, "Undo restore",
            f"Put '{name}' back the way it was at {fmt_when(parse_stamp(undo_id))}?{also}\n\n"
            "What is in the save folder now becomes a new undo point, so this can be undone too.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_job({"kind": "undo", "source": paths[0], "backup": paths[1],
                       "name": name, "ref": undo_id})

    JOB_TEXT = {  # kind: (status pill, verb for errors)
        "restore": ("RESTORING...", "restore"),
        "undo": ("UNDOING...", "undo"),
        "checkpoint": ("SAVING CHECKPOINT...", "save the checkpoint"),
        "restore_checkpoint": ("RESTORING CHECKPOINT...", "restore the checkpoint"),
        "restore_all": ("RESTORING ALL SAVES...", "restore all saves to"),
    }

    def _run_job(self, job: dict) -> None:
        self._set_busy(True)
        self.tick_timer.stop()
        self._set_status(self.JOB_TEXT[job["kind"]][0], "busy")
        self.request_job.emit(job)

    def on_job_done(self, result: dict) -> None:
        self._set_busy(False)
        kind, name = result["kind"], result["name"]
        if result.get("ok"):
            related = result.get("related") or []
            count = result.get("count", 1)
            msg = {
                "restore": f"'{name}' restored" + (f" with {', '.join(related)}" if related else "")
                           + ". The previous save is an undo point.",
                "undo": f"'{name}' put back" + (f" (+{count - 1} more)" if count > 1 else "") + ".",
                "checkpoint": f"Checkpoint '{name}' saved.",
                "restore_checkpoint": f"Checkpoint '{name}' restored.",
                "restore_all": f"{len(result.get('restored', []))} saves put back to {name}.",
            }[kind]
            if self.tray and self.isHidden():
                self.tray.showMessage(APP_NAME, msg, QSystemTrayIcon.MessageIcon.Information, 4000)
            if result.get("errors"):
                self._warn(f"{msg}\n\nSome saves were skipped:\n" + "\n".join(result["errors"][:8]) +
                           "\n\nIf the game is running, close it and try again.")
        else:
            verb = self.JOB_TEXT[kind][1]
            self.log("error", f"could not {verb} '{name}': {result['error']} - nothing was changed")
            self._warn(f"Could not {verb} '{name}':\n{result['error']}\n\n"
                       "Nothing was changed. If the game is running, close it and try again.")
        self._refresh_chips()
        self._refresh_backups()
        self._refresh_checkpoints(select=result.get("id") if kind == "checkpoint" else None)
        self._resume_after_busy()

    # -------------------------------------------------------- checkpoints
    def save_checkpoint(self) -> None:
        if self.busy:
            return
        if self.isHidden():
            self.show_window()
        paths = self._validate()
        if not paths:
            return
        default = f"Checkpoint {datetime.now():%d %b %H:%M}"
        title, ok = QInputDialog.getText(
            self, "Save checkpoint",
            f"Back up every save of {self.profile.name} right now.\n"
            "Name it so you can find it later, e.g. 'Before the big choice':",
            QLineEdit.EchoMode.Normal, default)
        if not ok:
            return
        title = " ".join(title.split()) or default
        self._save_settings()
        self._run_job({"kind": "checkpoint", "source": paths[0], "backup": paths[1],
                       "name": title, "ref": title})

    def _refresh_checkpoints(self, select: str | None = None) -> None:
        root = self.profile.backup
        items = load_checkpoints(root) if root and os.path.isdir(root) else []
        keep = select or self._selected(self.cp_list)
        self._checkpoints = {cp["id"]: cp for cp in items}
        self._loading = True
        self.cp_list.clear()
        for cp in items:
            when = cp["created_time"]
            n = len(cp["entries"])
            sub = (f"{when.strftime('%d %b %Y  %H:%M') if when else '?'}  -  "
                   f"{humanize_age(when)}  -  {n} save{'s' if n != 1 else ''}")
            star = label("★")
            star.setFont(head_font(20, QFont.Weight.Bold))
            star.setStyleSheet(f"color: {PALETTE['magenta']};")
            row = RowWidget(cp["title"], sub, star)
            item = QListWidgetItem()
            item.setData(ROLE, cp["id"])
            item.setSizeHint(QSize(0, 60))
            self.cp_list.addItem(item)
            self.cp_list.setItemWidget(item, row)
        self._loading = False
        self.cp_count.setText(str(len(items)) if items else "")
        self.cp_list.setVisible(bool(items))
        self.cp_empty.setVisible(not items)
        target = 0
        for i in range(self.cp_list.count()):
            if self.cp_list.item(i).data(ROLE) == keep:
                target = i
        if items:
            self.cp_list.setCurrentRow(target)
        self._on_checkpoint_selected(self.cp_list.currentItem())

    def _on_checkpoint_selected(self, current: QListWidgetItem | None, _prev=None) -> None:
        if self._loading:
            return
        cp = self._checkpoints.get(current.data(ROLE)) if current else None
        self.cp_saves.clear()
        if cp is None:
            self.cp_title.set_full_text("-")
            self.cp_when.setText("")
            self.cp_age.hide()
            for w in (self.cp_saves_n, self.cp_files, self.cp_size):
                w.setText("-")
            self._update_action_buttons()
            return
        when = cp["created_time"]
        self.cp_title.set_full_text(cp["title"])
        self.cp_when.setText(when.strftime("%A %d %b %Y   %H:%M:%S") if when else cp["id"])
        self.cp_age.setText(humanize_age(when))
        self.cp_age.show()
        self.cp_saves_n.setText(str(len(cp["entries"])))
        self.cp_files.setText(f"{cp.get('files', 0):,}")
        self.cp_size.setText(human_bytes(cp.get("bytes", 0)))
        for e in cp["entries"]:
            present = os.path.isdir(os.path.join(self.profile.backup, e["name"], e["version"]))
            row = RowWidget(e["name"], f"version {e['version']}" if present
                            else "backup missing - deleted outside SaveWatch",
                            title_font=head_font(14, QFont.Weight.DemiBold))
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 50))
            self.cp_saves.addItem(item)
            self.cp_saves.setItemWidget(item, row)
        self._update_action_buttons()

    def _selected_checkpoint(self) -> dict | None:
        cp_id = self._selected(self.cp_list)
        return getattr(self, "_checkpoints", {}).get(cp_id) if cp_id else None

    def restore_selected_checkpoint(self) -> None:
        cp = self._selected_checkpoint()
        if cp is None or self.busy:
            return
        paths = self._check_restore_paths()
        if not paths:
            return
        names = [e["name"] for e in cp["entries"]]
        shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
        when = cp["created_time"]
        box = QMessageBox(self)
        box.setWindowTitle("Restore checkpoint")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"<b>Restore checkpoint '{cp['title']}'</b><br>from {fmt_when(when)} "
                    f"({humanize_age(when)})?")
        box.setInformativeText(
            f"These {len(names)} saves will be put back: {shown}.\n\n"
            "Each save that gets replaced is kept as an undo point first. Saves you made after "
            "this checkpoint are not deleted.\n\nClose the game before restoring.")
        ok = box.addButton("Restore", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(ok)
        box.exec()
        if box.clickedButton() is not ok:
            return
        self._run_job({"kind": "restore_checkpoint", "source": paths[0], "backup": paths[1],
                       "name": cp["title"], "ref": cp["id"]})

    def rename_selected_checkpoint(self) -> None:
        cp = self._selected_checkpoint()
        if cp is None or self.busy:
            return
        title, ok = QInputDialog.getText(self, "Rename checkpoint", "New name:",
                                         QLineEdit.EchoMode.Normal, cp["title"])
        if not ok or not title.strip():
            return
        try:
            rename_checkpoint(self.profile.backup, cp["id"], title)
        except OSError as exc:
            self._warn(f"Could not rename the checkpoint:\n{exc}")
        self._refresh_checkpoints()
        self._refresh_backups()

    def delete_selected_checkpoint(self) -> None:
        cp = self._selected_checkpoint()
        if cp is None or self.busy:
            return
        answer = QMessageBox.question(
            self, "Delete checkpoint",
            f"Delete the checkpoint '{cp['title']}'?\n\nIts backups are not deleted right away, "
            "but they are no longer protected: 'Versions to keep' may prune them later.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_checkpoint(self.profile.backup, cp["id"])
            self.log("info", f"checkpoint '{cp['title']}' deleted (its backups were unpinned)")
        except OSError as exc:
            self._warn(f"Could not delete the checkpoint:\n{exc}")
        self._refresh_checkpoints()
        self._refresh_backups()

    def open_selected_version(self) -> None:
        name = self._selected(self.slot_list)
        version = self._selected(self.version_list)
        if name and version:
            self._open_path(os.path.join(self.profile.backup, name, version))

    # ------------------------------------------------------------ logging
    def log(self, level: str, text: str) -> None:
        color = LEVEL_COLOR.get(level, PALETTE["text"])
        stamp = datetime.now().strftime("%H:%M:%S")
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace("\n", "<br>&nbsp;&nbsp;&nbsp;&nbsp;"))
        self.log_view.appendHtml(
            f'<span style="color:{STAMP_COLOR}">{stamp}</span>&nbsp; '
            f'<span style="color:{color}">{safe}</span>'
        )
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        if self.tray and level in ("error", "alert") and self.isHidden():
            self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Warning, 6000)

    # ----------------------------------------------------------- settings
    def _apply_settings(self) -> None:
        s = self.settings
        self._loading = True
        self.startup_chk.setChecked(s.full_backup_on_start)
        self.tray_chk.setChecked(s.minimize_to_tray and self.tray is not None)
        self.autowatch_chk.setChecked(s.auto_start_watching)
        self.undo_days_spin.setValue(s.undo_days)
        self.autostart_chk.blockSignals(True)
        self.autostart_chk.setChecked(autostart.is_enabled())
        self.autostart_chk.blockSignals(False)
        self._loading = False
        self._fill_profile_list()
        self._load_profile_fields()

    def _collect_settings(self) -> None:
        s = self.settings
        s.full_backup_on_start = self.startup_chk.isChecked()
        s.minimize_to_tray = self.tray_chk.isChecked()
        s.auto_start_watching = self.autowatch_chk.isChecked()
        s.undo_days = self.undo_days_spin.value()

    def _schedule_save(self, *_args) -> None:
        if not self._loading:
            self._save_timer.start()

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
        p = self.profile
        src = os.path.normpath(p.source) if p.source else ""
        dst = os.path.normpath(p.backup) if p.backup else ""
        if not src or not os.path.isdir(src):
            self._warn(f"The save folder for '{p.name}' does not exist.", from_tray)
            return None
        if not dst:
            self._warn(f"Pick a backup folder for '{p.name}'.", from_tray)
            return None
        # Backing up into the folder you are watching would copy the copies.
        if is_inside(dst, src) or is_inside(src, dst):
            self._warn("The backup folder and the save folder must not be inside each other.",
                       from_tray)
            return None
        for other in self.settings.profiles:
            if other.id != p.id and other.backup and \
                    os.path.normcase(os.path.abspath(other.backup)) == os.path.normcase(os.path.abspath(dst)):
                self._warn(f"That backup folder is already used by '{other.name}'. "
                           "Give every game its own backup folder.", from_tray)
                return None
        try:
            os.makedirs(dst, exist_ok=True)
        except OSError as exc:
            self._warn(f"Cannot create the backup folder:\n{exc}", from_tray)
            return None
        return src, dst

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for w in (self.src_edit, self.dst_edit, self.src_btn, self.dst_btn, self.name_edit,
                  self.interval_spin.parentWidget(), self.keep_spin.parentWidget(),
                  self.startup_chk, self.profile_list, self.add_btn):
            w.setEnabled(enabled)
        self.lock_hint.setVisible(not enabled)
        self._update_profile_rows()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for b in (self.now_btn, self.checkpoint_btn, self.new_cp_btn):
            b.setEnabled(not busy)
        if self.tray:
            self.tray_now.setEnabled(not busy)
            self.tray_checkpoint.setEnabled(not busy)
        self._update_action_buttons()

    def _configure_worker(self, src: str, dst: str) -> None:
        self.worker.source = src
        self.worker.backup = dst
        self.worker.keep = self.keep_spin.value()
        self.worker.undo_days = self.undo_days_spin.value()
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
        self.watched_id = self.profile.id
        self._show_running_buttons(True)
        self._set_inputs_enabled(False)
        if self.tray:
            self.tray_toggle.setText("Stop watching")
        self.log("info", f"[{self.profile.name}] watching {src}")
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
            self._set_status("FINISHING COPY...", "busy")
            self.log("info", "Stopping after the current copy finishes...")

    def _set_status(self, text: str, state: str) -> None:
        self.pill.setText(text)
        if self.pill.property("state") != state:
            self.pill.setProperty("state", state)
            repolish(self.pill)
            color = {"watching": PALETTE["lime"], "busy": PALETTE["cyan"],
                     "error": PALETTE["red"]}.get(state)
            if state == "watching":
                self.pulse.start()
            else:
                self.pulse.stop()
                c = QColor(color or "#000000")
                c.setAlpha(170 if color else 0)
                self.pill_glow.setColor(c)
                self.pill_glow.setBlurRadius(24 if color else 0)
        self._set_tray_status(text.title())

    def _on_pulse(self, value: float) -> None:
        c = QColor(PALETTE["lime"])
        c.setAlpha(int(70 + 130 * value))
        self.pill_glow.setColor(c)
        self.pill_glow.setBlurRadius(14 + 22 * value)

    def _show_running_buttons(self, running: bool) -> None:
        self.start_btn.setVisible(not running)
        self.start_btn.setEnabled(not running)
        self.stop_btn.setVisible(running)
        self.stop_btn.setEnabled(running)

    def _back_to_idle(self) -> None:
        self.watched_id = ""
        self._show_running_buttons(False)
        self.now_btn.setEnabled(True)
        self._set_inputs_enabled(True)
        self._set_status("IDLE", "idle")

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
        self._set_busy(True)
        self.tick_timer.stop()
        self._set_status("SCANNING...", "busy")
        # Queued across threads - returns immediately, GUI stays responsive.
        self.request_cycle.emit()

    def on_cycle_done(self, stats: dict) -> None:
        self._set_busy(False)
        self.log("info" if not stats["errors"] else "warn", summarize_stats(stats))
        if stats["copied"]:
            self._refresh_chips()
            if self.pages.currentIndex() == 1:
                self._refresh_backups()
        self._resume_after_busy()

    def _resume_after_busy(self) -> None:
        """After a cycle or a restore: quit, schedule the next check, or go idle."""
        if self.quitting:
            self.quit_app()
            return
        if self.running:
            if not self.cycle_timer.isActive():
                ms = max(1000, int(self.interval_spin.value() * 60_000))
                self.seconds_left = ms // 1000
                self.cycle_timer.start(ms)
            self.tick_timer.start()
            self.on_tick()
        elif self.watched_id:
            self._back_to_idle()
            self.log("info", "Stopped.")
        else:
            self._set_status("IDLE", "idle")

    def on_tick(self) -> None:
        if not self.running or self.busy:
            return
        self.seconds_left = max(0, self.cycle_timer.remainingTime() // 1000)
        m, s = divmod(self.seconds_left, 60)
        self._set_status(f"WATCHING  -  NEXT CHECK {m:02d}:{s:02d}", "watching")

    def _open_path(self, path: str) -> None:
        if os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self._warn("That folder does not exist yet.", from_tray=self.isHidden())

    def open_backup_folder(self) -> None:
        path = self.profile.backup.strip()
        if path:
            self._open_path(os.path.normpath(path))
        else:
            self._warn("Pick a backup folder first.", from_tray=self.isHidden())

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
            self._set_status("FINISHING, THEN QUITTING...", "busy")
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


def dark_palette() -> QPalette:
    """Base palette so anything the stylesheet does not reach is still dark."""
    pal = QPalette()
    bg, panel, text = QColor(PALETTE["panel"]), QColor("#090a16"), QColor(PALETTE["text"])
    for role, color in ((QPalette.ColorRole.Window, bg), (QPalette.ColorRole.Base, panel),
                        (QPalette.ColorRole.AlternateBase, bg), (QPalette.ColorRole.Button, bg),
                        (QPalette.ColorRole.WindowText, text), (QPalette.ColorRole.Text, text),
                        (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.ToolTipBase, bg),
                        (QPalette.ColorRole.ToolTipText, text),
                        (QPalette.ColorRole.Highlight, QColor(PALETTE["cyan"])),
                        (QPalette.ColorRole.HighlightedText, QColor("#05060c"))):
        pal.setColor(role, color)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(PALETTE["dim"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(PALETTE["dim"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(PALETTE["dim"]))
    return pal


def setup_app(argv0: str) -> QApplication:
    app = QApplication([argv0])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    app.setPalette(dark_palette())
    font = QFont(FONT_FAMILY)
    font.setPixelSize(14)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(app_icon())
    return app


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    start_minimized = "--minimized" in argv

    app = setup_app(sys.argv[0])
    settings = Settings.load()
    win = SaveWatchWindow(settings)
    if start_minimized and win.tray and settings.minimize_to_tray:
        win.hide()
    else:
        win.show()
    active = settings.active()
    if settings.auto_start_watching and active and active.source and active.backup:
        QTimer.singleShot(300, lambda: win.start_watching(from_tray=win.isHidden()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
