# PyInstaller spec: builds dist/SaveWatch.exe (one file, no console).
#   pyinstaller savewatch.spec
import os

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.dirname(SPEC))
SRC = os.path.join(ROOT, "src")
ASSETS = os.path.join(SRC, "savewatch", "assets")

a = Analysis(
    [os.path.join(ROOT, "launcher.py")],
    pathex=[SRC],
    datas=[(os.path.join(ASSETS, "icon.ico"), "."), (os.path.join(ASSETS, "icon.png"), ".")],
    hiddenimports=["savewatch.app", "savewatch.cli"],
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
        "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtNetwork",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="SaveWatch",
    icon=os.path.join(ASSETS, "icon.ico"),
    console=False,
    upx=False,
    bootloader_ignore_signals=False,
)
