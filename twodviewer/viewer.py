from __future__ import annotations

from dataclasses import replace
from uuid import uuid4
import math

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from .models import Annotation, Calibration
from .annotation_geometry import (annotation_path, annotation_bounds, hit_annotation,
                                  mapped_points, label_text, label_position, label_bounds)
from .runtime import register_fonts
from .image_assets import decode_image_asset


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
    selected_indices = {selected} if isinstance(selected, int) else set(selected)
    for index, annotation in enumerate(annotations):
        if not annotation.points:
            continue
        if annotation.kind == "image":
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setOpacity(painter.opacity() * annotation.image_opacity)
            painter.setTransform(QTransform(*annotation.transform), True)
            painter.drawImage(QRectF(QPointF(*annotation.points[0]), QPointF(*annotation.points[-1])).normalized(),
                              decode_image_asset(annotation.image_data))
            painter.restore()
            continue
        pen = QPen(QColor(annotation.color), 2 / zoom)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if annotation.kind != "text":
            painter.drawPath(annotation_path(annotation, zoom))
        if annotation.kind in ("measure", "calibrate", "angle"):
            points = mapped_points(annotation)
            for point in (points if annotation.kind == "angle" else (points[0], points[-1])):
                painter.drawEllipse(point, 3 / zoom, 3 / zoom)
        label = label_text(annotation, calibration)
        if label:
            position = label_position(annotation, zoom)
            label_font = QFont(font)
            label_font.setUnderline(index in selected_indices and annotation.kind in ("marker", "text"))
            painter.setFont(label_font)
            painter.setPen(QPen(QColor("#171d28"), 1 / zoom))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(position + QPointF(dx / zoom, dy / zoom), label)
            painter.setPen(pen)
            painter.drawText(position, label)
    painter.restore()


def annotation_group_image(annotations: list[Annotation], calibration: Calibration) -> QImage:
    """Render annotations alone at image-pixel scale, cropped with alpha padding."""
    bounds = None
    for a in annotations:
        if not a.points:
            continue
        rect = annotation_bounds(a, calibration)
        label = label_bounds(a, calibration)
        if not label.isEmpty():
            rect = rect.united(label)
        bounds = rect if bounds is None else bounds.united(rect)
    if bounds is None:
        raise ValueError("组合中没有可导出的标注")
    # Check floating bounds before converting to Qt's 32-bit integer rectangle.
    if (not all(math.isfinite(v) and abs(v) < 2_000_000_000 for v in
                (bounds.left(), bounds.top(), bounds.right(), bounds.bottom())) or
            max(bounds.width(), bounds.height()) > 32757 or
            (bounds.width() + 10) * (bounds.height() + 10) > 20_000_000):
        raise ValueError("组合导出范围过大，请缩小标注后重试")
    bounds = bounds.adjusted(-5, -5, 5, 5).toAlignedRect()
    width, height = bounds.width(), bounds.height()
    if width <= 0 or height <= 0 or max(width, height) > 32767 or width * height > 20_000_000:
        raise ValueError("组合导出范围过大（最多 2000 万像素，单边最多 32767 像素），请缩小标注后重试")
    output = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    if output.isNull():
        raise ValueError("无法为组合导出分配图像内存")
    output.fill(Qt.GlobalColor.transparent)
    painter = QPainter(output)
    try:
        painter.translate(-bounds.left(), -bounds.top())
        paint_annotations(painter, annotations, calibration)
    finally:
        painter.end()
    return output


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
    document_changed = Signal()
    selection_changed = Signal()
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
        self.objective_name = ""
        self.objective_id = ""
        self.magnification = None
        self.measurement_enabled = True
        self.scale_bar_length_um = 100.0
        self.selection: set[int] = set()
        self._gesture = None
        self._marquee = None
        self.tool = "pan"
        self.color = "#ffd166"
        self.grid_visible = False
        self.crosshair_visible = False
        self._rgb: np.ndarray | None = None
        self._draft: Annotation | None = None
        self._draft_cursor = None
        self._history: list[list[Annotation]] = []
        self._redo_history: list[list[Annotation]] = []
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
        self._cancel_edit()
        self.annotations.clear()
        self._history.clear()
        self._redo_history.clear()
        self.calibration = Calibration()
        self.selected, self._draft = -1, None
        self._fit = True
        self.annotations_changed.emit()
        self.viewport().update()

    def set_tool(self, tool: str) -> None:
        self._cancel_edit()
        self.tool, self._draft = tool, None
        self._draft_cursor = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if tool == "pan"
                         else QGraphicsView.DragMode.NoDrag)
        self._update_tool_cursor()
        self.viewport().update()

    def _update_tool_cursor(self) -> None:
        if self.tool in ("measure", "calibrate", "angle", "polygon"):
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
        self.document_changed.emit()
        self.viewport().update()

    def _measurement_snapshot(self, annotation: Annotation) -> Annotation:
        if annotation.kind in ("measure", "polygon", "calibrate", "angle") and annotation.calibration is None:
            return replace(annotation, calibration=self.calibration,
                           objective_name=self.objective_name, objective_id=self.objective_id,
                           magnification=self.magnification)
        return annotation

    def add_annotation(self, annotation: Annotation) -> None:
        if annotation.kind == "angle" and annotation.angle_degrees is None:
            return
        annotation = self._measurement_snapshot(annotation)
        if annotation.kind == "polygon" and annotation.mean_rgb is None and self._rgb is not None:
            from .region_measurement import region_rgb
            mean, count = region_rgb(self._rgb, mapped_points(annotation))
            annotation = replace(annotation, mean_rgb=mean, rgb_pixel_count=count)
        self._checkpoint()
        self.annotations.append(annotation)
        self.selected = len(self.annotations) - 1
        self.annotations_changed.emit()
        self.document_changed.emit()
        self.viewport().update()

    @property
    def selected(self):
        return min(self.selection, default=-1)

    @selected.setter
    def selected(self, index):
        self.selection = {index} if index >= 0 else set()

    def _expanded_selection(self, indices):
        indices = {i for i in indices if 0 <= i < len(self.annotations)}
        groups = {self.annotations[i].group_id for i in indices if self.annotations[i].group_id}
        return indices | {i for i, a in enumerate(self.annotations) if a.group_id and a.group_id in groups}

    def select_annotations(self, indices):
        selected = self._expanded_selection(indices)
        if selected != self.selection:
            self.selection = selected
            self.selection_changed.emit()
        self.viewport().update()

    def select_annotation(self, index: int) -> None:
        self.select_annotations({index})

    def _document_edited(self):
        self.annotations_changed.emit()
        self.selection_changed.emit()
        self.document_changed.emit()
        self.viewport().update()

    def delete_selected(self) -> None:
        if self.selection:
            self._cancel_edit()
            self._checkpoint()
            self.annotations = [a for i, a in enumerate(self.annotations) if i not in self.selection]
            self.selection.clear()
            self._document_edited()

    def can_group(self):
        roots = {a.group_id or f"item:{i}" for i, a in enumerate(self.annotations) if i in self.selection}
        return len(roots) > 1

    def group_selected(self):
        if self.can_group():
            self._cancel_edit()
            self._checkpoint()
            group = uuid4().hex
            self.annotations = [replace(a, group_id=group) if i in self.selection else a
                                for i, a in enumerate(self.annotations)]
            self._document_edited()

    def ungroup_selected(self):
        if any(self.annotations[i].group_id for i in self.selection):
            self._cancel_edit()
            self._checkpoint()
            self.annotations = [replace(a, group_id="") if i in self.selection else a
                                for i, a in enumerate(self.annotations)]
            self._document_edited()

    def set_selection_color(self, color):
        if any(self.annotations[i].kind != "image" and self.annotations[i].color != color for i in self.selection):
            self._cancel_edit()
            self._checkpoint()
            self.annotations = [replace(a, color=color) if i in self.selection and a.kind != "image" else a
                                for i, a in enumerate(self.annotations)]
            self._document_edited()

    def set_image_opacity(self, opacity):
        if not math.isfinite(opacity) or not 0 <= opacity <= 1:
            raise ValueError("图片不透明度必须在 0% 到 100% 之间")
        indices = {i for i in self.selection if self.annotations[i].kind == "image"}
        if any(self.annotations[i].image_opacity != opacity for i in indices):
            self._cancel_edit()
            self._checkpoint()
            self.annotations = [replace(a, image_opacity=opacity) if i in indices else a
                                for i, a in enumerate(self.annotations)]
            self._document_edited()

    def selection_transform_values(self):
        """Absolute angle and scale of the first selected object's local axes."""
        if not self.selection:
            return None
        a = self.annotations[min(self.selection)]
        return ((math.degrees(math.atan2(a.transform[1], a.transform[0])) + 180) % 360 - 180,
                math.hypot(a.transform[0], a.transform[1]))

    def set_selection_transform(self, *, angle=None, scale=None):
        self._cancel_edit()
        values = self.selection_transform_values()
        if values is None:
            return
        old_angle, old_scale = values
        if angle is not None and not math.isfinite(angle):
            raise ValueError("角度必须是有限数值")
        if scale is not None and (not math.isfinite(scale) or scale <= 0):
            raise ValueError("缩放必须大于 0")
        rotation = 0 if angle is None else (angle - old_angle + 180) % 360 - 180
        factor = 1 if scale is None else scale / old_scale
        if abs(rotation) < 1e-9 and abs(factor - 1) < 1e-9:
            return
        # Use image-space geometry so the pivot is independent of view zoom.
        bounds = None
        for i in sorted(self.selection):
            rect = annotation_bounds(self.annotations[i], self.calibration)
            bounds = rect if bounds is None else bounds.united(rect)
        center = bounds.center()
        delta = QTransform()
        delta.translate(center.x(), center.y())
        delta.rotate(rotation)
        delta.scale(factor, factor)
        delta.translate(-center.x(), -center.y())
        updated = list(self.annotations)
        for i in self.selection:
            matrix = QTransform(*updated[i].transform) * delta
            updated[i] = replace(updated[i], transform=(matrix.m11(), matrix.m12(), matrix.m21(),
                                                        matrix.m22(), matrix.dx(), matrix.dy()))
        self._checkpoint()
        self.annotations = updated
        self._document_edited()

    def selected_group_annotations(self):
        selected = [a for i, a in enumerate(self.annotations) if i in self.selection]
        groups = {a.group_id for a in selected}
        return selected if len(groups) == 1 and "" not in groups else []

    def clear_annotations(self, kinds: set[str] | None = None) -> None:
        self._cancel_edit()
        remaining = [] if kinds is None else [a for a in self.annotations if a.kind not in kinds]
        if remaining != self.annotations:
            self._checkpoint()
            self.annotations = remaining
            self.selected = -1
            self.annotations_changed.emit()
            self.document_changed.emit()
            self.viewport().update()

    def _checkpoint(self, state=None) -> None:
        self._history.append(list(self.annotations if state is None else state))
        del self._history[:-100]
        self._redo_history.clear()

    def redo(self) -> None:
        self._cancel_edit()
        if self._redo_history:
            self._history.append(list(self.annotations))
            self.annotations = self._redo_history.pop()
            self.selected = -1
            self.annotations_changed.emit()
            self.document_changed.emit()
            self.viewport().update()

    def undo(self) -> None:
        if self._draft is not None or self._gesture is not None or self._marquee is not None:
            self.cancel_drawing()
            return
        if self._history:
            self._redo_history.append(list(self.annotations))
            self.annotations = self._history.pop()
            self.selected = -1
            self.annotations_changed.emit()
            self.document_changed.emit()
            self.viewport().update()

    def selection_bounds(self):
        zoom = max(self.transform().m11(), .001)
        bounds = None
        for index in sorted(self.selection):
            rect = annotation_bounds(self.annotations[index], self.calibration, zoom)
            if rect.width() < 8 / zoom:
                rect.adjust(-4 / zoom, 0, 4 / zoom, 0)
            if rect.height() < 8 / zoom:
                rect.adjust(0, -4 / zoom, 0, 4 / zoom)
            bounds = rect if bounds is None else bounds.united(rect)
        return bounds.adjusted(-6 / zoom, -6 / zoom, 6 / zoom, 6 / zoom) if bounds is not None else None

    def selection_handles(self):
        bounds = self.selection_bounds()
        if bounds is None:
            return [], None
        zoom = max(self.transform().m11(), .001)
        return ([bounds.topLeft(), bounds.topRight(), bounds.bottomRight(), bounds.bottomLeft()],
                QPointF(bounds.center().x(), bounds.top() - 24 / zoom))

    def _hit_handle(self, point):
        corners, rotate = self.selection_handles()
        radius = 8 / max(self.transform().m11(), .001)
        if rotate is not None and math.hypot(point.x() - rotate.x(), point.y() - rotate.y()) <= radius:
            return "rotate"
        if any(math.hypot(point.x() - p.x(), point.y() - p.y()) <= radius for p in corners):
            return "scale"
        return None

    def _hit_annotation(self, point):
        zoom = max(self.transform().m11(), .001)
        return next((i for i in range(len(self.annotations) - 1, -1, -1)
                     if hit_annotation(self.annotations[i], point, self.calibration, zoom)), -1)

    def _select_press(self, event):
        point = self.mapToScene(event.position().toPoint())
        action = self._hit_handle(point)
        if action:
            self._gesture = {"action": action, "start": point,
                             "center": self.selection_bounds().center(), "before": list(self.annotations)}
            return
        hit = self._hit_annotation(point)
        toggle = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if hit < 0 and not toggle and self.selection:
            groups = {self.annotations[i].group_id for i in self.selection}
            if len(groups) == 1 and "" not in groups and self.selection_bounds().contains(point):
                hit = self.selected
        if hit >= 0:
            indices = self._expanded_selection({hit})
            if toggle:
                self.select_annotations(self.selection - indices if indices <= self.selection else self.selection | indices)
                return
            if hit not in self.selection:
                self.select_annotations(indices)
            self.annotation_selected.emit(hit)
            self._gesture = {"action": "move", "start": point,
                             "center": self.selection_bounds().center(), "before": list(self.annotations)}
        else:
            before = set(self.selection)
            base = before if toggle else set()
            self.select_annotations(base)
            self._marquee = {"start": point, "end": point, "base": base, "before": before}

    def _select_move(self, point):
        if self._marquee is not None:
            self._marquee["end"] = point
            rect = QRectF(self._marquee["start"], point).normalized()
            zoom = max(self.transform().m11(), .001)
            if rect.width() > 2 / zoom or rect.height() > 2 / zoom:
                indices = {i for i, a in enumerate(self.annotations)
                           if rect.intersects(annotation_bounds(a, self.calibration, zoom).adjusted(-1, -1, 1, 1))}
                self.select_annotations(indices | self._marquee["base"])
            self.viewport().update()
            return
        gesture = self._gesture
        if gesture is None:
            return
        start, center = gesture["start"], gesture["center"]
        delta = QTransform()
        if math.hypot(point.x() - start.x(), point.y() - start.y()) > 2 / max(self.transform().m11(), .001):
            if gesture["action"] == "move":
                delta.translate(point.x() - start.x(), point.y() - start.y())
            else:
                delta.translate(center.x(), center.y())
                if gesture["action"] == "scale":
                    radius = math.hypot(start.x() - center.x(), start.y() - center.y())
                    factor = min(50., max(.02, math.hypot(point.x() - center.x(), point.y() - center.y()) / max(radius, 1e-9)))
                    delta.scale(factor, factor)
                else:
                    angle = math.atan2(point.y() - center.y(), point.x() - center.x()) - math.atan2(start.y() - center.y(), start.x() - center.x())
                    delta.rotate(math.degrees(angle))
                delta.translate(-center.x(), -center.y())
        updated = list(gesture["before"])
        try:
            for index in self.selection:
                annotation = gesture["before"][index]
                matrix = QTransform(*annotation.transform) * delta
                updated[index] = replace(annotation, transform=(matrix.m11(), matrix.m12(), matrix.m21(),
                                                                  matrix.m22(), matrix.dx(), matrix.dy()))
        except ValueError:
            return
        self.annotations = updated
        self.viewport().update()

    def _finish_edit(self):
        gesture, self._gesture = self._gesture, None
        self._marquee = None
        if gesture is not None and self.annotations != gesture["before"]:
            self._checkpoint(gesture["before"])
            self._document_edited()
        self.viewport().update()

    def _cancel_edit(self):
        if self._gesture is not None:
            self.annotations = self._gesture["before"]
            self._gesture = None
        if self._marquee is not None:
            before = self._marquee["before"]
            self._marquee = None
            self.select_annotations(before)

    def _paint_selection(self, painter):
        zoom = max(self.transform().m11(), .001)
        bounds = self.selection_bounds()
        painter.save()
        painter.setPen(QPen(QColor("#9be2cf"), 1 / zoom, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if bounds is not None:
            painter.drawRect(bounds)
            corners, rotate = self.selection_handles()
            painter.drawLine(QPointF(bounds.center().x(), bounds.top()), rotate)
            painter.setPen(QPen(QColor("#ffffff"), 1 / zoom))
            painter.setBrush(QColor("#287968"))
            for point in corners:
                painter.drawRect(QRectF(point.x() - 4 / zoom, point.y() - 4 / zoom, 8 / zoom, 8 / zoom))
            painter.drawEllipse(rotate, 5 / zoom, 5 / zoom)
        if self._marquee is not None:
            painter.setBrush(QColor(105, 211, 181, 35))
            painter.drawRect(QRectF(self._marquee["start"], self._marquee["end"]).normalized())
        painter.restore()

    def _point(self, event) -> tuple[float, float] | None:
        if self._rgb is None:
            return None
        point = self.mapToScene(event.position().toPoint())
        return (float(np.clip(point.x(), 0, self._rgb.shape[1] - 1)),
                float(np.clip(point.y(), 0, self._rgb.shape[0] - 1)))

    def _finish_polygon(self) -> None:
        draft, self._draft = self._draft, None
        if draft is not None and len(draft.points) >= 3 and draft.area_px > 0:
            self.add_annotation(draft)
        self.viewport().update()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.tool in ("polygon", "polygon_shape") and event.button() == Qt.MouseButton.LeftButton:
            point = self._point(event)
            if self._draft is not None and point is not None:
                if math.dist(self._draft.points[-1], point) > 1e-6:
                    self._draft = replace(self._draft, points=self._draft.points + (point,))
                self._finish_polygon()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._draft is not None:
            if self.tool in ("polygon", "polygon_shape"):
                self._finish_polygon()
            else:
                self.cancel_drawing()
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_position = event.position().toPoint()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or self.tool == "pan":
            super().mousePressEvent(event)
            return
        if self._rgb is not None and self.tool == "select":
            self._select_press(event)
            event.accept()
            return
        if self._rgb is None or not self.sceneRect().contains(self.mapToScene(event.position().toPoint())):
            return
        if self.tool in ("measure", "polygon", "angle") and not self.measurement_enabled:
            event.accept()
            return
        point = self._point(event)
        if self.tool in ("polygon", "polygon_shape", "angle"):
            self._draft_cursor = point
            if self._draft is None:
                self._draft = self._measurement_snapshot(Annotation(self.tool, (point,), self.color))
            elif math.dist(self._draft.points[-1], point) >= 1e-6:
                self._draft = replace(self._draft, points=self._draft.points + (point,))
            if self.tool == "angle" and len(self._draft.points) == 3:
                draft, self._draft = self._draft, None
                self.add_annotation(draft)
        elif self.tool == "marker":
            numbers = [int(a.text.removeprefix("P")) for a in self.annotations
                       if a.kind == "marker" and a.text.removeprefix("P").isdigit()]
            self.add_annotation(Annotation("marker", (point,), self.color,
                                           str(max(numbers, default=0) + 1)))
        elif self.tool == "text":
            self.text_requested.emit(point)
        else:
            self._draft = self._measurement_snapshot(Annotation(self.tool, (point, point), self.color))
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
        self._update_cursor_info()
        if self._gesture is not None or self._marquee is not None:
            self._select_move(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        point = self._point(event)
        if self._draft is not None and point is not None and self.tool in ("polygon", "polygon_shape", "angle"):
            self._draft_cursor = point
            self.viewport().update()
            event.accept()
            return
        if self._draft is not None and point is not None and self.tool not in ("polygon", "polygon_shape", "angle"):
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
        if event.button() == Qt.MouseButton.LeftButton and (self._gesture is not None or self._marquee is not None):
            self._select_move(self.mapToScene(event.position().toPoint()))
            self._finish_edit()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._draft is not None and self.tool not in ("polygon", "polygon_shape", "angle"):
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
        self._cancel_edit()
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
        draft = self._draft
        if draft is not None and self._draft_cursor is not None and self.tool in ("polygon", "polygon_shape", "angle"):
            if math.dist(draft.points[-1], self._draft_cursor) > 1e-6:
                draft = replace(draft, points=draft.points + (self._draft_cursor,))
        annotations = self.annotations + ([draft] if draft else [])
        paint_annotations(painter, annotations, self.calibration, self.transform().m11(), self.selection)
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
        if self.tool == "select":
            self._paint_selection(painter)

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
