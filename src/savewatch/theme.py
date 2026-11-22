"""Neon-on-glass look for SaveWatch.  Plain data, no Qt import.

The window paints its own dark backdrop with soft neon light blobs
(`app.Backdrop`); every panel on top of it is a translucent "glass" card with
a thin bright edge, so the glow shows through.
"""

PALETTE = {
    "bg_top": "#070814",
    "bg_bottom": "#0d0a22",
    "panel": "#0d0e1c",          # opaque fallback (dialogs, menus)
    "text": "#e9ecff",
    "muted": "#8a90b4",
    "dim": "#565b80",
    "cyan": "#00e5ff",
    "magenta": "#ff3df0",
    "violet": "#8b5cf6",
    "lime": "#3dffa2",
    "amber": "#ffc857",
    "red": "#ff4d6d",
}

# Avatar gradients for game profiles, picked by name.
AVATAR_GRADIENTS = [
    ("#00e5ff", "#8b5cf6"),
    ("#ff3df0", "#8b5cf6"),
    ("#3dffa2", "#00b3ff"),
    ("#ffc857", "#ff3df0"),
    ("#ff4d6d", "#ff9f43"),
    ("#8b5cf6", "#00e5ff"),
]

FONT_FAMILY = "Segoe UI"
HEAD_FAMILIES = ["Bahnschrift", "Segoe UI", "Arial"]
MONO_FAMILY = "Consolas, 'Cascadia Mono', monospace"

P = PALETTE
GLASS = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
         "stop:0 rgba(255,255,255,17), stop:1 rgba(255,255,255,6))")

# No font sizes on generic selectors: they would override fonts set in code.
STYLESHEET = f"""
QWidget {{ color: {P['text']}; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QStackedWidget, QStackedWidget > QWidget {{ background: transparent; }}

#Sidebar, #Card {{
    background: {GLASS};
    border: 1px solid rgba(255,255,255,26);
    border-radius: 18px;
}}
#Sidebar {{ border-color: rgba(0,229,255,40); }}
#Divider {{ background: rgba(255,255,255,22); max-height: 1px; min-height: 1px; border: none; }}

#Muted {{ color: {P['muted']}; }}
#Dim {{ color: {P['dim']}; }}
#Section {{ color: {P['muted']}; }}
#Accent {{ color: {P['cyan']}; }}
#Hint {{ color: {P['muted']}; font-size: 12px; }}

QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: rgba(3,4,14,165);
    border: 1px solid rgba(255,255,255,32);
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 14px;
    selection-background-color: rgba(0,229,255,110);
    selection-color: #ffffff;
}}
QSpinBox, QDoubleSpinBox {{ padding: 8px 6px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow {{ width: 0; height: 0; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: rgba(0,229,255,90); }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {P['cyan']};
    background: rgba(0,229,255,14);
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {P['dim']}; border-color: rgba(255,255,255,14);
}}

QPushButton {{
    background: rgba(255,255,255,14);
    border: 1px solid rgba(255,255,255,40);
    border-radius: 11px;
    padding: 9px 18px;
    font-size: 14px;
    font-weight: 600;
    color: {P['text']};
}}
QPushButton:hover {{ border-color: {P['cyan']}; background: rgba(0,229,255,26); color: #ffffff; }}
QPushButton:pressed {{ background: rgba(0,229,255,45); }}
QPushButton:disabled {{
    background: rgba(255,255,255,5); color: {P['dim']}; border-color: rgba(255,255,255,14);
}}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {P['cyan']}, stop:1 {P['violet']});
    border: 1px solid rgba(255,255,255,90);
    color: #05060c;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6af2ff, stop:1 #a78bfa);
}}
QPushButton#Restore {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {P['magenta']}, stop:1 {P['violet']});
    border: 1px solid rgba(255,255,255,90);
    color: #ffffff;
}}
QPushButton#Restore:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff7af5, stop:1 #a78bfa);
}}
QPushButton#Primary:disabled, QPushButton#Restore:disabled {{
    background: rgba(255,255,255,6); color: {P['dim']}; border-color: rgba(255,255,255,16);
}}
QPushButton#Danger {{
    background: rgba(255,77,109,26); border: 1px solid rgba(255,77,109,170); color: #ff9db0;
}}
QPushButton#Danger:hover {{ background: rgba(255,77,109,60); color: #ffffff; }}
QPushButton#Danger:disabled {{
    background: rgba(255,255,255,5); color: {P['dim']}; border-color: rgba(255,255,255,14);
}}
QPushButton#Small {{ padding: 6px 12px; font-size: 13px; border-radius: 9px; }}
QPushButton#Step {{ padding: 0; font-size: 20px; font-weight: 500; border-radius: 10px; }}

QPushButton#Tab {{
    background: transparent; border: none; border-radius: 0;
    border-bottom: 2px solid transparent;
    padding: 8px 2px; margin-right: 22px;
    color: {P['muted']}; font-size: 14px; font-weight: 700;
}}
QPushButton#Tab:hover {{ color: {P['text']}; background: transparent; }}
QPushButton#Tab:checked {{ color: {P['cyan']}; border-bottom: 2px solid {P['cyan']}; }}

QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{
    border: 1px solid transparent; border-radius: 12px; margin: 2px 0; padding: 0;
}}
QListWidget::item:hover {{ background: rgba(255,255,255,10); }}
QListWidget::item:selected {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(0,229,255,38), stop:1 rgba(139,92,246,22));
    border: 1px solid rgba(0,229,255,150);
}}
QListWidget:disabled::item:selected {{ border-color: rgba(61,255,162,150); }}

QPlainTextEdit#Log {{
    background: rgba(2,3,10,175);
    border: 1px solid rgba(255,255,255,20);
    border-radius: 14px;
    padding: 8px 10px;
    font-family: {MONO_FAMILY};
    font-size: 13px;
    selection-background-color: rgba(0,229,255,110);
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 6px 2px; }}
QScrollBar::handle:vertical {{ background: rgba(0,229,255,80); border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(0,229,255,150); }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 6px; }}
QScrollBar::handle:horizontal {{ background: rgba(0,229,255,80); border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QCheckBox {{ color: {P['text']}; spacing: 10px; font-size: 14px; }}
QCheckBox:disabled {{ color: {P['dim']}; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 6px;
    border: 1px solid rgba(255,255,255,70); background: rgba(0,0,0,90);
}}
QCheckBox::indicator:hover {{ border-color: {P['cyan']}; }}
QCheckBox::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {P['cyan']}, stop:1 {P['violet']});
    border: 1px solid rgba(255,255,255,140);
}}
QCheckBox::indicator:disabled {{ border-color: rgba(255,255,255,20); background: rgba(255,255,255,5); }}

#Pill {{
    border-radius: 14px; padding: 6px 16px; font-size: 13px; font-weight: 700;
    color: {P['muted']}; background: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,40);
}}
#Pill[state="watching"] {{
    color: {P['lime']}; background: rgba(61,255,162,22); border-color: rgba(61,255,162,170);
}}
#Pill[state="busy"] {{
    color: {P['cyan']}; background: rgba(0,229,255,22); border-color: rgba(0,229,255,170);
}}
#Pill[state="error"] {{
    color: {P['red']}; background: rgba(255,77,109,22); border-color: rgba(255,77,109,170);
}}
#Chip {{
    color: {P['muted']}; background: rgba(255,255,255,9);
    border: 1px solid rgba(255,255,255,22); border-radius: 10px;
    padding: 4px 11px; font-size: 12px;
}}
#Badge {{
    color: #05060c; background: {P['lime']}; border-radius: 6px;
    padding: 1px 7px; font-size: 10px; font-weight: 800;
}}
#BadgeCyan {{
    color: #05060c; background: {P['cyan']}; border-radius: 6px;
    padding: 1px 7px; font-size: 10px; font-weight: 800;
}}
#BadgeMagenta {{
    color: #ffffff; background: {P['magenta']}; border-radius: 6px;
    padding: 1px 7px; font-size: 10px; font-weight: 800;
}}
#AgePill {{
    color: {P['magenta']}; background: rgba(255,61,240,20);
    border: 1px solid rgba(255,61,240,140); border-radius: 11px;
    padding: 3px 12px; font-size: 13px; font-weight: 700;
}}

QMessageBox, QDialog, QInputDialog {{ background: {P['panel']}; }}
QMessageBox QLabel {{ color: {P['text']}; font-size: 14px; }}
QMessageBox QPushButton {{ min-width: 90px; }}
QMenu {{
    background: {P['panel']}; border: 1px solid rgba(0,229,255,110);
    border-radius: 10px; padding: 6px;
}}
QMenu::item {{ padding: 7px 26px; border-radius: 7px; color: {P['text']}; }}
QMenu::item:selected {{ background: rgba(0,229,255,40); }}
QMenu::item:disabled {{ color: {P['dim']}; }}
QMenu::separator {{ height: 1px; background: rgba(255,255,255,30); margin: 5px 8px; }}
QToolTip {{
    background: {P['panel']}; color: {P['text']};
    border: 1px solid rgba(0,229,255,140); padding: 6px; border-radius: 6px;
}}
"""

LEVEL_COLOR = {
    "ok": "#3dffa2",
    "info": "#9aa6d6",
    "warn": "#ffc857",
    "alert": "#ff9f43",
    "error": "#ff4d6d",
    "prune": "#6c7299",
}
STAMP_COLOR = "#4b5178"
