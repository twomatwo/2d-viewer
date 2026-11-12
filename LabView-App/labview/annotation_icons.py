"""Small Qt-drawn line icons for annotation controls, including high-DPI states."""
from functools import lru_cache

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def _draw(painter, name):
    def lines(*segments):
        for segment in segments:
            painter.drawLine(QLineF(*segment))

    def polygon(*points):
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in points]))

    if name == "select":
        polygon((5, 3), (5, 19), (9, 15), (12, 21), (15, 19), (12, 13), (19, 12))
    elif name == "rect":
        painter.drawRect(QRectF(4, 5, 16, 14))
    elif name == "ellipse":
        painter.drawEllipse(QRectF(3, 5, 18, 14))
    elif name == "line":
        lines((5, 19, 19, 5))
        painter.drawEllipse(QRectF(3, 17, 4, 4))
        painter.drawEllipse(QRectF(17, 3, 4, 4))
    elif name == "measure":
        lines((4, 16, 20, 16), (4, 12, 4, 20), (20, 12, 20, 20),
              (8, 5, 8, 9), (12, 5, 12, 11), (16, 5, 16, 9), (4, 5, 20, 5))
    elif name == "angle":
        lines((5, 4, 5, 20), (5, 20, 21, 14))
        painter.drawArc(QRectF(-3, 12, 16, 16), 20 * 16, 70 * 16)
    elif name == "polygon_shape":
        polygon((4, 18), (6, 5), (16, 3), (21, 14), (13, 21))
    elif name == "polygon":
        polygon((4, 18), (6, 5), (16, 3), (21, 14), (13, 21))
        lines((8, 15, 15, 8), (11, 18, 18, 11))
    elif name == "marker":
        painter.drawEllipse(QRectF(7, 7, 10, 10))
        lines((12, 3, 12, 8), (12, 16, 12, 21), (3, 12, 8, 12), (16, 12, 21, 12))
    elif name == "text":
        lines((5, 5, 19, 5), (12, 5, 12, 20), (8, 20, 16, 20), (5, 5, 5, 8), (19, 5, 19, 8))
    elif name == "freehand":
        path = QPainterPath(QPointF(3, 17))
        path.cubicTo(4, 4, 10, 4, 10, 13)
        path.cubicTo(10, 24, 16, 20, 21, 7)
        painter.drawPath(path)
    elif name == "image":
        painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
        painter.drawEllipse(QRectF(6, 7, 4, 4))
        lines((4, 18, 11, 12), (11, 12, 15, 16), (15, 16, 18, 13), (18, 13, 21, 16))
    elif name in ("undo", "redo"):
        if name == "redo":
            painter.translate(24, 0)
            painter.scale(-1, 1)
        lines((4, 5, 4, 11), (4, 11, 10, 11))
        path = QPainterPath(QPointF(4, 11))
        path.cubicTo(11, 1, 24, 9, 19, 18)
        painter.drawPath(path)
    elif name in ("group", "ungroup"):
        painter.drawRect(QRectF(4, 4, 10, 10))
        painter.drawRect(QRectF(10, 10, 10, 10))
        if name == "group":
            pen = painter.pen()
            dashed = QPen(pen)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.drawRect(QRectF(1, 1, 22, 22))
        else:
            lines((3, 21, 21, 3))
    elif name == "delete":
        lines((4, 6, 20, 6), (9, 3, 15, 3), (9, 10, 9, 18), (15, 10, 15, 18),
              (6, 6, 7, 21), (7, 21, 17, 21), (17, 21, 18, 6))
    elif name == "clear":
        polygon((4, 14), (13, 4), (21, 11), (12, 21), (10, 21))
        lines((8, 10, 16, 17), (12, 21, 21, 21))
    elif name == "export":
        lines((12, 3, 12, 15), (7, 10, 12, 15), (12, 15, 17, 10),
              (4, 16, 4, 21), (4, 21, 20, 21), (20, 21, 20, 16))
    elif name == "csv":
        painter.drawRect(QRectF(4, 4, 16, 16))
        lines((4, 9, 20, 9), (4, 14, 20, 14), (10, 4, 10, 20))
    else:
        raise ValueError(f"Unknown annotation icon: {name}")


@lru_cache(maxsize=32)
def annotation_icon(name):
    icon = QIcon()
    for mode, state, color in ((QIcon.Mode.Normal, QIcon.State.Off, "#c5d2e5"),
                               (QIcon.Mode.Normal, QIcon.State.On, "#a6edda"),
                               (QIcon.Mode.Disabled, QIcon.State.Off, "#65738b"),
                               (QIcon.Mode.Disabled, QIcon.State.On, "#65738b")):
        for size in (18, 24, 36, 48, 54, 72):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.scale(size / 24, size / 24)
                painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                _draw(painter, name)
            finally:
                painter.end()
            icon.addPixmap(pixmap, mode, state)
    return icon
