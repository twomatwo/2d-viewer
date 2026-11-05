"""Editors for objective identity and inspection of saved camera parameters."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
    QHeaderView, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout)

from .camera_profiles import automatic, differences


class ObjectivesDialog(QDialog):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("物镜与相机参数 · 管理物镜")
        self.resize(650, 520)
        layout = QVBoxLayout(self)
        description = QLabel("填写实验室实际使用的物镜，可使用相同倍率、不同名称。改名保留标定和方案。\n"
                             "取消“启用”可隐藏暂不用的物镜；移除的配置会保留在本机归档中。")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["物镜名称", "倍率", "启用"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(42)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for label, callback in (("添加", self._add), ("移除", self._remove),
                                ("上移", lambda: self._move(-1)), ("下移", lambda: self._move(1))):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #ffd166;")
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._populate(deepcopy(store.data["objective_entries"]))

    def _populate(self, entries):
        self.table.setRowCount(0)
        for entry in entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(entry["name"])
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.table.setItem(row, 0, item)
            magnification = QDoubleSpinBox()
            magnification.setRange(0.001, 10000)
            magnification.setDecimals(3)
            magnification.setSuffix(" ×")
            magnification.setValue(entry["magnification"])
            self.table.setCellWidget(row, 1, magnification)
            enabled = QTableWidgetItem()
            enabled.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            enabled.setCheckState(Qt.CheckState.Checked if entry.get("enabled", True) else Qt.CheckState.Unchecked)
            self.table.setItem(row, 2, enabled)

    def entries(self):
        return [{"id": self.table.item(row, 0).data(Qt.ItemDataRole.UserRole),
                 "name": self.table.item(row, 0).text().strip(),
                 "magnification": self.table.cellWidget(row, 1).value(),
                 "enabled": self.table.item(row, 2).checkState() == Qt.CheckState.Checked}
                for row in range(self.table.rowCount())]

    def _add(self):
        entries = self.entries()
        entries.append({"id": uuid4().hex, "name": "新物镜", "magnification": 1.0, "enabled": True})
        self._populate(entries)
        self.table.setCurrentCell(len(entries) - 1, 0)
        self.table.editItem(self.table.item(len(entries) - 1, 0))

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _move(self, offset):
        row, entries = self.table.currentRow(), self.entries()
        if 0 <= row + offset < len(entries) and row >= 0:
            entries[row], entries[row + offset] = entries[row + offset], entries[row]
            self._populate(entries)
            self.table.setCurrentCell(row + offset, 0)

    def accept(self):
        try:
            entries = self.entries()
            self.store._validate_objectives(entries)
            removed = {entry["id"] for entry in self.store.data["objective_entries"]} - {entry["id"] for entry in entries}
            if removed and QMessageBox.question(self, "移除物镜？",
                    "这些物镜将从选择列表移除，关联标定和方案保留在本机归档中。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.store.update_objectives(entries)
        except (OSError, ValueError) as exc:
            self.message.setText(str(exc))
            return
        super().accept()


class ConfigurationDialog(QDialog):
    def __init__(self, parent, saved: dict | None, actual: dict):
        super().__init__(parent)
        self.setWindowTitle("相机参数核对 · 最近一次回读")
        self.resize(820, 600)
        layout = QVBoxLayout(self)
        changed = differences(saved, actual) if saved else []
        label = QLabel((f"与保存方案相比，{len(changed)} 项设置不同。自动产生的数值不计为修改。" if saved else
                        "当前物镜尚未保存方案。下列为最近一次回读的相机参数。") +
                       f"\n相机：{actual.get('camera', {}).get('model', '—')} · {actual.get('camera', {}).get('serial', '—')}")
        label.setWordWrap(True)
        layout.addWidget(label)
        rows = {}
        for side, payload in ((0, saved or {}), (1, actual)):
            for name, value in payload.get("settings", {}).items():
                rows.setdefault(name, [None, None])[side] = value
            for entry in payload.get("features", []):
                name = entry["name"]
                if entry.get("selectors"):
                    name += " [" + ", ".join(f"{k}={v}" for k, v in entry["selectors"]) + "]"
                rows.setdefault(name, [None, None])[side] = entry["value"]
        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["相机参数", "已保存", "最近回读", "说明"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, (name, values) in enumerate(sorted(rows.items())):
            base = name.split(" [")[0]
            annotation = "自动读数，不参与修改判定" if automatic(base, actual.get("settings", {})) else ""
            for column, value in enumerate((name, *values, annotation)):
                table.setItem(row, column, QTableWidgetItem("—" if value is None else str(value)))
        layout.addWidget(table)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
