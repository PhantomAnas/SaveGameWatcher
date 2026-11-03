"""Shared dark look used across the app set.  Plain data, no dependency."""

PALETTE = {
    "bg": "#14161b",
    "card": "#1c1f27",
    "border": "#2a2f3a",
    "input": "#10131a",
    "text": "#e6e8ee",
    "muted": "#8b93a7",
    "accent": "#3b82f6",
    "ok": "#1f7a4d",
    "ok_hover": "#24915b",
    "danger": "#8a2c2c",
    "danger_hover": "#a33636",
}

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas, 'Cascadia Mono', monospace"

STYLESHEET = f"""
QWidget {{ background: {PALETTE['bg']}; color: {PALETTE['text']}; font-size: 14px; }}
QLabel, QCheckBox {{ background: transparent; }}
#Card {{ background: {PALETTE['card']}; border: 1px solid {PALETTE['border']}; border-radius: 12px; }}
#Title {{ font-size: 26px; font-weight: 600; color: #ffffff; }}
#Sub {{ color: {PALETTE['muted']}; font-size: 13px; }}
QLabel#Field {{ color: #9aa3b8; font-size: 13px; }}
QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {PALETTE['input']}; border: 1px solid #2f3542; border-radius: 8px;
    padding: 8px 10px; selection-background-color: {PALETTE['accent']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {PALETTE['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: #6b7280; }}
QPushButton {{
    background: #262b36; border: 1px solid #343b49; border-radius: 8px;
    padding: 8px 18px; font-weight: 500;
}}
QPushButton:hover {{ background: #2f3542; }}
QPushButton:disabled {{ background: #1a1d24; color: #5a6172; border-color: #23272f; }}
QPushButton#Start {{ background: {PALETTE['ok']}; border-color: {PALETTE['ok_hover']}; color: #ffffff; }}
QPushButton#Start:hover {{ background: {PALETTE['ok_hover']}; }}
QPushButton#Stop {{ background: {PALETTE['danger']}; border-color: {PALETTE['danger_hover']}; color: #ffffff; }}
QPushButton#Stop:hover {{ background: {PALETTE['danger_hover']}; }}
QPlainTextEdit {{
    background: #0d1016; border: 1px solid {PALETTE['border']}; border-radius: 10px;
    padding: 8px; font-family: {MONO_FAMILY}; font-size: 13px;
}}
QCheckBox {{ color: #9aa3b8; }}
QCheckBox:disabled {{ color: #5a6172; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid #3a4150; background: {PALETTE['input']};
}}
QCheckBox::indicator:checked {{ background: {PALETTE['accent']}; border-color: {PALETTE['accent']}; }}
QCheckBox::indicator:disabled {{ border-color: #2a2f3a; background: #1a1d24; }}
QMenu {{ background: {PALETTE['card']}; border: 1px solid {PALETTE['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2f3542; }}
QMenu::item:disabled {{ color: #5a6172; }}
"""

LEVEL_COLOR = {
    "ok": "#4ade80",
    "info": "#93a4c0",
    "warn": "#fbbf24",
    "alert": "#fb923c",
    "error": "#f87171",
    "prune": "#7d8699",
}
