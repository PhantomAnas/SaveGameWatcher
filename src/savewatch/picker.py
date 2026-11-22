"""Game picker: choose a game from the built-in list and find its save folder.

For a listed game every known location (Steam, Epic, GOG, Ubisoft Connect,
cracked / repacked copies) is checked on this PC; the ones that hold files
are shown first, newest save on top.  For any game - listed or not - the PC
can also be searched by the game's name.  Imported lazily by app.py.
"""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from .app import (
    FONT_FAMILY,
    ROLE,
    Avatar,
    Backdrop,
    CoverLoader,
    ElidedLabel,
    RowWidget,
    card,
    glow,
    head_font,
    label,
    rounded_cover,
    section,
    style_title_bar,
)
from .catalog import Candidate, Game, detect, find_game, load_catalog, search, search_by_name
from .core import humanize_age, plural
from .theme import PALETTE


class GamePicker(QDialog):
    """After exec(): `chosen` is {"game": Game | None, "name": str, "path": str}
    or None when cancelled.  `path` may be "" for "add without a folder"."""

    paintEvent = Backdrop.paintEvent  # same neon backdrop as the main window

    def __init__(self, parent, covers: CoverLoader, query: str = "", game_id: str = "",
                 title: str = "Add a game") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1160, 720)
        self.setMinimumSize(1000, 640)
        self.covers = covers
        self.chosen: dict | None = None
        self.game: Game | None = None
        self.custom_name = ""
        self.candidates: list[Candidate] = []
        self._loading = False

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)
        root.addWidget(self._build_list())
        root.addWidget(self._build_detail(), 1)
        covers.ready.connect(self._on_cover)

        self.search_edit.setText(query)
        self._fill_list(query)
        target = find_game(game_id) if game_id else None
        if target is None and query:
            matches = search(query)
            target = matches[0] if len(matches) == 1 else None
        if target is not None:
            self._select_game(target.id)
        elif query and not search(query):
            self._show_custom(query)
        elif self.game_list.count():
            self.game_list.setCurrentRow(0)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        style_title_bar(self)

    # ---------------------------------------------------------------- UI
    def _build_list(self):
        box = card()
        box.setFixedWidth(360)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(section("Game list"))
        head.addStretch(1)
        self.count_lbl = label("", "Dim", QFont(FONT_FAMILY, 9))
        head.addWidget(self.count_lbl)
        lay.addLayout(head)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search games...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_query)
        lay.addWidget(self.search_edit)
        self.game_list = QListWidget()
        self.game_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.game_list.currentItemChanged.connect(self._on_game_selected)
        lay.addWidget(self.game_list, 1)
        self.not_listed = label("", "Hint", wrap=True)
        lay.addWidget(self.not_listed)
        self.custom_btn = QPushButton("+  Game not listed? Add it yourself")
        self.custom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.custom_btn.clicked.connect(lambda: self._show_custom(self.search_edit.text().strip()))
        lay.addWidget(self.custom_btn)
        return box

    def _build_detail(self):
        box = card()
        d = QVBoxLayout(box)
        d.setContentsMargins(22, 18, 22, 18)
        d.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(18)
        self.cover = QLabel()
        self.cover.setFixedSize(96, 136)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.cover)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(section("Game"))
        self.title_lbl = ElidedLabel("", mode=Qt.TextElideMode.ElideRight)
        self.title_lbl.setFont(head_font(28, QFont.Weight.Bold, 0.5))
        self.title_lbl.setMinimumHeight(38)
        self.title_lbl.setStyleSheet("color: #ffffff;")
        glow(self.title_lbl, PALETTE["cyan"], 26, 140)
        titles.addWidget(self.title_lbl)
        self.custom_name_edit = QLineEdit()
        self.custom_name_edit.setPlaceholderText("Game name")
        self.custom_name_edit.textEdited.connect(self._on_custom_name)
        self.custom_name_edit.hide()
        titles.addWidget(self.custom_name_edit)
        self.meta_lbl = label("", "Muted", wrap=True)
        titles.addWidget(self.meta_lbl)
        titles.addStretch(1)
        top.addLayout(titles, 1)
        d.addLayout(top)

        d.addWidget(section("Where are the saves?"))
        self.hint_lbl = label("", "Hint", wrap=True)
        d.addWidget(self.hint_lbl)
        self.status_lbl = label("", font=head_font(15, QFont.Weight.DemiBold))
        d.addWidget(self.status_lbl)

        self.cand_list = QListWidget()
        self.cand_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.cand_list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.cand_list.itemDoubleClicked.connect(lambda *_: self._use_selected())
        d.addWidget(self.cand_list, 1)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.detect_btn = QPushButton("⟳  Detect again")
        self.detect_btn.setToolTip("Check every known location again - do this after the game "
                                   "has saved once.")
        self.detect_btn.clicked.connect(self._detect)
        self.name_btn = QPushButton("Search PC by name")
        self.name_btn.setToolTip("Look for folders named after the game in Documents, "
                                 "Saved Games and AppData.")
        self.name_btn.clicked.connect(self._search_by_name)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.setToolTip("Look inside the selected folder before choosing it.")
        self.open_btn.clicked.connect(self._open_selected)
        self.show_all = QCheckBox("Show locations not found")
        self.show_all.toggled.connect(lambda *_: self._fill_candidates())
        for w in (self.detect_btn, self.name_btn, self.open_btn):
            w.setObjectName("Small")
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            tools.addWidget(w)
        tools.addStretch(1)
        tools.addWidget(self.show_all)
        d.addLayout(tools)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.browse_btn = QPushButton("Choose folder myself...")
        self.browse_btn.clicked.connect(self._browse)
        self.skip_btn = QPushButton("Add without a folder")
        self.skip_btn.setToolTip("Create the profile now and set its save folder later.")
        self.skip_btn.clicked.connect(self._skip)
        bottom.addWidget(self.browse_btn)
        bottom.addWidget(self.skip_btn)
        bottom.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.use_btn = QPushButton("✓  Use this folder")
        self.use_btn.setObjectName("Primary")
        self.use_btn.setMinimumHeight(42)
        self.use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.use_btn.clicked.connect(self._use_selected)
        bottom.addWidget(cancel)
        bottom.addWidget(self.use_btn)
        d.addLayout(bottom)
        return box

    # -------------------------------------------------------------- list
    def _fill_list(self, query: str) -> None:
        games = search(query) if query.strip() else list(load_catalog())
        keep = self.game.id if self.game else None
        self._loading = True
        self.game_list.clear()
        for g in games:
            avatar = Avatar(34)
            avatar.set_name(g.name)
            avatar.set_cover(self.covers.get(g.steam))
            n = len(g.locations)
            row = RowWidget(g.name, f"{plural(n, 'known save location')}", avatar,
                            title_font=head_font(14, QFont.Weight.DemiBold))
            item = QListWidgetItem()
            item.setData(ROLE, g.id)
            item.setSizeHint(QSize(0, 54))
            self.game_list.addItem(item)
            self.game_list.setItemWidget(item, row)
            if g.id == keep:
                self.game_list.setCurrentItem(item)
        self._loading = False
        total = len(load_catalog())
        self.count_lbl.setText(f"{len(games)} / {total}" if query.strip() else str(total))
        self.not_listed.setText(f"No game in the list matches '{query.strip()}'."
                                if query.strip() and not games else "")
        self.not_listed.setVisible(bool(query.strip() and not games))

    def _on_query(self, text: str) -> None:
        self._fill_list(text)
        if self.game_list.count() and self.game_list.currentItem() is None:
            self.game_list.setCurrentRow(0)

    def _select_game(self, game_id: str) -> None:
        for i in range(self.game_list.count()):
            if self.game_list.item(i).data(ROLE) == game_id:
                self.game_list.setCurrentRow(i)
                return

    def _on_game_selected(self, current, _prev=None) -> None:
        if self._loading or current is None:
            return
        game = find_game(current.data(ROLE))
        if game is not None:
            self._show_game(game)

    def _on_cover(self, steam_id: int, path: str) -> None:
        for i in range(self.game_list.count()):
            g = find_game(self.game_list.item(i).data(ROLE))
            if g and g.steam == steam_id:
                row = self.game_list.itemWidget(self.game_list.item(i))
                if isinstance(row.lead, Avatar):
                    row.lead.set_cover(path)
        if self.game and self.game.steam == steam_id:
            self._set_big_cover(path)

    # ------------------------------------------------------------ detail
    def _set_big_cover(self, path: str | None) -> None:
        pm = QPixmap(path) if path else QPixmap()
        if pm.isNull():
            self.cover.setPixmap(QPixmap())
            name = self.game.name if self.game else (self.custom_name or "?")
            words = [w for w in name.replace("-", " ").split() if w[:1].isalnum()]
            self.cover.setText("".join(w[0] for w in words[:2]).upper() or "?")
            self.cover.setFont(head_font(34, QFont.Weight.Bold))
            self.cover.setStyleSheet(
                "QLabel { color: #05060c; border-radius: 14px; background: qlineargradient("
                f"x1:0, y1:0, x2:1, y2:1, stop:0 {PALETTE['cyan']}, stop:1 {PALETTE['violet']}); }}")
            return
        self.cover.setText("")
        self.cover.setStyleSheet("QLabel { background: transparent; }")
        self.cover.setPixmap(rounded_cover(pm, 96, 136, self.devicePixelRatioF(), 14))

    def _show_game(self, game: Game) -> None:
        self.game = game
        self.custom_name = ""
        self.custom_name_edit.hide()
        self.title_lbl.show()
        self.title_lbl.set_full_text(game.name)
        cracked = sum(1 for loc in game.locations if loc.label.startswith(("Cracked", "Repack")))
        bits = [plural(len(game.locations), "known save location")]
        if cracked:
            bits.append(f"incl. {cracked} for cracked / repacked copies")
        if game.steam:
            bits.append(f"Steam app {game.steam}")
        self.meta_lbl.setText("  -  ".join(bits))
        self._set_big_cover(self.covers.get(game.steam))
        self.hint_lbl.setText(
            "Every known location of this game is checked: Steam, Epic, GOG, Ubisoft Connect "
            "and common cracked / repacked versions. If nothing is found yet, start the game, "
            "save once, then press Detect again.")
        self.skip_btn.hide()
        self._detect()

    def _show_custom(self, name: str) -> None:
        self.game = None
        self.custom_name = name
        self._loading = True
        self.game_list.setCurrentItem(None)
        self.game_list.clearSelection()
        self._loading = False
        self.title_lbl.hide()
        self.custom_name_edit.show()
        self.custom_name_edit.setText(name)
        self.custom_name_edit.setFocus()
        self.meta_lbl.setText("Not in the built-in list. Name it, then search the PC by name "
                              "or choose the save folder yourself.")
        self._set_big_cover(None)
        self.hint_lbl.setText(
            "Search PC by name looks for folders named after the game in Documents, Saved Games "
            "and AppData, and lists the ones holding files, newest first. Open a folder to check "
            "it really has the saves before using it.")
        self.skip_btn.show()
        self.candidates = []
        if name:
            self._search_by_name()
        else:
            self._fill_candidates()

    def _on_custom_name(self, text: str) -> None:
        self.custom_name = text.strip()
        self._set_big_cover(None)
        self._update_buttons()

    def _detect(self) -> None:
        if self.game is None:
            self._search_by_name()
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.candidates = detect(self.game)
        finally:
            QApplication.restoreOverrideCursor()
        self._fill_candidates()

    def _search_by_name(self) -> None:
        name = self.game.name if self.game else self.custom_name
        if not name:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            hits = search_by_name(name)
        finally:
            QApplication.restoreOverrideCursor()
        known = {os.path.normcase(c.path) for c in self.candidates}
        merged = [h for h in hits if os.path.normcase(h.path) not in known] + self.candidates
        found = sorted((c for c in merged if c.exists), key=lambda c: c.newest or datetime.min,
                       reverse=True)
        self.candidates = found + [c for c in merged if not c.exists]
        self._fill_candidates(searched=True)

    def _fill_candidates(self, searched: bool = False) -> None:
        self.cand_list.clear()
        found = [c for c in self.candidates if c.exists]
        shown = self.candidates if self.show_all.isChecked() else found
        for i, c in enumerate(shown):
            if c.exists:
                sub = (f"{c.path}   -   {plural(c.files, 'file')}, newest saved "
                       f"{humanize_age(c.newest)}")
            else:
                sub = f"{c.path}   -   not found on this PC"
            row = RowWidget(c.label, sub, title_font=head_font(14, QFont.Weight.DemiBold),
                            sub_mode=Qt.TextElideMode.ElideMiddle)
            if c.exists and c is found[0]:
                row.set_badge("NEWEST", "Badge")
            elif c.exists:
                row.set_badge("FOUND", "BadgeCyan")
            else:
                row.setEnabled(False)
            item = QListWidgetItem()
            item.setData(ROLE, self.candidates.index(c))
            item.setSizeHint(QSize(0, 56))
            self.cand_list.addItem(item)
            self.cand_list.setItemWidget(item, row)
        if found:
            self.cand_list.setCurrentRow(0)
            self.status_lbl.setText(f"✓  Found saves in {plural(len(found), 'location')}. "
                                    "The newest is selected.")
            self.status_lbl.setStyleSheet(f"color: {PALETTE['lime']};")
        else:
            if self.game is None and not searched:
                text = "Search the PC by name, or choose the folder yourself."
            elif self.game is None:
                text = ("No folder named after this game holds any files. Choose the folder "
                        "yourself, or add the game now and set the folder later.")
            else:
                text = ("No saves found in any known location yet. Start the game, save once and "
                        "press Detect again - or search by name / choose the folder yourself.")
            self.status_lbl.setText(text)
            self.status_lbl.setStyleSheet(f"color: {PALETTE['amber']};")
        hidden = len(self.candidates) - len(found)
        self.show_all.setText(f"Show {plural(hidden, 'location')} not found" if hidden
                              else "Show locations not found")
        self.show_all.setEnabled(hidden > 0)
        self._update_buttons()

    def _selected(self) -> Candidate | None:
        item = self.cand_list.currentItem()
        return self.candidates[item.data(ROLE)] if item is not None else None

    def _update_buttons(self) -> None:
        c = self._selected()
        ok = c is not None and os.path.isdir(c.path)
        self.use_btn.setEnabled(ok and bool(self.game or self.custom_name))
        self.open_btn.setEnabled(ok)
        self.detect_btn.setVisible(self.game is not None)
        self.skip_btn.setEnabled(bool(self.custom_name))

    # ----------------------------------------------------------- results
    def _open_selected(self) -> None:
        c = self._selected()
        if c and os.path.isdir(c.path):
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(c.path))

    def _finish(self, path: str) -> None:
        name = self.game.name if self.game else self.custom_name
        self.chosen = {"game": self.game, "name": name, "path": path}
        self.accept()

    def _use_selected(self) -> None:
        c = self._selected()
        if c and os.path.isdir(c.path) and (self.game or self.custom_name):
            self._finish(os.path.normpath(c.path))

    def _browse(self) -> None:
        start = self._selected().path if self._selected() else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select the game's save folder", start)
        if not path:
            return
        if self.game is None and not self.custom_name:
            from .config import guess_game_name

            self.custom_name = guess_game_name(path) or os.path.basename(path)
        self._finish(os.path.normpath(path))

    def _skip(self) -> None:
        if self.custom_name:
            self._finish("")
