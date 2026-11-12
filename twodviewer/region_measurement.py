"""OpenCV ROI statistics on the displayed base image, excluding annotations."""
import math

import cv2
import numpy as np


def region_rgb(rgb, points):
    if len(points) < 3:
        return None, 0
    coordinates = np.array([(p.x(), p.y()) for p in points], dtype=np.float64)
    height, width = rgb.shape[:2]
    left = max(0, min(width, math.floor(coordinates[:, 0].min())))
    top = max(0, min(height, math.floor(coordinates[:, 1].min())))
    right = max(0, min(width, math.ceil(coordinates[:, 0].max()) + 1))
    bottom = max(0, min(height, math.ceil(coordinates[:, 1].max()) + 1))
    if right <= left or bottom <= top:
        return None, 0
    # Fixed-point vertices retain subpixel placement; OpenCV includes boundary pixels.
    local = coordinates - (left, top)
    if not np.isfinite(local).all() or np.abs(local).max() > 8_000_000:
        raise ValueError("区域坐标超出 RGB 统计范围")
    contour = np.rint(local * 256).astype(np.int32)
    mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
    cv2.fillPoly(mask, [contour], 255, shift=8)
    count = cv2.countNonZero(mask)
    if count == 0:
        return None, 0
    mean = cv2.mean(np.ascontiguousarray(rgb[top:bottom, left:right]), mask=mask)[:3]
    # OpenCV reciprocals can round an all-white mean just above 255.
    return tuple(float(min(255, max(0, v))) for v in mean), count
