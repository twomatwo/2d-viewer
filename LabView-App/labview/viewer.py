from __future__ import annotations

from dataclasses import replace
import math

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from .models import Annotation, Calibration
from .runtime import register_fonts


def rgb_image(rgb: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(rgb)
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                  QImage.Format.Format_RGB888).copy()


def measurement_cursor(device_pixel_ratio: float) -> QCursor:
    """Outlined screen-space crosshair; its open center is the exact hotspot."""
    pixmap = QPixmap(math.ceil(33 * device_pixel_ratio), math.ceil(33 * device_pixel_ratio))
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    arms = ((2, 16, 12, 16), (20, 16, 30, 16),
            (16, 2, 16, 12), (16, 20, 16, 30))
    for color, width in ((Qt.GlobalColor.black, 5), (Qt.GlobalColor.white, 2)):
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        for arm in arms:
            painter.drawLine(*arm)
    painter.end()
    return QCursor(pixmap, 16, 16)


def paint_annotations(painter: QPainter, annotations: list[Annotation], calibration: Calibration,
                      zoom: float = 1, selected: int = -1) -> None:
    zoom = max(zoom, 0.001)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont("Microsoft YaHei")
    font.setPixelSize(max(1, round(14 / zoom)))
    painter.setFont(font)
    for index, annotation in enumerate(annotations):
        label_font = QFont(font)
        label_font.setUnderline(index == selected and annotation.kind in ("marker", "text"))
        painter.setFont(label_font)
        points = [QPointF(*p) for p in annotation.points]
        if not points:
            continue
        pen = QPen(QColor(annotation.color), 2 / zoom)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        start = points[0]
        label = annotation.text
        if annotation.kind in ("line", "measure", "calibrate") and len(points) >= 2:
            painter.drawLine(start, points[-1])
            if annotation.kind in ("measure", "calibrate"):
                label = calibration.label(annotation.length_px)
            for point in (start, points[-1]):
                painter.drawEllipse(point, 3 / zoom, 3 / zoom)
        elif annotation.kind in ("rect", "ellipse") and len(points) >= 2:
            rect = QRectF(start, points[-1]).normalized()
            if annotation.kind == "rect":
                painter.drawRect(rect)
            else:
                painter.drawEllipse(rect)
        elif annotation.kind == "freehand":
            painter.drawPolyline(QPolygonF(points))
        elif annotation.kind == "marker":
            radius = 3 / zoom
            painter.setPen(QPen(QColor(annotation.color), 1 / zoom))
            painter.drawLine(start + QPointF(-radius, 0), start + QPointF(radius, 0))
            painter.drawLine(start + QPointF(0, -radius), start + QPointF(0, radius))
        if label:
            position = (start + points[-1]) / 2 if annotation.kind in ("measure", "calibrate") else start
            position += QPointF(7 / zoom, -7 / zoom)
            # A dark outline keeps labels legible on both bright and dark samples.
            painter.setPen(QPen(QColor("#171d28"), 1 / zoom))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(position + QPointF(dx / zoom, dy / zoom), label)
            painter.setPen(pen)
            painter.drawText(position, label)
        if index == selected and annotation.kind not in ("marker", "text"):
            painter.setPen(QPen(QColor("#ffffff"), 1 / zoom, Qt.PenStyle.DashLine))
            for point in (points[0], points[-1]):
                painter.drawRect(QRectF(point.x() - 5 / zoom, point.y() - 5 / zoom,
                                       10 / zoom, 10 / zoom))
    painter.restore()


UNIT_TO_UM = {"µm": 1.0, "μm": 1.0, "um": 1.0, "mm": 1000.0, "nm": 0.001}


def paint_scale_bar(painter: QPainter, size: tuple[int, int], calibration: Calibration,
                    length_um: float = 100.0, zoom: float = 1.0, show_hint: bool = False) -> None:
    if calibration.units_per_pixel <= 0:
        return
    unit_factor = UNIT_TO_UM.get(calibration.unit)
    if unit_factor is None:
        return
    width, height = size
    length = length_um / unit_factor
    pixels = length / calibration.units_per_pixel * zoom
    margin = max(20, width * 0.025)
    if pixels > width - 2 * margin or pixels < 1:
        if show_hint:
            painter.save()
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(margin, height - 40, width - 2 * margin, 30),
                             Qt.AlignmentFlag.AlignRight,
                             "比例尺过长，可缩短长度或缩小画面" if pixels >= 1 else "比例尺过短，可增大长度或放大画面")
            painter.restore()
        return
    start = QPointF(width - margin - pixels, height - margin)
    end = QPointF(width - margin, height - margin)
    painter.save()
    painter.setPen(QPen(QColor("#151b24"), 7))
    painter.drawLine(start, end)
    painter.setPen(QPen(QColor("#ffffff"), 3))
    painter.drawLine(start, end)
    font = QFont("Microsoft YaHei")
    font.setPixelSize(max(16, round(width / 90)))
    painter.setFont(font)
    painter.drawText(QRectF(start.x() - 20, start.y() - 45, pixels + 40, 35),
                     Qt.AlignmentFlag.AlignCenter, f"{length:g} {calibration.unit}")
    painter.restore()


class ImageViewer(QGraphicsView):
    annotations_changed = Signal()
    calibration_requested = Signal(float)
    calibration_cancelled = Signal()
    annotation_selected = Signal(int)
    text_requested = Signal(object)
    cursor_info = Signal(str)
    zoom_changed = Signal(float)
    frame_presented = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        register_fonts()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._image_item = QGraphicsPixmapItem()
        self._scene.addItem(self._image_item)
        self.setBackgroundBrush(QColor("#0c1018"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.annotations: list[Annotation] = []
        self.calibration = Calibration()
        self.scale_bar_length_um = 100.0
        self.selected = -1
        self.tool = "pan"
        self.color = "#ffd166"
        self.grid_visible = False
        self.crosshair_visible = False
        self._rgb: np.ndarray | None = None
        self._draft: Annotation | None = None
        self._history: list[list[Annotation]] = []
        self._pan_position = None
        self._fit = True
        self._presentation = None
        self._last_presentation = None
        self._cursor_position = None
        self._measurement_cursor = None
        self._measurement_cursor_ratio = None
        self.set_tool("pan")

    def set_frame(self, rgb: np.ndarray, presentation=None) -> None:
        changed = self._rgb is None or self._rgb.shape[:2] != rgb.shape[:2]
        self._rgb = rgb
        self._presentation = presentation
        # _rgb owns the buffer throughout synchronous conversion. Saving still
        # uses rgb_image()'s independent copy; preview needs no intermediate copy.
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888)
        self._image_item.setPixmap(QPixmap.fromImage(image))
        self._scene.setSceneRect(QRectF(0, 0, rgb.shape[1], rgb.shape[0]))
        if changed:
            self.fit_image()
        self._update_cursor_info()
        self.viewport().update()

    def _update_cursor_info(self) -> None:
        if self._rgb is None or self._cursor_position is None:
            return
        point = self.mapToScene(self._cursor_position)
        if not self.sceneRect().contains(point):
            self.cursor_info.emit("坐标 / 显示 RGB：鼠标不在图像内")
            return
        x = min(self._rgb.shape[1] - 1, max(0, int(point.x())))
        y = min(self._rgb.shape[0] - 1, max(0, int(point.y())))
        r, g, b = self._rgb[y, x]
        self.cursor_info.emit(f"x {x}   y {y}   ·   显示 RGB {r}, {g}, {b}")

    def paintEvent(self, event) -> None:
        presentation = self._presentation
        super().paintEvent(event)
        if presentation is not None and presentation is not self._last_presentation:
            self._last_presentation = presentation
            self.frame_presented.emit(presentation)

    def reset_document(self) -> None:
        self.annotations.clear()
        self._history.clear()
        self.calibration = Calibration()
        self.selected, self._draft = -1, None
        self._fit = True
        self.annotations_changed.emit()
        self.viewport().update()

    def set_tool(self, tool: str) -> None:
        self.tool, self._draft = tool, None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if tool == "pan"
                         else QGraphicsView.DragMode.NoDrag)
        self._update_tool_cursor()
        self.viewport().update()

    def _update_tool_cursor(self) -> None:
        if self.tool in ("measure", "calibrate"):
            ratio = self.devicePixelRatioF()
            if self._measurement_cursor_ratio != ratio:
                self._measurement_cursor = measurement_cursor(ratio)
                self._measurement_cursor_ratio = ratio
            self.setCursor(self._measurement_cursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.tool == "pan" else
                           Qt.CursorShape.ArrowCursor if self.tool == "select" else Qt.CursorShape.CrossCursor)

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, "_measurement_cursor_ratio"):
            self._update_tool_cursor()
        return result

    def fit_image(self) -> None:
        if self._rgb is not None:
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._fit = True
            self.zoom_changed.emit(self.transform().m11())

    def actual_size(self) -> None:
        if self._rgb is not None:
            self.set_zoom(1.0)

    def zoom_by(self, multiplier: float) -> None:
        if self._rgb is not None:
            self.set_zoom(min(32.0, max(0.02, self.transform().m11() * multiplier)))

    def set_zoom(self, scale: float) -> None:
        if not math.isfinite(scale) or not 0.02 <= scale <= 32:
            raise ValueError("缩放比例应为 2% 到 3200%")
        center = self.mapToScene(self.viewport().rect().center())
        self.setTransform(QTransform.fromScale(scale, scale))
        self.centerOn(center)
        self._fit = False
        self.zoom_changed.emit(scale)
        self.viewport().update()

    def set_scale_bar_length(self, length_um: float) -> None:
        if not math.isfinite(length_um) or length_um <= 0:
            raise ValueError("比例尺长度必须大于零")
        self.scale_bar_length_um = length_um
        self.viewport().update()

    def set_calibration(self, calibration: Calibration) -> None:
        self.calibration = calibration
        self.annotations_changed.emit()
        self.viewport().update()

    def add_annotation(self, annotation: Annotation) -> None:
        self._history.append(list(self.annotations))
        self.annotations.append(annotation)
        self.selected = len(self.annotations) - 1
        self.annotations_changed.emit()
        self.viewport().update()

    def select_annotation(self, index: int) -> None:
        selected = index if 0 <= index < len(self.annotations) else -1
        if selected != self.selected:
            self.selected = selected
            self.annotations_changed.emit()
        self.viewport().update()

    def delete_selected(self) -> None:
        if 0 <= self.selected < len(self.annotations):
            self._history.append(list(self.annotations))
            self.annotations.pop(self.selected)
            self.selected = -1
            self.annotations_changed.emit()
            self.viewport().update()

    def clear_annotations(self, kinds: set[str] | None = None) -> None:
        remaining = [] if kinds is None else [a for a in self.annotations if a.kind not in kinds]
        if remaining != self.annotations:
            self._history.append(list(self.annotations))
            self.annotations = remaining
            self.selected = -1
            self.annotations_changed.emit()
            self.viewport().update()

    def undo(self) -> None:
        if self._history:
            self.annotations = self._history.pop()
            self.selected = -1
            self.annotations_changed.emit()
            self.viewport().update()

    def _point(self, event) -> tuple[float, float] | None:
        if self._rgb is None:
            return None
        point = self.mapToScene(event.position().toPoint())
        return (float(np.clip(point.x(), 0, self._rgb.shape[1] - 1)),
                float(np.clip(point.y(), 0, self._rgb.shape[0] - 1)))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_position = event.position().toPoint()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or self.tool == "pan":
            super().mousePressEvent(event)
            return
        if self._rgb is None or not self.sceneRect().contains(self.mapToScene(event.position().toPoint())):
            return
        point = self._point(event)
        if self.tool == "select":
            tolerance = 15 / max(self.transform().m11(), 0.001)
            best, selected = float("inf"), -1
            for i, annotation in enumerate(self.annotations):
                points = annotation.points
                distance = min(math.dist(point, p) for p in points)
                for a, b in zip(points, points[1:]):
                    vec = np.array(b) - a
                    ratio = np.clip(np.dot(np.array(point) - a, vec) / max(np.dot(vec, vec), 1e-9), 0, 1)
                    distance = min(distance, float(np.linalg.norm(np.array(point) - (np.array(a) + ratio * vec))))
                if annotation.kind in ("rect", "ellipse") and QRectF(QPointF(*points[0]), QPointF(*points[-1])).normalized().contains(QPointF(*point)):
                    distance = 0
                if distance < min(best, tolerance):
                    best, selected = distance, i
            self.selected = selected
            self.annotations_changed.emit()
            self.annotation_selected.emit(selected)
        elif self.tool == "marker":
            numbers = [int(a.text.removeprefix("P")) for a in self.annotations
                       if a.kind == "marker" and a.text.removeprefix("P").isdigit()]
            self.add_annotation(Annotation("marker", (point,), self.color,
                                           str(max(numbers, default=0) + 1)))
        elif self.tool == "text":
            self.text_requested.emit(point)
        else:
            self._draft = Annotation(self.tool, (point, point), self.color)
        self.viewport().update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        self._cursor_position = event.position().toPoint()
        if self._pan_position is not None:
            current = event.position().toPoint()
            delta = current - self._pan_position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_position = current
            return
        point = self._point(event)
        self._update_cursor_info()
        if self._draft is not None and point is not None:
            if self.tool == "freehand":
                if math.dist(self._draft.points[-1], point) > 2 / max(self.transform().m11(), 0.001):
                    self._draft = replace(self._draft, points=self._draft.points + (point,))
            else:
                self._draft = replace(self._draft, points=(self._draft.points[0], point))
            self.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_position = None
            return
        if event.button() == Qt.MouseButton.LeftButton and self._draft is not None:
            draft, self._draft = self._draft, None
            point = self._point(event)
            if point is not None and draft.kind != "freehand":
                draft = replace(draft, points=(draft.points[0], point))
            substantial = (len(draft.points) > 2 and any(math.dist(draft.points[0], p) >= 1 for p in draft.points)) if draft.kind == "freehand" else draft.length_px >= 1
            if substantial:
                if draft.kind == "calibrate":
                    self.calibration_requested.emit(draft.length_px)
                else:
                    self.add_annotation(draft)
            self.viewport().update()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if self._rgb is None:
            return
        multiplier = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        zoom = self.transform().m11() * multiplier
        if 0.02 <= zoom <= 32:
            self.scale(multiplier, multiplier)
            self._fit = False
            self.zoom_changed.emit(zoom)
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit:
            self.fit_image()

    def cancel_drawing(self) -> None:
        self._draft = None
        if self.tool == "calibrate":
            self.calibration_cancelled.emit()
        self.viewport().update()

    def drawForeground(self, painter, rect) -> None:
        super().drawForeground(painter, rect)
        painter.save()
        painter.setClipRect(self.sceneRect())
        if self._rgb is not None and (self.grid_visible or self.crosshair_visible):
            width, height = self._rgb.shape[1], self._rgb.shape[0]
            painter.setPen(QPen(QColor(210, 220, 240, 100), 1 / max(self.transform().m11(), 0.001)))
            if self.grid_visible:
                for portion in (0.25, 0.5, 0.75):
                    painter.drawLine(QPointF(width * portion, 0), QPointF(width * portion, height))
                    painter.drawLine(QPointF(0, height * portion), QPointF(width, height * portion))
            if self.crosshair_visible:
                painter.drawLine(QPointF(width / 2 - 30, height / 2), QPointF(width / 2 + 30, height / 2))
                painter.drawLine(QPointF(width / 2, height / 2 - 30), QPointF(width / 2, height / 2 + 30))
        annotations = self.annotations + ([self._draft] if self._draft else [])
        paint_annotations(painter, annotations, self.calibration, self.transform().m11(), self.selected)
        if self._rgb is not None:
            # Keep the ruler in the visible image corner when the image is
            # zoomed or panned. Its width still follows image pixels and zoom.
            visible = self.mapFromScene(self.sceneRect()).boundingRect().intersected(self.viewport().rect())
            painter.save()
            painter.resetTransform()
            painter.translate(visible.topLeft())
            paint_scale_bar(painter, (visible.width(), visible.height()), self.calibration,
                            self.scale_bar_length_um, self.transform().m11(), show_hint=True)
            painter.restore()
        painter.restore()

    def exported_image(self, include_annotations: bool = True) -> QImage:
        if self._rgb is None:
            raise ValueError("当前没有图像")
        output = rgb_image(self._rgb)
        if include_annotations:
            painter = QPainter(output)
            paint_annotations(painter, self.annotations, self.calibration)
            paint_scale_bar(painter, (output.width(), output.height()), self.calibration, self.scale_bar_length_um)
            painter.end()
        return output
