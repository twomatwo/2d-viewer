"""Durable, per-view annotation journals and an unmodified sensor snapshot."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import json
import math
import os
from uuid import uuid4

from PySide6.QtWidgets import QFileDialog

from .exporting import load_image, save_raw
from .models import Annotation, Calibration
from .settings_store import display_from_dict
from .image_assets import decode_image_asset


class AnnotationRecovery:
    def _start_recovery_view(self):
        self._recovery_key = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]
        self._recovery_signature = None

    def _journal_annotations(self):
        if self.current is None or self._pending_recovery is not None or self.viewer._gesture is not None:
            return
        folder = self.data_directory / "recovery"
        image = folder / (self._recovery_key + ".tiff")
        if not self.viewer.annotations and not image.exists():
            return
        record = {"schema": "labview.annotations.v1", "image": image.name,
                  "annotations": [asdict(a) for a in self.viewer.annotations],
                  "calibration": asdict(self.viewer.calibration),
                  "display_settings": asdict(self.current.settings),
                  "scale_bar_length_um": self.viewer.scale_bar_length_um}
        signature = json.dumps(record, ensure_ascii=False, allow_nan=False)
        if signature == self._recovery_signature:
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not image.exists():
                temporary_image = image.with_suffix(".tmp.tiff")
                save_raw(temporary_image, self.current, [], self.viewer.calibration)
                temporary_image.replace(image)
            target = image.with_suffix(".json")
            temporary = target.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(signature)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
            self._recovery_signature = signature
        except (OSError, ValueError) as exc:
            self._error(f"标注自动保存失败：{exc}")

    def _restore_annotations(self):
        if self.capture is not None:
            self.statusBar().showMessage("请先停止相机采集，再恢复标注视野。", 6000)
            return
        filename, _ = QFileDialog.getOpenFileName(self, "恢复自动保存的标注视野",
            str(self.data_directory / "recovery"), "标注恢复记录 (*.json)")
        if not filename:
            return
        try:
            path = Path(filename)
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("schema") != "labview.annotations.v1":
                raise ValueError("不是标注恢复记录")
            image = path.with_suffix(".tiff")
            frame = load_image(image)
            calibration = Calibration(**record["calibration"])
            context = frame.profile_context
            legacy_name = context.get("objective_name") or calibration.profile_name
            legacy_objective = self.store.objective(context.get("objective_id") or legacy_name)
            annotations = []
            allowed = {"marker", "text", "line", "rect", "ellipse", "freehand", "measure", "polygon", "angle", "polygon_shape", "image"}
            for item in record["annotations"]:
                points = tuple(tuple(float(v) for v in point) for point in item["points"])
                if item["kind"] not in allowed or not points or any(
                        len(point) != 2 or not all(math.isfinite(v) for v in point) for point in points):
                    raise ValueError("标注坐标无效")
                scale_record = item.get("calibration")
                scale = Calibration(**scale_record) if scale_record is not None else calibration
                magnification = item.get("magnification", context.get("magnification",
                    legacy_objective["magnification"] if legacy_objective else None))
                if magnification is not None:
                    magnification = float(magnification)
                    if not math.isfinite(magnification) or magnification <= 0:
                        raise ValueError("物镜倍率无效")
                annotations.append(Annotation(item["kind"], points, item["color"], item["text"],
                    scale if item["kind"] in ("measure", "polygon", "angle") else None,
                    str(item.get("objective_name", legacy_name)),
                    str(item.get("objective_id", context.get("objective_id", ""))), magnification,
                    group_id=item.get("group_id", ""),
                    transform=tuple(float(v) for v in item.get("transform", (1, 0, 0, 1, 0, 0))),
                    image_name=item.get("image_name", ""), image_data=item.get("image_data", ""),
                    image_opacity=float(item.get("image_opacity", 1)),
                    mean_rgb=tuple(float(v) for v in item["mean_rgb"]) if item.get("mean_rgb") is not None else None,
                    rgb_pixel_count=item.get("rgb_pixel_count", 0)))
                if item["kind"] == "angle" and annotations[-1].angle_degrees is None:
                    raise ValueError("角度测量必须包含三个有效点，第二个点为顶点")
                if item["kind"] == "image":
                    decode_image_asset(annotations[-1].image_data)
            settings = display_from_dict(record["display_settings"])
            length = float(record.get("scale_bar_length_um", 100))
            if not math.isfinite(length) or length <= 0:
                raise ValueError("比例尺长度无效")
            def restore():
                self._new_source()
                self._pending_recovery = (annotations, calibration, length)
                self._set_display_settings(settings)
                self.raw_buffer.put(frame)
                self.source_badge.setText("恢复视野 · " + path.stem)
            self._confirm_new_view(restore)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self._error(f"恢复失败，原文件保留：{exc}")
