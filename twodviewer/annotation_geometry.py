"""Qt paths share transformed geometry across drawing, hit testing and export."""
import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath, QPainterPathStroker, QTransform


def mapped_points(annotation):
    transform = QTransform(*annotation.transform)
    return [transform.map(QPointF(*point)) for point in annotation.points]


def label_text(annotation, calibration):
    if annotation.kind == "image":
        return ""
    if annotation.kind == "angle" and annotation.angle_degrees is None:
        return ""
    return (annotation.measurement_label(calibration)
            if annotation.kind in ("measure", "polygon", "calibrate", "angle") else annotation.text)


def label_position(annotation, zoom=1):
    points = mapped_points(annotation)
    anchor = ((points[0] + points[-1]) / 2
              if annotation.kind in ("measure", "calibrate") else points[0])
    if annotation.kind == "angle" and len(points) >= 2:
        anchor = points[1]
    return anchor + QPointF(7 / zoom, -7 / zoom)


def label_bounds(annotation, calibration, zoom=1):
    text = label_text(annotation, calibration)
    if not text or not annotation.points:
        return QRectF()
    font = QFont("Microsoft YaHei")
    font.setPixelSize(max(1, round(14 / zoom)))
    return QFontMetricsF(font).boundingRect(text).translated(label_position(annotation, zoom))


def annotation_path(annotation, zoom=1):
    path = QPainterPath()
    if not annotation.points:
        return path
    points = [QPointF(*point) for point in annotation.points]
    start = points[0]
    if annotation.kind in ("rect", "ellipse", "image") and len(points) >= 2:
        rect = QRectF(start, points[-1]).normalized()
        if annotation.kind in ("rect", "image"):
            path.addRect(rect)
        else:
            path.addEllipse(rect)
    elif annotation.kind in ("marker", "text"):
        anchor = mapped_points(annotation)[0]
        radius = 3 / zoom
        path.moveTo(anchor + QPointF(-radius, 0)); path.lineTo(anchor + QPointF(radius, 0))
        path.moveTo(anchor + QPointF(0, -radius)); path.lineTo(anchor + QPointF(0, radius))
        return path
    else:
        path.moveTo(start)
        for point in (points[-1:] if annotation.kind in ("line", "measure", "calibrate") else points[1:]):
            path.lineTo(point)
        if annotation.kind in ("polygon", "polygon_shape"):
            path.closeSubpath()
    path = QTransform(*annotation.transform).map(path)
    if annotation.kind == "angle" and annotation.angle_degrees is not None:
        first, vertex, last = mapped_points(annotation)
        a, b = first - vertex, last - vertex
        radius = min(math.hypot(a.x(), a.y()), math.hypot(b.x(), b.y())) * .3
        radius = min(radius, 22 / max(zoom, .001))
        start = math.degrees(math.atan2(-a.y(), a.x()))
        end = math.degrees(math.atan2(-b.y(), b.x()))
        sweep = (end - start + 180) % 360 - 180
        rect = QRectF(vertex.x() - radius, vertex.y() - radius, 2 * radius, 2 * radius)
        path.arcMoveTo(rect, start)
        path.arcTo(rect, start, sweep)
    return path


def annotation_bounds(annotation, calibration, zoom=1):
    if annotation.kind == "text":
        return label_bounds(annotation, calibration, zoom)
    return annotation_path(annotation, zoom).boundingRect()


def hit_annotation(annotation, position, calibration, zoom=1):
    path = annotation_path(annotation, zoom)
    stroker = QPainterPathStroker()
    stroker.setWidth(16 / zoom)
    return (stroker.createStroke(path).contains(position) or
            annotation.kind in ("rect", "ellipse", "polygon", "polygon_shape", "image") and path.contains(position) or
            label_bounds(annotation, calibration, zoom).contains(position))
