"""Objective-scoped camera presets; all SDK work stays on CaptureWorker."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QInputDialog, QLabel,
    QMenu, QMessageBox, QPushButton, QToolButton)

from .camera_profiles import LIBRARY_SCHEMA, configuration, differences, migrate_preset
from .models import Calibration
from .panels import button_row
from .profile_dialogs import ConfigurationDialog, ObjectivesDialog
from .settings_store import sampling_context


class CameraProfileControls:
    def _init_profile_state(self):
        objective = self.store.objective(self.store.data["selected_objective"])
        self._profile_key = (objective["id"], self.store.objective_library(objective["id"])["active"])
        self._pending_profile_key = None
        self._profile_actual = {}
        self._profile_requests = {}
        self._profile_verified = False
        self._profile_failure = ""
        self._session_drafts = {}
        self._minimum_frame_generation = 0
        self._automatic_once_pending = set()

    def _build_profile_ui(self):
        form = self.camera_profiles_section.form
        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip("保存当前物镜使用的相机成像参数，例如曝光、增益、白平衡、像素格式和 ROI；不包含电脑处理参数。")
        self.profile_combo.currentIndexChanged.connect(self._profile_combo_changed)
        form.addRow("相机方案", self.profile_combo)
        self.profile_scope_label = QLabel("仅保存相机参数，不包含电脑处理。")
        self.profile_scope_label.setObjectName("muted")
        self.profile_scope_label.setWordWrap(True)
        form.addRow(self.profile_scope_label)
        self.save_profile_action = QAction("保存修改", self)
        self.save_profile_action.triggered.connect(self._save_preset)
        self.restore_profile_action = QAction("恢复已保存", self)
        self.restore_profile_action.triggered.connect(self._load_preset)
        save = QToolButton()
        save.setDefaultAction(self.save_profile_action)
        restore = QToolButton()
        restore.setDefaultAction(self.restore_profile_action)
        form.addRow(button_row(save, restore))
        self.profile_menu = self.camera_settings_menu
        self.profile_menu.addAction("另存为相机方案…", lambda: self._save_preset(as_new=True))
        self.profile_menu.addAction("核对全部相机参数…", self._inspect_camera_profile)
        self.recover_profile_action = self.profile_menu.addAction("恢复上次临时调整", self._recover_profile_draft)
        self.profile_menu.addAction("删除当前方案…", self._delete_camera_profile)
        self.profile_menu.addSeparator()
        self.profile_menu.addAction("导出当前方案…", lambda: self._export_preset("profile"))
        self.profile_menu.addAction("导出当前物镜全部方案…", lambda: self._export_preset("objective"))
        self.profile_menu.addAction("导出全部物镜与相机方案…", lambda: self._export_preset("all"))
        self.profile_menu.addAction("载入相机参数文件…", self._import_preset)
        self.objective_status_button = QPushButton()
        self.objective_status_button.setToolTip("打开物镜与标定设置，选择或校准当前物镜")
        self.objective_status_button.clicked.connect(self._show_calibration_settings)
        form.addRow(self.objective_status_button)
        self.profile_status = QLabel()
        self.profile_status.setWordWrap(True)
        form.addRow(self.profile_status)
        self._refresh_profile_ui()

    def _key_string(self, key=None):
        return json.dumps(key or self._profile_key, ensure_ascii=False, separators=(",", ":"))

    def _saved_profile(self, key=None):
        identity, name = key or self._profile_key
        objective = self.store.objective(identity)
        return self.store.objective_library(identity)["schemes"].get(name) if objective else None

    def _context_for_profile(self, key, payload=None):
        objective = self.store.objective(key[0])
        return {"objective_id": objective["id"], "objective_name": objective["name"],
            "magnification": objective["magnification"], "profile_name": key[1],
            "revision": (payload or {}).get("revision"), "saved": payload is not None}

    def _profile_busy(self):
        return any(info["kind"] != "control" for info in self._profile_requests.values())

    def _refresh_profile_ui(self):
        if not hasattr(self, "profile_combo"):
            return
        key = self._pending_profile_key or self._profile_key
        objective = self.store.objective(key[0])
        if objective is None:
            objective = self.store.objective(self.store.data["selected_objective"])
        self.camera_profiles_section.header.setText(f"物镜相机参数 · {objective['name']}")
        objective_options = [(item["name"], item["id"]) for item in self.store.data["objective_entries"]
                             if item.get("enabled", True)]
        self.objective_selector.configure(objective_options, objective["id"])
        library = self.store.objective_library(objective["id"])
        profile_options = []
        if not library["schemes"] or not key[1]:
            profile_options.append(("尚未保存方案" if not library["schemes"] else "未选择已保存方案", ""))
        profile_options.extend((name, name) for name in library["schemes"])
        with QSignalBlocker(self.profile_combo):
            existing = [(self.profile_combo.itemText(i), self.profile_combo.itemData(i))
                        for i in range(self.profile_combo.count())]
            if existing != profile_options:
                self.profile_combo.clear()
                for label, name in profile_options:
                    self.profile_combo.addItem(label, name)
            self.profile_combo.setCurrentIndex(max(0, self.profile_combo.findData(key[1])))
        saved = self._saved_profile(key)
        changed = differences(saved, self._profile_actual) if saved and self._profile_actual else []
        busy, online = bool(self._profile_requests), self.capture is not None and bool(self._profile_actual)
        if self._pending_profile_key is not None:
            status = f"正在切换至 {objective['name']}…"
        elif self._profile_failure:
            status = "应用失败 · 请核对"
        elif busy:
            status = "正在回读…" if self._profile_busy() else "正在应用…"
        elif not online:
            status = "待连接后应用" if saved else "未保存方案"
        elif not saved:
            status = "当前相机参数未保存"
        elif not self._profile_verified:
            status = "待应用 · 尚未核对"
        elif changed:
            status = "临时修改"
        else:
            status = "与已保存方案一致"
        self.profile_status.setText(status)
        self.profile_status.setStyleSheet("color: #ffd166;" if changed or self._profile_failure else "")
        self.profile_status.setToolTip("不同参数：" + ", ".join(changed[:30]) if changed else status)
        self.save_profile_action.setEnabled(online and not busy)
        self.restore_profile_action.setEnabled(bool(saved) and not busy)
        self.objective_selector.setEnabled(not self._profile_busy())
        self.profile_combo.setEnabled(not self._profile_busy() and bool(library["schemes"]))
        draft = self.store.data["profile_drafts"].get(self._key_string(key))
        self.recover_profile_action.setEnabled(bool(draft) and not busy)

    def _objective_button_clicked(self, identity):
        item = self.store.objective(identity)
        if item:
            self._objective_changed(item["name"])

    def _profile_combo_changed(self, index):
        name = self.profile_combo.itemData(index)
        if name:
            self._select_camera_scheme(name)

    def _objective_changed(self, name):
        objective = self.store.objective(name)
        if not objective or objective["id"] == self._profile_key[0]:
            return
        library = self.store.objective_library(objective["id"])
        self._request_profile_selection((objective["id"], library["active"]))

    def _select_camera_scheme(self, name):
        key = (self._profile_key[0], name)
        if key != self._profile_key:
            self._request_profile_selection(key)

    def _guard_profile_change(self):
        if self._save_busy:
            self.statusBar().showMessage("请等待图像保存完成，再切换物镜或方案。", 5000)
            self._refresh_profile_ui()
            self._refresh_objectives()
            return False
        if self._profile_requests:
            self.statusBar().showMessage("请等待相机参数应用和回读完成。", 5000)
            self._refresh_profile_ui()
            self._refresh_objectives()
            return False
        if self.recorder is not None or self.timelapse_timer.isActive():
            self.statusBar().showMessage("请先停止录像或定时拍照，再切换物镜和相机方案。", 6000)
            self._refresh_profile_ui()
            self._refresh_objectives()
            return False
        return True

    def _confirm_new_view(self, callback):
        self._refresh_profile_ui()
        self._refresh_objectives()
        if not self.viewer.annotations:
            callback()
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("切换前保留当前测量与标注")
        dialog.setText("切换将开始新的视野，当前测量线和标注不能沿用新物镜的标定。")
        save = dialog.addButton("保存图像后切换…", QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("不保留并切换", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        if dialog.clickedButton() == save:
            self._capture_image(after_saved=callback)
        elif dialog.clickedButton() == discard:
            callback()
        else:
            self._refresh_profile_ui()

    def _stash_profile_draft(self):
        if not self._profile_actual or not self._profile_verified:
            return
        saved, key = self._saved_profile(), self._key_string()
        dirty = not saved or bool(differences(saved, self._profile_actual))
        if dirty:
            if self._session_drafts.get(key) != self._profile_actual:
                self._session_drafts[key] = deepcopy(self._profile_actual)
        else:
            self._session_drafts.pop(key, None)

        drafts = self.store.data["profile_drafts"]
        if (dirty and drafts.get(key) == self._profile_actual) or (not dirty and key not in drafts):
            return
        def update():
            if dirty:
                self.store.data["profile_drafts"][key] = deepcopy(self._profile_actual)
            else:
                self.store.data["profile_drafts"].pop(key, None)
        try:
            self.store.transaction(update)
        except (OSError, ValueError) as exc:
            self._error(f"临时相机参数保存失败：{exc}")

    def _request_profile_selection(self, key, restore=False, recover=False):
        if not self._guard_profile_change():
            return
        if not restore and not recover:
            self._stash_profile_draft()
        saved = self._saved_profile(key)
        payload = saved
        if recover:
            payload = self.store.data["profile_drafts"].get(self._key_string(key))
        elif not restore:
            payload = self._session_drafts.get(self._key_string(key), saved)
        changed_view = key[0] != self._profile_key[0]
        if payload and self._profile_actual:
            geometry = ("OffsetX", "OffsetY", "Width", "Height", "BinningHorizontal", "BinningVertical",
                        "DecimationHorizontal", "DecimationVertical")
            changed_view |= any(payload["settings"].get(name) != self._profile_actual.get("settings", {}).get(name)
                                for name in geometry if name in payload["settings"])
        def apply():
            self._apply_profile_selection(key, payload, saved, new_view=changed_view)
        if changed_view and key[0] == self._profile_key[0]:
            self._confirm_new_view(apply)
        else:
            apply()

    def _apply_profile_selection(self, key, payload, saved, *, new_view=False):
        if self.capture is None:
            self._commit_profile_selection(key)
            self._profile_verified = False
            self.statusBar().showMessage("已选择相机方案，连接相机后回读并应用；离线图像保持原标定。", 6000)
            return
        current_camera = self._profile_actual.get("camera", {})
        target_camera = (payload or {}).get("camera", {})
        if payload and (not target_camera.get("serial") or target_camera.get("serial") != current_camera.get("serial")):
            answer = QMessageBox.question(self, "核对方案所属相机",
                f"方案相机：{target_camera.get('model') or '旧方案，型号未记录'} / {target_camera.get('serial') or '未记录'}\n"
                f"当前相机：{current_camera.get('model', '—')} / {current_camera.get('serial', '—')}\n"
                "是否尝试载入相机参数？标定不会从参数文件载入，参数不兼容会明确报告。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self._refresh_profile_ui()
                return
        self._pending_profile_key = key
        if new_view:
            preserve = key[0] != self._profile_key[0]
            self._new_source(preserve_annotations=preserve)
            if preserve and self.viewer.annotations:
                self.statusBar().showMessage("已保留原标注，请核对位置；原测量保留创建时的倍率和标定。", 7000)
        token = uuid4().hex
        self._profile_requests[token] = {"kind": "select", "key": key, "baseline": saved}
        self.viewer.measurement_enabled = False
        self._profile_failure = ""
        context = self._context_for_profile(key, saved)
        try:
            if payload:
                self.capture.configure_profile(payload, context, token, baseline=saved)
            else:
                self.capture.configure_context(context, None, token)
        except (ValueError, RuntimeError) as exc:
            self._profile_requests.pop(token, None)
            self._pending_profile_key = None
            self._profile_verified = False
            self._profile_failure = str(exc)
            self._error(f"物镜切换请求失败：{exc}")
            self._apply_selected_calibration()
        self._refresh_profile_ui()

    def _commit_profile_selection(self, key):
        objective = self.store.objective(key[0])
        def update():
            self.store.data["selected_objective"] = objective["name"]
            self.store.objective_library(key[0])["active"] = key[1]
        if (self.store.data["selected_objective"] != objective["name"] or
                self.store.objective_library(key[0])["active"] != key[1]):
            self.store.transaction(update)
        self._profile_key = key
        self._pending_profile_key = None
        self._refresh_objectives()
        self._refresh_profile_ui()
        self._apply_selected_calibration()

    def _profiles_connected(self):
        self._minimum_frame_generation = 0
        self._profile_verified = False
        self._profile_failure = ""
        self._apply_profile_selection(self._profile_key, self._saved_profile(), self._saved_profile())

    def _profiles_received(self, description):
        self._profile_actual = configuration(description)
        finished = {name for name in self._automatic_once_pending
                    if self._profile_actual["settings"].get(name) == "Off"}
        self._automatic_once_pending.difference_update(finished)
        if finished and self._profile_verified and not self._profile_requests:
            self._stash_profile_draft()
        self._refresh_profile_ui()

    def _profile_operation_finished(self, result):
        info = self._profile_requests.pop(result["request_id"], None)
        self._profile_actual = result["configuration"]
        self._minimum_frame_generation = result.get("context", {}).get("generation", self._minimum_frame_generation)
        if info is None:
            self._refresh_profile_ui()
            return
        if not result["ok"]:
            if info["kind"] == "select":
                self._pending_profile_key = None
            self._profile_failure = result["error"]
            self._profile_verified = False
            self._refresh_profile_ui()
            self._apply_selected_calibration()
            return
        self._profile_failure = ""
        if self.error_label.text().startswith("相机参数设置失败"):
            self.error_label.clear()
        try:
            if info["kind"] == "save":
                payload = result["configuration"]
                if not payload.get("complete"):
                    details = "\n".join(payload.get("unavailable", [])[:8])
                    if QMessageBox.question(self, "部分相机参数未能读取",
                            "以下参数未能完整读取。是否仍保存当前可读部分？\n" + details,
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                        self._refresh_profile_ui()
                        return
                old = self._key_string()
                saved = self.store.save_profile(self._profile_key[0], info["name"], payload, clear_drafts=[old])
                self._profile_key = (self._profile_key[0], info["name"])
                self._session_drafts.pop(old, None)
                token = uuid4().hex
                self._profile_requests[token] = {"kind": "context"}
                self.capture.configure_context(self._context_for_profile(self._profile_key, saved), saved, token)
                self.statusBar().showMessage("已保存当前物镜的相机参数，电脑处理未写入方案。", 7000)
            elif info["kind"] == "select":
                self._profile_verified = True
                self._commit_profile_selection(info["key"])
                self._stash_profile_draft()
            elif info["kind"] in ("control", "context"):
                self._profile_verified = True
                self._stash_profile_draft()
        except (OSError, ValueError) as exc:
            if info["kind"] == "select":
                self._pending_profile_key = None
            self._error(f"相机方案保存或选择失败：{exc}")
        self._refresh_profile_ui()

    def _save_preset(self, checked=False, *, as_new=False):
        if self.capture is None or not self._profile_actual:
            self.statusBar().showMessage("请先连接相机，保存实际回读的相机参数。", 6000)
            return
        if self._profile_requests:
            self.statusBar().showMessage("请等待相机参数回读完成，再保存。", 5000)
            return
        name = self._profile_key[1]
        if as_new or not name:
            name, ok = QInputDialog.getText(self, "保存相机参数方案", "当前物镜下的方案名称：", text="常规" if not name else name + " 副本")
            if not ok or not name.strip():
                return
            name = name.strip()
            if name in self.store.objective_library(self._profile_key[0])["schemes"]:
                self._error("当前物镜下已有同名方案，请使用其他名称。")
                return
        token = uuid4().hex
        self._profile_requests[token] = {"kind": "save", "name": name}
        self.capture.request_snapshot(token)
        self._refresh_profile_ui()

    def _load_preset(self):
        if self._saved_profile():
            self._request_profile_selection(self._profile_key, restore=True)

    def _recover_profile_draft(self):
        self._request_profile_selection(self._profile_key, recover=True)

    def _inspect_camera_profile(self):
        dialog = ConfigurationDialog(self, self._saved_profile(), self._profile_actual)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _delete_camera_profile(self):
        if not self._guard_profile_change() or not self._saved_profile():
            return
        if QMessageBox.question(self, "删除相机方案？", f"删除“{self._profile_key[1]}”？当前相机参数和物镜标定会保留。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        identity, name = self._profile_key
        def update():
            library = self.store.objective_library(identity)
            self.store.data.setdefault("deleted_profiles", []).append({"objective_id": identity,
                "name": name, "profile": library["schemes"].pop(name)})
            library["active"] = ""
        try:
            self.store.transaction(update)
            self._profile_verified = False
            self._profile_key = (identity, self.store.objective_library(identity)["active"])
            if self.capture is not None:
                token = uuid4().hex
                self._profile_requests[token] = {"kind": "context"}
                self.capture.configure_context(self._context_for_profile(self._profile_key), None, token)
            self._refresh_profile_ui()
        except (OSError, ValueError) as exc:
            self._error(str(exc))

    def _export_preset(self, scope="profile"):
        if not isinstance(scope, str):
            scope = "profile"
        content = self.store.export_profiles("all" if scope == "all" else self._profile_key[0])
        if scope == "profile":
            if not self._saved_profile():
                self.statusBar().showMessage("请先保存一个相机方案，再导出。", 5000)
                return
            library = content["objectives"][0]["profiles"]
            library["schemes"] = {self._profile_key[1]: deepcopy(self._saved_profile())}
        filename, _ = QFileDialog.getSaveFileName(self, "导出已保存的相机参数", "2d-viewer-camera-profiles.json", "JSON (*.json)")
        if filename:
            try:
                Path(filename).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                self._error(str(exc))

    def _import_preset(self):
        if not self._guard_profile_change():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "载入相机参数文件", "", "JSON (*.json)")
        if not filename:
            return
        try:
            path = Path(filename)
            if path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("参数文件超过 20 MiB")
            content = json.loads(path.read_text(encoding="utf-8"))
            if content.get("schema") == "labview.preset.v1":
                objective = deepcopy(self.store.objective(content.get("objective")) or self.store.objective(self._profile_key[0]))
                objective["profiles"] = {"active": path.stem, "schemes": {path.stem: migrate_preset(content)}}
                content = {"schema": LIBRARY_SCHEMA, "objectives": [objective]}
            elif content.get("schema") != LIBRARY_SCHEMA:
                raise ValueError("不是物镜相机参数文件")
            entries = content.get("objectives", [])
            summary = "\n".join(f"{entry.get('name', '?')}：" + ", ".join(entry.get("profiles", {}).get("schemes", {})) for entry in entries[:12])
            choice = QMessageBox(self)
            choice.setWindowTitle("载入相机参数库")
            choice.setText(summary + "\n\n只载入物镜和相机方案，不改电脑处理或标定；导入后需选择方案应用。")
            merge = choice.addButton("合并，同名方案覆盖", QMessageBox.ButtonRole.AcceptRole)
            duplicate = choice.addButton("全部另存为新物镜", QMessageBox.ButtonRole.ActionRole)
            cancel = choice.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            choice.setDefaultButton(cancel)
            choice.exec()
            if choice.clickedButton() not in (merge, duplicate):
                return
            self.store.import_profiles(content, duplicate=choice.clickedButton() == duplicate)
            self._profile_verified = False
            self._refresh_objectives()
            self._refresh_profile_ui()
            self.statusBar().showMessage("相机方案已载入参数库，尚未改变当前相机；选择方案或点击“恢复已保存”以应用。", 9000)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self._error(f"参数文件载入失败：{exc}")

    def _manage_objectives(self):
        if not self._guard_profile_change():
            return
        old_key = self._profile_key
        self._stash_profile_draft()
        dialog = ObjectivesDialog(self, self.store)
        try:
            if not dialog.exec():
                return
            objective = self.store.objective(old_key[0])
            if objective is None or not objective.get("enabled", True):
                target = self.store.objective(self.store.data["selected_objective"])
                self._profile_key = (target["id"], self.store.objective_library(target["id"])["active"])
                self._profile_verified = False
                self._refresh_objectives()
                self._refresh_profile_ui()
                self.statusBar().showMessage("当前物镜已移除或停用；请核对实际物镜，连接后点击“恢复已保存”应用所选方案。", 9000)
            else:
                self._refresh_objectives()
                self._refresh_profile_ui()
                if self.capture is not None:
                    token = uuid4().hex
                    self._profile_requests[token] = {"kind": "context"}
                    self.capture.configure_context(self._context_for_profile(old_key, self._saved_profile()), self._saved_profile(), token)
        finally:
            dialog.deleteLater()

    def _add_objective(self):
        self._manage_objectives()

    def _display_objective(self):
        context = self.current.frame.profile_context if self.current else {}
        identity = context.get("objective_id")
        objective = self.store.objective(identity) if identity else None
        return objective["name"] if objective else context.get("objective_name", self.store.data["selected_objective"])

    def _apply_selected_calibration(self):
        name = self._display_objective()
        self._sync_annotation_context()
        context = sampling_context(self.current.frame) if self.current is not None else None
        calibration = self.store.calibration(name, context)
        if self.current is not None and self.current.frame.capture_calibration:
            record = self.current.frame.capture_calibration
            try:
                calibration = self._file_calibration_override or Calibration(
                    float(record.get("units_per_pixel", 0)), str(record.get("unit", "µm")),
                    str(record.get("profile_name", name)))
            except (ValueError, TypeError, AttributeError):
                calibration = None
        if self._profile_failure:
            calibration = None
        self.viewer.set_calibration(calibration if calibration and self.current else Calibration())
        if self.current is None:
            message = f"{name} · 等待图像并核对相机/采样配置"
        elif self._profile_failure:
            message = "相机参数应用失败 · 标定暂停使用，请先核对相机设置"
        elif not calibration or calibration.units_per_pixel == 0:
            message = f"{name} 标定不匹配 · 长度显示为 px" if self.store.has_calibration(name) else f"{name} 未标定 · 长度显示为 px"
        else:
            message = f"当前画面：{name} · {calibration.units_per_pixel:.6g} {calibration.unit}/px"
        self.calibration_label.setText(message)
        self._refresh_objectives()

    def _refresh_objectives(self):
        context = sampling_context(self.current.frame) if self.current is not None else None
        with QSignalBlocker(self.objective_list):
            names = self.store.objective_names()
            existing = [self.objective_list.topLevelItem(i).text(0)
                        for i in range(self.objective_list.topLevelItemCount())]
            if existing != names:
                self.objective_list.clear()
                self.objective_list._selected = ""
                self.objective_list.addItems(names)
            for index in range(self.objective_list.topLevelItemCount()):
                row = self.objective_list.topLevelItem(index)
                entry = self.store.calibration_record(row.text(0), context) or self.store.calibration_record(row.text(0))
                row.setText(1, f"{entry['scale']:.6g} {entry['unit']}/px" if entry else "—")
                row.setText(2, "未标定" if not entry else "待核对" if context is None else
                            "已标定" if entry["context"] == context else "不匹配")
                row.setForeground(2, QColor("#9be2cf" if row.text(2) == "已标定" else "#ffd166"))
                row.setToolTip(1, row.text(1))
            selected = self.store.objective((self._pending_profile_key or self._profile_key)[0])
            self.objective_list.setCurrentText(selected["name"] if selected else self.store.data["selected_objective"])
        if hasattr(self, "objective_status_button"):
            scale = self.viewer.calibration
            self.objective_status_button.setText("标定：切换中" if self._pending_profile_key is not None else
                                                 "标定：已匹配" if scale.units_per_pixel else "标定：未匹配")
            self.objective_status_button.setToolTip(self.calibration_label.text())
