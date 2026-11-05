from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class HistogramWidget(QWidget):
    levels_changed = Signal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.hist = np.zeros((3, 256))
        self.channel = -1
        self.overall = np.zeros(256)
        self.mode = "overall"
        self.black, self.white = 0.0, 100.0
        self.logarithmic = True
        self.editable = True
        self._dragging = None
        self.setToolTip("拖动左右虚线设置黑点和白点；RGB 通道可分别调节。横轴为输入满量程的百分比。")

    def configure(self, counts: np.ndarray, channel: int, black: float, white: float,
                  editable: bool = True, overall: np.ndarray | None = None, mode: str = "rgb") -> None:
        self.hist, self.channel = counts, channel
        self.black, self.white, self.editable = black, white, editable
        self.overall = overall if overall is not None else counts.sum(axis=0)
        self.mode = mode
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(14, 12, max(1, self.width() - 28), max(1, self.height() - 38))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        painter.fillRect(self.rect(), QColor("#121925"))
        painter.setPen(QPen(QColor("#283348"), 1))
        for portion in (0, 0.25, 0.5, 0.75, 1):
            x = plot.left() + plot.width() * portion
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        if self.mode == "overall":
            series = [(self.overall, "#d8e1ef")]
        else:
            series = [(self.hist[c], color) for c, color in enumerate(("#ff6d78", "#65d8a2", "#6eacff"))
                      if self.mode == "rgb" or self.mode == str(c)]
        maximum = max(1, max((np.log1p(values) if self.logarithmic else values).max() for values, _ in series))
        for values, color in series:
            counts = np.log1p(values) if self.logarithmic else values
            path = QPainterPath()
            for i, count in enumerate(counts):
                point = QPointF(plot.left() + i / 255 * plot.width(),
                                plot.bottom() - count / maximum * plot.height())
                if i == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            qcolor = QColor(color)
            qcolor.setAlpha(240)
            painter.setPen(QPen(qcolor, 1.5))
            painter.drawPath(path)
        if self.editable:
            for value, color in ((self.black, "#ffd166"), (self.white, "#f4f7ff")):
                x = plot.left() + value / 100 * plot.width()
                painter.setPen(QPen(QColor(color), 2, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
                painter.drawEllipse(QPointF(x, plot.bottom()), 4, 4)
        painter.setPen(QColor("#92a1b8"))
        painter.drawText(QRectF(plot.left(), plot.bottom() + 5, plot.width(), 20),
                         Qt.AlignmentFlag.AlignLeft, "0%")
        painter.drawText(QRectF(plot.left(), plot.bottom() + 5, plot.width(), 20),
                         Qt.AlignmentFlag.AlignRight, "100%")
        painter.end()

    def mousePressEvent(self, event) -> None:
        if not self.editable or event.button() != Qt.MouseButton.LeftButton:
            return
        value = np.clip((event.position().x() - self._plot().left()) / self._plot().width() * 100, 0, 100)
        self._dragging = "black" if abs(value - self.black) <= abs(value - self.white) else "white"
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is None:
            return
        value = round(float(np.clip((event.position().x() - self._plot().left()) / self._plot().width() * 100, 0, 100)), 2)
        if self._dragging == "black":
            self.black = min(value, self.white - 0.1)
        else:
            self.white = max(value, self.black + 0.1)
        self.levels_changed.emit(self.black, self.white)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = None
