from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
import csv
import json
import logging
import math
from uuid import uuid4

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QFileDialog, QInputDialog

from .discovery import DiscoveryWorker
from .exporting import export_metadata, save_raw, save_png
from .models import Calibration, DisplaySettings, ProcessedFrame
from .annotation_geometry import mapped_points
from .processing import decode_rgb, render_rgb, histogram, luminance_histogram
from .recording import VideoRecorder
from .settings_store import display_from_dict, sampling_context
from .viewer import rgb_image
from .capture_naming import effective_sequence, naming_settings, render_name


class CameraControls:
    def _scan_cameras(self):
        if self.capture is not None or self.discovery is not None: return
        self.discovery = DiscoveryWorker()
        self.discovery.found.connect(self._devices_found)
        self.discovery.error.connect(self._error)
        self.discovery.finished.connect(self._discovery_finished)
        self.scan_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.discovery.start()

    def _devices_found(self, devices):
        previous = self.source_combo.currentData()
        with QSignalBlocker(self.source_combo):
            self.source_combo.clear()
            self.source_combo.addItem("自动选择（仅一台相机时）", "")
            for device in devices:
                self.source_combo.addItem(f"{device['model']} · {device['serial']}", device["serial"])
            index = self.source_combo.findData(previous) if previous else -1
            self.source_combo.setCurrentIndex(index if index >= 0 else 1 if len(devices) == 1 else 0)

    def _discovery_finished(self):
        self.discovery.deleteLater()
        self.discovery = None
        self.scan_button.setEnabled(True)
        self.connect_button.setEnabled(True)
        if self._closing: self.close()

    def _connected(self, description):
        self.source_badge.setText(description["model"])
        from . import APP_NAME, __version__
        self.setWindowTitle(f"{APP_NAME} {__version__} · {description['model']}")
        label = f"{description['model']} · {description['serial']}"
        values = description["settings"]
        speed = values.get("U3VCurrentSpeed")
        if speed:
            speed_label = {"HighSpeed": "USB 2.0", "SuperSpeed": "USB 3.x"}.get(speed, speed)
            label += f"\n当前连接：{speed_label}"
            if values.get("DeviceLinkSpeed"):
                label += f" · {values['DeviceLinkSpeed'] / 1e6:g} MB/s"
        self.device_label.setText(label)
        logging.getLogger(__name__).info("Camera connected: model=%s serial=%s link=%s",
                                         description["model"], description["serial"], speed or "unknown")
        self._set_camera_controls(True)
        self._camera_settings_updated(description)
        self._profiles_connected()

    def _camera_settings_updated(self, incoming):
        if "telemetry" in incoming:
            values = incoming["telemetry"]
            self._update_camera_rate(values)
            self.actual_exposure_label.setText(self._camera_readout(values))
            # Continuous/Once controls are read back; never overwrite a pending mode request.
            for name, checkbox in self._mode_checkboxes().items():
                if name not in incoming.get("modes", {}) and name not in values:
                    continue
                mode = incoming.get("modes", {}).get(name, values.get(name, "Off"))
                if name in self._pending_modes: continue
                with QSignalBlocker(checkbox): checkbox.setChecked(mode != "Off")
                self._camera_description.setdefault("settings", {})[name] = mode
            self._update_auto_controls()
            if self._profile_actual:
                self._profile_actual = dict(self._profile_actual, settings=dict(
                    self._profile_actual.get("settings", {}), **incoming.get("modes", {})))
            self._refresh_profile_ui()
            return
        if "settings" not in incoming: incoming = dict(self._camera_description, settings=incoming)
        self._camera_description = incoming
        self._pending_modes.clear()
        values, numeric, enums = incoming["settings"], incoming.get("numeric", {}), incoming.get("enums", {})
        self._update_camera_rate(values)
        self._updating_camera = True
        try:
            for name, box in self.numeric_boxes.items():
                limits = numeric.get(name)
                box.setEnabled(bool(limits) and limits.get("writable", True))
                if limits:
                    factor = 1000 if name in ("ExposureTime", "AutoExposureExposureTimeUpperLimit") else 1
                    with QSignalBlocker(box):
                        box.setRange(limits["min"] / factor, limits["max"] / factor)
                        # A reply to an earlier command can arrive while the user
                        # is entering the next value. Preserve that active edit.
                        if not (box.hasFocus() or box.lineEdit().hasFocus()):
                            box.setValue(values.get(name, limits.get("value", box.value() * factor)) / factor)
            for name, checkbox in self._mode_checkboxes().items():
                checkbox.setEnabled("Continuous" in enums.get(name, []) and incoming.get("enum_writable", {}).get(name, True))
                with QSignalBlocker(checkbox): checkbox.setChecked(values.get(name, "Off") != "Off")
            self.once_exposure_button.setEnabled("Once" in enums.get("ExposureAuto", []) and incoming.get("enum_writable", {}).get("ExposureAuto", True))
            self.once_wb_button.setEnabled("Once" in enums.get("BalanceWhiteAuto", []) and incoming.get("enum_writable", {}).get("BalanceWhiteAuto", True))
            combo = self.enum_boxes["PixelFormat"]
            self.frame_rate_check.setEnabled(incoming.get("booleans", {}).get("AcquisitionFrameRateEnable", False))
            with QSignalBlocker(self.frame_rate_check):
                self.frame_rate_check.setChecked(bool(values.get("AcquisitionFrameRateEnable", "AcquisitionFrameRate" in numeric)))
            combo.setEnabled(bool(enums.get("PixelFormat")) and incoming.get("enum_writable", {}).get("PixelFormat", True))
            with QSignalBlocker(combo):
                combo.clear()
                combo.addItems(enums.get("PixelFormat", []))
                combo.setCurrentText(values.get("PixelFormat", ""))
            size = incoming.get("sensor_size", [values.get("Width", 1200), values.get("Height", 900)])
            for name, box in self.roi_boxes.items():
                limits = incoming.get("integers", {}).get(name)
                box.setEnabled(bool(limits) and limits.get("writable", True))
                if limits:
                    maximum = size[0] if name in ("Width", "OffsetX") else size[1]
                    with QSignalBlocker(box):
                        extent = "Width" if name == "OffsetX" else "Height"
                        minimum_extent = incoming.get("integers", {}).get(extent, {}).get("min", 1)
                        upper = maximum if name in ("Width", "Height") else max(limits["min"], maximum - minimum_extent)
                        box.setRange(limits["min"], upper)
                        box.setSingleStep(max(1, limits.get("inc", 1)))
                        box.setValue(values.get(name, limits["min"]))
            roi_writable = all(incoming.get("integers", {}).get(name, {}).get("writable", False)
                               for name in ("Width", "Height"))
            self.resolution_combo.setEnabled(roi_writable)
            self.apply_roi_button.setEnabled(roi_writable)
            self.full_roi_button.setEnabled(roi_writable)
            self.camera_info.setText(f"采集 {values.get('Width', '—')} × {values.get('Height', '—')} · {values.get('PixelFormat', '—')}")
            self.actual_exposure_label.setText(self._camera_readout(values))
            self._sync_exposure_slider()
            self._sync_gain_slider()
            self._update_auto_controls()
        finally:
            self._updating_camera = False
        self._profiles_received(incoming)

    @staticmethod
    def _camera_readout(values):
        exposure = f"{values['ExposureTime'] / 1000:.3f} ms" if "ExposureTime" in values else "—"
        gain = f"{values['Gain']:.2f} dB" if "Gain" in values else "—"
        return f"当前曝光 {exposure} · 增益 {gain}"

    def _mode_checkboxes(self):
        return {"ExposureAuto": self.auto_exposure_check, "GainAuto": self.auto_gain_check,
                "BalanceWhiteAuto": self.auto_wb_check}

    def _update_auto_controls(self):
        numeric = self._camera_description.get("numeric", {})
        rate = numeric.get("AcquisitionFrameRate", {})
        self.numeric_boxes["AcquisitionFrameRate"].setEnabled(
            bool(rate) and rate.get("writable", True) and self.frame_rate_check.isChecked())
        automatic = self.auto_exposure_check.isChecked()
        automatic_gain = self.auto_gain_check.isChecked()
        self.numeric_boxes["ExposureTime"].setEnabled("ExposureTime" in numeric and numeric["ExposureTime"].get("writable", True) and not automatic)
        self.exposure_slider.setEnabled("ExposureTime" in numeric and numeric["ExposureTime"].get("writable", True) and not automatic)
        self.numeric_boxes["Gain"].setEnabled("Gain" in numeric and numeric["Gain"].get("writable", True) and not automatic_gain)
        self.gain_slider.setEnabled("Gain" in numeric and numeric["Gain"].get("writable", True) and not automatic_gain)
        for name in ("AutoExposureTargetGreyValue", "AutoExposureExposureTimeUpperLimit", "AutoExposureGainUpperLimit"):
            supported = name in numeric and numeric[name].get("writable", True)
            self.numeric_boxes[name].setEnabled(supported)
            self.numeric_boxes[name].setToolTip("设置在开启自动控制后生效" if supported else
                                               "当前相机此参数不可写" if name in numeric else "当前相机未提供此参数")
        for name in ("BalanceRatioRed", "BalanceRatioBlue"):
            self.numeric_boxes[name].setEnabled(name in numeric and numeric[name].get("writable", True) and not self.auto_wb_check.isChecked())

    def _camera_command(self, settings):
        if self.capture is not None and not self._updating_camera:
            geometry = {"SensorROI", "PixelFormat", "BinningHorizontal", "BinningVertical",
                        "DecimationHorizontal", "DecimationVertical"}
            if geometry.intersection(settings) and (self.recorder is not None or self.timelapse_timer.isActive()):
                self.statusBar().showMessage("请先停止录像或定时拍照，再改变相机格式或采集区域。", 6000)
                return
            if self._profile_busy():
                self.statusBar().showMessage("请等待相机方案回读完成，再调整参数。", 5000)
                return
            token = uuid4().hex
            self._automatic_once_pending.update(name for name, value in settings.items() if value == "Once")
            self._profile_requests[token] = {"kind": "control"}
            self.capture.configure_many(settings, token)
            self._refresh_profile_ui()

    def _update_camera_rate(self, values):
        rate = values.get("AcquisitionResultingFrameRate")
        self.camera_fps_label.setText(f"相机 {rate:.1f} FPS" if rate is not None else "相机 —")

    def _frame_rate_mode_changed(self, enabled):
        self._update_auto_controls()
        self._camera_command({"AcquisitionFrameRateEnable": enabled})

    def _exposure_mode_changed(self, enabled):
        self._update_auto_controls()
        self._pending_modes["ExposureAuto"] = "Continuous" if enabled else "Off"
        self._camera_command({"ExposureAuto": self._pending_modes["ExposureAuto"]})

    def _gain_mode_changed(self, enabled):
        self._update_auto_controls()
        self._pending_modes["GainAuto"] = "Continuous" if enabled else "Off"
        self._camera_command({"GainAuto": self._pending_modes["GainAuto"]})

    def _wb_mode_changed(self, enabled):
        self._update_auto_controls()
        self._pending_modes["BalanceWhiteAuto"] = "Continuous" if enabled else "Off"
        self._camera_command({"BalanceWhiteAuto": self._pending_modes["BalanceWhiteAuto"]})

    def _exposure_once(self):
        self._camera_command({"ExposureAuto": "Once"})

    def _commit_numeric(self, name):
        box = self.numeric_boxes[name]
        if not box.isEnabled(): return
        factor = 1000 if name in ("ExposureTime", "AutoExposureExposureTimeUpperLimit") else 1
        self._camera_command({name: box.value() * factor})

    def _exposure_slider_changed(self, value):
        box = self.numeric_boxes["ExposureTime"]
        low, high = max(box.minimum(), 0.001), max(box.maximum(), 0.002)
        box.setValue(10 ** (math.log10(low) + value / 1000 * math.log10(high / low)))
        if not self.exposure_slider.isSliderDown():
            self._commit_numeric("ExposureTime")

    def _sync_exposure_slider(self, *args):
        box = self.numeric_boxes["ExposureTime"]
        low, high = max(box.minimum(), 0.001), max(box.maximum(), 0.002)
        value = round(1000 * math.log10(max(box.value(), low) / low) / max(math.log10(high / low), 1e-9))
        with QSignalBlocker(self.exposure_slider): self.exposure_slider.setValue(value)

    def _gain_slider_changed(self, value):
        box = self.numeric_boxes["Gain"]
        box.setValue(box.minimum() + value / 1000 * (box.maximum() - box.minimum()))
        if not self.gain_slider.isSliderDown():
            self._commit_numeric("Gain")

    def _sync_gain_slider(self, *args):
        box = self.numeric_boxes["Gain"]
        value = round(1000 * (box.value() - box.minimum()) / max(box.maximum() - box.minimum(), 1e-9))
        with QSignalBlocker(self.gain_slider): self.gain_slider.setValue(value)

    def _pixel_format_changed(self, *args):
        combo = self.enum_boxes["PixelFormat"]
        if combo.currentText(): self._camera_command({"PixelFormat": combo.currentText()})

    def _resolution_changed(self, index):
        size = self._camera_description.get("sensor_size", [1200, 900])
        presets = [size, [1920, 1200], [1280, 1024], [640, 480]]
        if index >= len(presets): return
        width, height = min(presets[index][0], size[0]), min(presets[index][1], size[1])
        self.roi_boxes["Width"].setValue(width)
        self.roi_boxes["Height"].setValue(height)
        self.roi_boxes["OffsetX"].setValue((size[0] - width) // 2)
        self.roi_boxes["OffsetY"].setValue((size[1] - height) // 2)

    def _apply_roi(self):
        if self.recorder is not None:
            self.statusBar().showMessage("请先停止录像，再改变采集区域。", 5000)
            return
        self._camera_command({"SensorROI": [self.roi_boxes[n].value() for n in ("OffsetX", "OffsetY", "Width", "Height")]})


class ProfileControls:
    def _restore_preferences(self):
        try:
            settings = display_from_dict(self.store.data["preferences"].get("display_settings", {}))
        except (ValueError, TypeError, AttributeError):
            settings = DisplaySettings()
        self._set_display_settings(settings)

    def _remember_display(self):
        # Application preferences remain independent of objective camera profiles.
        values = asdict(self.settings)
        if self.store.data["preferences"].get("display_settings") != values:
            try:
                self.store.transaction(lambda: self.store.data["preferences"].update(display_settings=values))
            except (OSError, ValueError) as exc:
                self._error(f"显示配置保存失败：{exc}")
        if hasattr(self, "reset_display_button"):
            self.reset_display_button.setEnabled(self.settings != DisplaySettings())
        self._update_processing_state()

    def _sync_display_controls(self):
        mapping = {"brightness": self.settings.brightness, "contrast": self.settings.contrast,
                   "saturation": self.settings.saturation, "red_gain": self.settings.channel_gain[0],
                   "green_gain": self.settings.channel_gain[1],
                   "blue_gain": self.settings.channel_gain[2]}
        for name, value in mapping.items():
            with QSignalBlocker(self.display_boxes[name]): self.display_boxes[name].setValue(value)
        for name in self.rgb_sliders:
            self._sync_rgb_slider(name, mapping[name])
        for checkbox, value in ((self.gray_check, self.settings.grayscale), (self.flip_x_check, self.settings.flip_x),
                                (self.flip_y_check, self.settings.flip_y)):
            with QSignalBlocker(checkbox): checkbox.setChecked(value)
        with QSignalBlocker(self.rotation_combo): self.rotation_combo.setCurrentIndex(self.settings.rotation)
        self._update_processing_state()

    def _sync_rgb_slider(self, name, value):
        with QSignalBlocker(self.rgb_sliders[name]):
            self.rgb_sliders[name].setValue(round(value * 100))

    def _display_controls_changed(self, *args):
        if self.recorder is not None and (self.settings.rotation != self.rotation_combo.currentIndex()):
            self.statusBar().showMessage("请先停止录像，再改变图像方向。", 5000)
            self._sync_display_controls()
            return
        self.settings = replace(self.settings, brightness=self.display_boxes["brightness"].value(),
            contrast=self.display_boxes["contrast"].value(), saturation=self.display_boxes["saturation"].value(),
            channel_gain=tuple(self.display_boxes[name].value() for name in ("red_gain", "green_gain", "blue_gain")),
            grayscale=self.gray_check.isChecked(), flip_x=self.flip_x_check.isChecked(), flip_y=self.flip_y_check.isChecked(),
            rotation=self.rotation_combo.currentIndex())
        self.processor.configure(self.settings)
        self._remember_display()
        self._update_processing_state()

    def _guides_changed(self, *args):
        self.viewer.grid_visible = self.grid_check.isChecked()
        self.viewer.crosshair_visible = self.crosshair_check.isChecked()
        self.viewer.viewport().update()


    def _save_calibration(self, calibration):
        if self.current is None:
            self.statusBar().showMessage("请先连接相机或打开图像，再标定。", 5000)
            return
        name = self._display_objective()
        self.store.set_calibration(name, calibration, sampling_context(self.current.frame))
        if self.current.frame.capture_calibration:
            self._file_calibration_override = calibration
        self.viewer.set_calibration(replace(calibration, profile_name=name))
        self._refresh_objectives()
        self.statusBar().showMessage(f"已保存 {name} 标定，下次可直接选择。", 6000)

    def _apply_scale(self):
        try: self._save_calibration(Calibration(self.scale_box.value(), self.unit_combo.currentText()))
        except (OSError, ValueError) as exc: self._error(str(exc))

    def _calibrate(self, pixels):
        unit = self.unit_combo.currentText()
        name = self._display_objective()
        length, ok = QInputDialog.getDouble(self, f"校准物镜 {name}",
            f"刚才拖出的标尺刻度有多长？\n实际长度 / {unit}（这段线在图像上是 {pixels:.2f} 像素）：", 100, 0.000001, 1e9, 6)
        if ok:
            try: self._save_calibration(Calibration.from_reference(pixels, length, unit))
            except (OSError, ValueError) as exc: self._error(str(exc))
        self.tool_actions["measure"].trigger()


    def _export_calibrations(self):
        name, _ = QFileDialog.getSaveFileName(self, "导出标定库", "calibrations.json", "JSON (*.json)")
        if name:
            try: self.store.export_calibrations(Path(name))
            except (OSError, ValueError) as exc: self._error(str(exc))

    def _import_calibrations(self):
        name, _ = QFileDialog.getOpenFileName(self, "导入标定库", "", "JSON (*.json)")
        if name:
            try:
                self.store.import_calibrations(Path(name))
                self.objective_list.addItems(self.store.objective_names())
                self._apply_selected_calibration()
            except (OSError, ValueError, KeyError, TypeError) as exc: self._error(str(exc))


    def _export_measurements(self):
        filename, _ = QFileDialog.getSaveFileName(self, "导出测量结果", "measurements.csv", "CSV (*.csv)")
        if not filename: return
        try:
            with Path(filename).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["序号", "类型", "文字", "长度_px", "实际长度", "单位", "物镜配置", "坐标", "面积_px2", "实际面积", "面积单位", "镜头倍率", "创建时每像素长度", "标定单位", "组合", "显示变换", "显示坐标", "角度_deg", "平均R", "平均G", "平均B", "RGB像素数", "RGB取样依据"])
                for index, item in enumerate((a for a in self.viewer.annotations if a.kind in ("measure", "polygon", "angle")), 1):
                    calibration = item.measurement_scale(self.viewer.calibration)
                    length = item.kind == "measure"
                    area = item.kind == "polygon"
                    measured = item.length_px * calibration.units_per_pixel if length and calibration.units_per_pixel else ""
                    writer.writerow([index, item.kind, item.text, item.length_px if length else "", measured,
                                     (calibration.unit if calibration.units_per_pixel else "px") if length else "",
                                     item.objective_name or "倍率未记录", json.dumps(item.points),
                                     item.area_px if area else "",
                                     item.area_px * calibration.units_per_pixel ** 2
                                     if area and calibration.units_per_pixel else "",
                                     (calibration.unit + "²" if calibration.units_per_pixel else "px²")
                                     if area else "", item.magnification,
                                     calibration.units_per_pixel, calibration.unit, item.group_id,
                                     json.dumps(item.transform), json.dumps([[p.x(), p.y()] for p in mapped_points(item)]),
                                     item.angle_degrees if item.kind == "angle" else "",
                                     *(item.mean_rgb if item.mean_rgb is not None else ("", "", "")),
                                     item.rgb_pixel_count if item.mean_rgb is not None else "",
                                     "完成时底图显示RGB（0–255，含边界，不含标注）" if item.mean_rgb is not None else ""])
        except OSError as exc: self._error(str(exc))


class CaptureControls:
    def _snapshot_options_changed(self, *args):
        self.snapshot_format = self.snapshot_format_box.currentData()
        self.snapshot_annotations = self.snapshot_annotations_check.isChecked()
        try:
            self.store.transaction(lambda: self.store.data["preferences"].update(
                snapshot_format=self.snapshot_format, snapshot_annotations=self.snapshot_annotations))
        except (OSError, ValueError) as exc:
            self._error(f"图像保存选项未能写入设置：{exc}")
        self._update_snapshot_summary()

    def _capture_raw_image(self):
        self.snapshot_format_box.setCurrentIndex(self.snapshot_format_box.findData("tiff"))
        self._capture_image()

    def _insert_capture_name_field(self, field):
        self.capture_name_template.setFocus()
        self.capture_name_template.insert("{" + field + "}")

    def _capture_naming_changed(self, *args):
        candidate = dict(self.snapshot_naming, template=self.capture_name_template.text(),
            sample_text=self.capture_sample_text.text(),
            date_format=self.capture_date_format.currentData(),
            sequence_next=self.capture_next_sequence.value(), sequence_digits=self.capture_sequence_digits.value(),
            sequence_step=self.capture_sequence_step.value(), sequence_start=self.capture_sequence_start.value(),
            reset_daily=self.capture_daily_reset.isChecked())
        self.capture_sequence_start.setEnabled(candidate["reset_daily"])
        if self.sender() is self.capture_next_sequence:
            candidate["sequence_date"] = datetime.now().strftime("%Y-%m-%d")
        elif self.sender() is self.capture_daily_reset and candidate["reset_daily"]:
            candidate["sequence_date"] = ""
        try:
            candidate = naming_settings(candidate)
        except ValueError as exc:
            self._naming_error = str(exc)
            self._update_capture_name_preview()
            return
        self._naming_error = ""
        self.snapshot_naming = candidate
        try:
            self.store.transaction(lambda: self.store.data["preferences"].update(snapshot_naming=dict(candidate)))
        except (OSError, ValueError) as exc:
            self._error(f"命名规则未能写入设置：{exc}")
        self._update_capture_name_preview()

    def _capture_objective_token(self, processed=None):
        processed = processed or self.current
        context = processed.frame.profile_context if processed else {}
        value = context.get("magnification")
        if value is None:
            identity = context.get("objective_id") or context.get("objective_name")
            objective = self.store.objective(identity) if identity else self.store.objective(self.store.data["selected_objective"])
            value = objective["magnification"] if objective else None
        try:
            value = float(value)
            if math.isfinite(value) and value > 0:
                return f"{value:g}x"
        except (ValueError, TypeError):
            pass
        return "未知物镜"

    def _update_capture_name_preview(self):
        try:
            if self._naming_error:
                raise ValueError(self._naming_error)
            now = datetime.now()
            sequence = effective_sequence(self.snapshot_naming, now)
            stem = render_name(self.snapshot_naming, now, self._capture_objective_token(), sequence)
            self.capture_name_preview.setText("文件名预览：\n" + stem + "." + self.snapshot_format)
            self.capture_name_preview.setStyleSheet("")
            with QSignalBlocker(self.capture_next_sequence):
                self.capture_next_sequence.setValue(sequence)
            valid = True
        except ValueError as exc:
            self.capture_name_preview.setText("命名规则需要修改：\n" + str(exc))
            self.capture_name_preview.setStyleSheet("color: #ffd166;")
            valid = False
        self.naming_settings_preview.setText(self.capture_name_preview.text())
        self.naming_settings_preview.setStyleSheet(self.capture_name_preview.styleSheet())
        if hasattr(self, "quick_save_action"):
            self.quick_save_action.setEnabled(valid)

    def _advance_capture_sequence(self, used_settings, sequence, now):
        # Editing the rule while saving takes precedence over this older request.
        if self.snapshot_naming != used_settings:
            return
        updated = dict(used_settings, sequence_next=sequence + used_settings["sequence_step"],
                       sequence_date=now.strftime("%Y-%m-%d"))
        self.snapshot_naming = updated
        with QSignalBlocker(self.capture_next_sequence):
            self.capture_next_sequence.setValue(updated["sequence_next"])
        try:
            self.store.transaction(lambda: self.store.data["preferences"].update(snapshot_naming=dict(updated)))
        except (OSError, ValueError) as exc:
            self._error(f"图像已保存，但下次序号未能写入设置：{exc}")
        self._update_capture_name_preview()

    def _choose_capture_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", str(self.capture_directory))
        if directory:
            self.capture_directory = Path(directory)
            self.capture_directory_label.setText(directory)
            try: self.store.transaction(lambda: self.store.data["preferences"].update(capture_directory=directory))
            except (OSError, ValueError) as exc: self._error(str(exc))

    def _automatic_path(self, suffix, prefix=""):
        self.capture_directory.mkdir(parents=True, exist_ok=True)
        return self.capture_directory / (prefix + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + suffix)

    def _snap(self):
        self._capture_image(quick=True)

    def _toggle_recording(self, active):
        if not active:
            if self.recorder is not None: self.recorder.stop()
            return
        if self.current is None or self.capture is None:
            with QSignalBlocker(self.record_action): self.record_action.setChecked(False)
            self.statusBar().showMessage("请先连接相机并开始预览。", 5000)
            return
        if self._profile_requests or self.current.frame.profile_context.get("generation", 0) < self._minimum_frame_generation:
            with QSignalBlocker(self.record_action): self.record_action.setChecked(False)
            self.statusBar().showMessage("请等待相机参数回读及新画面到达，再开始录像。", 5000)
            return
        if self.recorder is not None: return
        path = self._automatic_path(".avi")
        size = (self.current.rgb.shape[1], self.current.rgb.shape[0])
        metadata = export_metadata(self.current, [], self.viewer.calibration, "preview-video")
        self.recorder = VideoRecorder(path, size, self.video_fps_box.value(), self.bridge.video_finished.emit, metadata)
        self.recorder.start()
        self.roi_group.setEnabled(False)
        self.record_action.setText("停止录像")
        self.recording_entry.setText("正在录像 · 查看 / 停止…")
        self.statusBar().showMessage(f"正在录像：{path}")

    def _video_finished(self, result):
        self.recorder = None
        self.roi_group.setEnabled(True)
        with QSignalBlocker(self.record_action): self.record_action.setChecked(False)
        self.record_action.setText("开始录像")
        self.recording_entry.setText("录制视频…")
        if result.startswith("ERROR:"): self._error(result)
        else: self.statusBar().showMessage(f"录像已保存：{result}", 8000)
        if self._closing: self.close()

    def _toggle_timelapse(self):
        if self.timelapse_timer.isActive(): self._stop_timelapse(); return
        if self.capture is None or self.current is None:
            self.statusBar().showMessage("请先连接相机并开始预览。", 5000)
            return
        if self._profile_requests or self.current.frame.profile_context.get("generation", 0) < self._minimum_frame_generation:
            self.statusBar().showMessage("请等待相机参数回读及新画面到达，再开始定时拍照。", 5000)
            return
        self._timelapse_done, self._timelapse_skipped, self._last_timed_frame = 0, 0, None
        self.timelapse_button.setText("停止定时拍照")
        self.timelapse_entry.setText("正在定时拍照 · 查看 / 停止…")
        self.timelapse_timer.start(round(self.interval_box.value() * 1000))
        self._timelapse_tick()

    def _stop_timelapse(self):
        if hasattr(self, "timelapse_timer"): self.timelapse_timer.stop()
        if hasattr(self, "timelapse_button"): self.timelapse_button.setText("开始定时拍照")
        if hasattr(self, "timelapse_entry"): self.timelapse_entry.setText("定时拍照…")

    def _timelapse_tick(self):
        if self.capture is None: self._stop_timelapse(); return
        _, frame = self.raw_buffer.get()
        if self._save_busy or frame is None or frame.frame_id == self._last_timed_frame:
            self._timelapse_skipped += 1
            self.timelapse_status.setText(f"已提交 {self._timelapse_done} 张 · 等待/跳过 {self._timelapse_skipped} 次")
            return
        settings = self.settings
        calibration = self.viewer.calibration
        raw_mode = self.timelapse_format.currentIndex() == 0
        path = self._automatic_path(".tiff" if raw_mode else ".png", "interval_")
        def save():
            rgb = decode_rgb(frame)
            processed = ProcessedFrame(frame, settings, render_rgb(rgb, frame.white_level, settings),
                                       histogram(rgb, frame.white_level), luminance_histogram(rgb, frame.white_level), (0, 0))
            if raw_mode: save_raw(path, processed, [], calibration)
            else:
                save_png(path, rgb_image(processed.rgb), export_metadata(processed, [], calibration, "timed-display"))
        self._last_timed_frame = frame.frame_id
        self._timelapse_done += 1
        self._submit_save(path, save)
        self.timelapse_status.setText(f"已提交 {self._timelapse_done} / {self.capture_count_box.value()} 张")
        if self._timelapse_done >= self.capture_count_box.value(): self._stop_timelapse()
