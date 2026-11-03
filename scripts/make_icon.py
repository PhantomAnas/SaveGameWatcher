"""Generate the app icon (PNG + ICO) with Qt only.  Run once:

    python scripts/make_icon.py

A rounded dark tile with a green shield-and-clock mark: "saves, versioned".
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "savewatch", "assets")


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size

    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
    p.fillPath(tile, QColor("#1c1f27"))

    shield = QPainterPath()
    shield.moveTo(s * 0.50, s * 0.14)
    shield.lineTo(s * 0.80, s * 0.26)
    shield.cubicTo(s * 0.80, s * 0.58, s * 0.68, s * 0.78, s * 0.50, s * 0.88)
    shield.cubicTo(s * 0.32, s * 0.78, s * 0.20, s * 0.58, s * 0.20, s * 0.26)
    shield.closeSubpath()
    p.fillPath(shield, QColor("#24915b"))

    p.setPen(QPen(QColor("#ffffff"), max(1.0, s * 0.055), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    c = QPointF(s * 0.50, s * 0.50)
    r = s * 0.17
    p.drawEllipse(c, r, r)
    p.drawLine(c, QPointF(s * 0.50, s * 0.50 - r * 0.62))
    p.drawLine(c, QPointF(s * 0.50 + r * 0.55, s * 0.50 + r * 0.30))
    p.end()
    return img


def main() -> None:
    QGuiApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)
    draw(256).save(os.path.join(OUT, "icon.png"))
    draw(256).save(os.path.join(OUT, "icon.ico"), "ICO")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
