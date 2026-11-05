from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json

import cv2
import numpy as np
import tifffile

from .models import Annotation, Calibration, Frame, ProcessedFrame


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_png(path: Path, image, metadata: dict) -> None:
    # Keep the capture record in the PNG itself, without an extra visible file.
    image.setText("LabView", json.dumps(metadata, ensure_ascii=False))
    if not image.save(str(path), "PNG"):
        raise OSError("PNG 写入失败")


def export_metadata(processed: ProcessedFrame, annotations: list[Annotation],
                    calibration: Calibration, kind: str) -> dict:
    frame = processed.frame
    return {"schema": "labview.capture.v1", "export": kind, "source": frame.source,
            "captured_at": frame.captured_at, "frame_id": frame.frame_id,
            "camera_timestamp_ns": frame.timestamp_ns, "pixel_format": frame.pixel_format,
            "raw_dtype": str(frame.raw.dtype), "raw_shape": list(frame.raw.shape),
            "white_level": frame.white_level, "camera_settings": frame.camera_settings,
            "per_frame_camera_fields": list(frame.per_frame_fields),
            "camera_settings_basis": "per-frame chunks for listed fields; other fields are last parameter readback",
            "display_settings": asdict(processed.settings), "calibration": asdict(calibration),
            "camera_configuration": frame.configuration, "objective_profile": frame.profile_context,
            "annotations": [dict(asdict(a), length_px=a.length_px,
                                 measured_length=a.length_px * calibration.units_per_pixel
                                 if calibration.units_per_pixel else None) for a in annotations],
            "annotation_coordinate_space": "display pixels after orientation transforms",
            "display_shape": list(processed.rgb.shape)}


def save_raw(path: Path, processed: ProcessedFrame, annotations: list[Annotation],
             calibration: Calibration, *, scale_bar_length_um: float | None = None) -> None:
    data = export_metadata(processed, annotations, calibration, "raw")
    data["annotations_burned_in"] = False
    if scale_bar_length_um is not None:
        data["scale_bar_length_um"] = scale_bar_length_um
    raw = processed.frame.raw
    # A Bayer TIFF intentionally remains a one-channel sensor mosaic.
    tifffile.imwrite(path, raw, photometric="rgb" if raw.ndim == 3 else "minisblack",
                     description=json.dumps(data, ensure_ascii=True), metadata=None)


def load_image(path: Path) -> Frame:
    metadata = {}
    if path.suffix.lower() in (".tif", ".tiff"):
        with tifffile.TiffFile(path) as tif:
            raw = tif.asarray()
            try:
                description = json.loads(tif.pages[0].description or "{}")
                if isinstance(description, dict) and description.get("schema") == "labview.capture.v1":
                    metadata = description
            except (TypeError, ValueError):
                pass
            if not metadata and raw.ndim == 3 and tif.pages[0].planarconfig == 2:
                raw = np.moveaxis(raw, 0, -1)
    else:
        if path.suffix.lower() == ".png":
            from PySide6.QtGui import QImageReader
            reader = QImageReader(str(path))
            try:
                record = json.loads(reader.text("LabView") or "{}")
                if isinstance(record, dict) and record.get("schema") == "labview.capture.v1":
                    metadata = record
            except (ValueError, TypeError):
                pass
        raw = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise ValueError("无法读取图像")
        if raw.ndim == 3:
            raw = cv2.cvtColor(raw, cv2.COLOR_BGRA2RGB if raw.shape[2] == 4 else cv2.COLOR_BGR2RGB)
    if raw.dtype not in (np.uint8, np.uint16) or raw.ndim not in (2, 3):
        raise ValueError("仅支持单张 uint8/uint16 图像")
    if raw.ndim == 3 and raw.shape[2] not in (3, 4):
        raise ValueError("仅支持灰度或 RGB 图像，不支持 TIFF 图像堆栈")
    raw = np.ascontiguousarray(raw)
    raw.setflags(write=False)
    default_format = "RGB8" if raw.ndim == 3 else "Mono8"
    if raw.dtype == np.uint16:
        default_format = "RGB16" if raw.ndim == 3 else "Mono16"
    camera_settings = dict(metadata.get("camera_settings", {}))
    if metadata.get("source"):
        camera_settings.setdefault("_source_camera", metadata["source"])
    display_export = metadata.get("export") in ("display", "timed-display")
    return Frame(raw, default_format if display_export else metadata.get("pixel_format", default_format),
                 float(np.iinfo(raw.dtype).max if display_export else metadata.get("white_level", np.iinfo(raw.dtype).max)),
                 int(metadata.get("frame_id", 0)), metadata.get("captured_at", datetime.now(timezone.utc).isoformat()),
                 str(path), int(metadata.get("camera_timestamp_ns", 0)),
                 camera_settings, tuple(metadata.get("per_frame_camera_fields", [])),
                 configuration=metadata.get("camera_configuration", {}),
                 profile_context=metadata.get("objective_profile", {}),
                 capture_calibration=metadata.get("calibration", {}))
