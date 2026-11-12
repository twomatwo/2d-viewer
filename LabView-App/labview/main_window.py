from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import monotonic
import logging
import math

from PySide6.QtCore import QObject, QSignalBlocker, QSaveFile, QIODevice, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QTreeWidgetItem, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSplitter, QTabWidget, QToolBar, QToolButton, QMenu, QVBoxLayout, QWidget)

from .acquisition import CaptureWorker
from .exporting import export_metadata, load_image, save_raw, save_png
from .histogram import HistogramWidget
from .models import Annotation, Calibration, DisplaySettings, Frame, LatestBuffer, ProcessedFrame
from .processing import DisplayWorker
from .panels import camera_panel, display_panel, annotation_panel, capture_panel, calibration_panel, button_row
from .dialogs import TaskDialog
from .controls import ClickToEditWheelGuard, ObjectiveSelector
from .controllers import CameraControls, ProfileControls, CaptureControls
from .profile_controls import CameraProfileControls
from .settings_store import SettingsStore, display_from_dict
from .viewer import ImageViewer, rgb_image, paint_annotations, paint_scale_bar, annotation_group_image, UNIT_TO_UM
from .capture_naming import available_capture_path, naming_settings, validate_filename, write_snapshot
from .shortcuts import ShortcutManager, ShortcutsDialog
from .recovery import AnnotationRecovery
from .image_assets import load_image_asset
from .image_annotation_dialog import ImageAnnotationDialog
from .annotation_icons import annotation_icon
from .app import default_data_directory
from . import APP_NAME, __version__


STYLE = """
QMainWindow, QWidget { background: #171e2b; color: #e4eaf4; font-family: 'Microsoft YaHei'; font-size: 13px; }
QToolBar { background: #202a3a; border: none; padding: 6px; spacing: 3px; }
QToolButton { padding: 7px 10px; border-radius: 5px; }
QToolButton:hover, QPushButton:hover { background: #344660; }
QToolButton:disabled { color: #65738b; }
QToolButton:checked { background: #3a6980; color: #b6f4e1; }
QPushButton { background: #2b3a51; border: 1px solid #3b4b65; padding: 7px 10px; border-radius: 5px; }
QPushButton#primary { background: #287968; border-color: #3b9d85; }
QPushButton:disabled { color: #65738b; background: #202938; border-color: #2a3548; }
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox { background: #101825; border: 1px solid #38465e; border-radius: 4px; padding: 6px; }
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #69d3b5; }
QDoubleSpinBox:disabled, QSpinBox:disabled, QComboBox:disabled { color: #65738b; background: #202938; }
QComboBox QAbstractItemView { background: #1e2b3c; selection-background-color: #365e74; }
QGroupBox { border: 1px solid #354156; border-radius: 7px; margin-top: 16px; padding: 13px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0px 4px; color: #b3c0d6; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 10px 8px; background: #202b3b; border-bottom: 2px solid #202b3b; }
QTabBar::tab:selected { color: #9aead3; border-bottom-color: #69d3b5; }
QListWidget, QTreeWidget { background: #111927; border: 1px solid #354156; border-radius: 4px; }
QTreeWidget::item { padding: 5px 2px; }
QTreeWidget::item:selected { background: #2c5063; }
QHeaderView::section { background: #202b3b; color: #b3c0d6; padding: 5px; border: none; }
QListWidget::item { padding: 8px; }
QListWidget::item:selected { background: #2c5063; }
QStatusBar { background: #101825; color: #9daec6; }
QLabel#muted { color: #92a3bc; font-size: 11px; }
QLabel#title { color: #f1f6ff; font-size: 20px; font-weight: 600; }
QLabel#badge { background: #263d43; color: #9be2cf; border-radius: 4px; padding: 5px 10px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #141c29; width: 10px; }
QScrollBar::handle:vertical { background: #394961; min-height: 25px; border-radius: 4px; }
QSplitter::handle { background: #111825; width: 4px; }
QCheckBox { spacing: 7px; }
"""


class Bridge(QObject):
    error = Signal(str)
    saved = Signal(str)
    video_finished = Signal(str)
    frame_ready = Signal()


def note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def spinbox(minimum: float, maximum: float, value: float, decimals: int = 2) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setKeyboardTracking(False)
    return box


class MainWindow(AnnotationRecovery, CameraControls, CameraProfileControls, ProfileControls, CaptureControls, QMainWindow):
    def __init__(self, settings_path: Path | None = None, data_directory: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__} · BFS-U3-51S5C")
        self.resize(1480, 940)
        self.setMinimumSize(1050, 700)
        self.setStyleSheet(STYLE)
        self.data_directory = Path(data_directory or (settings_path.parent if settings_path else default_data_directory()))
        self.store = SettingsStore(settings_path or self.data_directory / "settings" / "labview.json")
        self._init_profile_state()
        self.capture_directory = Path(self.store.data["preferences"].get("capture_directory", str(self.data_directory / "captures")))
        self.snapshot_format = self.store.data["preferences"].get("snapshot_format", "png")
        if self.snapshot_format not in ("png", "tiff"):
            self.snapshot_format = "png"
        self.snapshot_annotations = self.store.data["preferences"].get("snapshot_annotations", True)
        self._capture_settings_warning = ""
        try:
            self.snapshot_naming = naming_settings(self.store.data["preferences"].get("snapshot_naming"))
        except ValueError as exc:
            self.snapshot_naming = naming_settings()
            self._capture_settings_warning = f"快速保存命名规则无效，已使用默认规则：{exc}"
        self._naming_error = ""
        self._resume_after_calibration = False
        self.recorder = None
        self.discovery = None
        self._updating_camera = False
        self._pending_modes = {}
        self._timelapse_done = 0
        self._timelapse_skipped = 0
        self._last_timed_frame = None
        self.raw_buffer: LatestBuffer[Frame] = LatestBuffer()
        self.display_buffer: LatestBuffer[ProcessedFrame] = LatestBuffer()
        self.bridge = Bridge(self)
        self.bridge.error.connect(self._error)
        self.bridge.saved.connect(self._saved)
        self.bridge.video_finished.connect(self._video_finished)
        self.processor = DisplayWorker(self.raw_buffer, self.display_buffer, self.bridge.error.emit,
                                       self.bridge.frame_ready.emit)
        self.bridge.frame_ready.connect(self._refresh, Qt.ConnectionType.QueuedConnection)
        self.capture: CaptureWorker | None = None
        self.current: ProcessedFrame | None = None
        self.settings = DisplaySettings()
        self._output_version = -1
        self._geometry = None
        self._file_calibration_override = None
        self._frozen = False
        self._frozen_frame: Frame | None = None
        self._source_min_version = 0
        self._last_calibration = None
        self._closing = False
        self._save_busy = False
        self._pending_saves = 0
        self._save_callbacks = {}
        self._snapshot_sequences = {}
        self._fps_start = monotonic()
        self._displayed = 0
        self._last_displayed_identity = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="save-image")
        self._camera_description = {}
        self._annotation_color = "#6eacff"
        self._pending_recovery = None
        self._start_recovery_view()
        self.viewer = ImageViewer()
        self.viewer.document_changed.connect(self._journal_annotations)
        self.viewer.annotations_changed.connect(self._annotations_changed)
        self.viewer.selection_changed.connect(self._sync_annotation_selection)
        self.viewer.calibration_requested.connect(self._calibrate)
        self.viewer.calibration_cancelled.connect(lambda: self.tool_actions["pan"].trigger())
        self.viewer.annotation_selected.connect(self._show_selected_annotation)
        self.viewer.text_requested.connect(self._add_text)
        self.viewer.cursor_info.connect(lambda text: self.cursor_label.setText(text))
        self.viewer.zoom_changed.connect(self._update_zoom)
        self.viewer.frame_presented.connect(self._frame_painted)
        try:
            self.viewer.set_scale_bar_length(float(self.store.data["preferences"].get("scale_bar_length_um", 100.0)))
        except (ValueError, TypeError):
            self.viewer.set_scale_bar_length(100.0)
        self._build_ui()
        self.calibration_dialog = TaskDialog(self, "物镜标定", "各物镜分别保存标定。设置不会修改已有测量；新测量使用当前画面的匹配标定。")
        calibration_scroll = self._scroll(calibration_panel(self))
        calibration_scroll.setMinimumHeight(430)
        self.calibration_dialog.form.addRow(calibration_scroll)
        self.calibration_dialog.resize(560, 650)
        self._build_toolbar()
        self._build_profile_ui()
        self.settings_menu.addAction("恢复自动保存的标注…", self._restore_annotations)
        self.shortcuts = ShortcutManager(self, self.shortcut_actions,
            self.store.data["preferences"].get("shortcuts"))
        self._refresh_shortcut_hints()
        self._update_snapshot_summary()
        self._update_scale_bar_button()
        self.wheel_guard = ClickToEditWheelGuard(self)
        self.timelapse_timer = QTimer(self)
        self.timelapse_timer.timeout.connect(self._timelapse_tick)
        self._restore_preferences()
        self._apply_selected_calibration()
        self._set_camera_controls(False)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()
        self.naming_preview_timer = QTimer(self)
        self.naming_preview_timer.setInterval(1000)
        self.naming_preview_timer.timeout.connect(self._update_capture_name_preview)
        self.naming_preview_timer.start()
        self.processor.start()
        if self.store.error:
            self._error(self.store.error)
        elif self._capture_settings_warning:
            self._error(self._capture_settings_warning)
        elif any((self.data_directory / "recovery").glob("*.json")):
            self.statusBar().showMessage("有自动保存的标注视野，可从“设置 → 恢复自动保存的标注…”打开。", 15000)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 0)
        header = QHBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        header.addWidget(title)
        subtitle = QLabel("显微成像 · 标记 · 测量")
        subtitle.setObjectName("muted")
        header.addWidget(subtitle)
        header.addStretch()
        self.processing_state_label = QLabel("电脑处理：彩色")
        self.processing_state_label.setObjectName("muted")
        header.addWidget(self.processing_state_label)
        self.source_badge = QLabel("尚未连接")
        self.source_badge.setObjectName("badge")
        header.addWidget(self.source_badge)
        layout.addLayout(header)
        self.tool_hint = QLabel()
        self.tool_hint.setObjectName("muted")
        self.tool_hint.setWordWrap(True)
        self.tool_hint.setContentsMargins(2, 2, 2, 6)
        layout.addWidget(self.tool_hint)
        splitter = QSplitter()
        image_column = QWidget()
        image_layout = QVBoxLayout(image_column)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(5)
        image_layout.addWidget(self.viewer, 1)
        self.frame_parameters_label = QLabel("图像 — · 曝光 — · 增益 —")
        self.frame_parameters_label.setWordWrap(True)
        image_info = QHBoxLayout()
        image_info.addWidget(self.frame_parameters_label, 1)
        self.cursor_label = QLabel("坐标 / 显示 RGB：将鼠标移到图像上查看")
        image_layout.addWidget(self.cursor_label)
        self.camera_fps_label = QLabel("相机 —")
        self.display_fps_label = QLabel("显示 —")
        image_info.addWidget(self.camera_fps_label)
        image_info.addSpacing(12)
        image_info.addWidget(self.display_fps_label)
        image_layout.insertLayout(1, image_info)
        self.camera_fps_label.setToolTip("相机实际采集帧率")
        self.display_fps_label.setToolTip("实际显示的新画面帧率；冻结时为 0")
        splitter.addWidget(image_column)
        tabs = QTabWidget()
        self.tabs = tabs
        tabs.setMinimumWidth(340)
        tabs.setMaximumWidth(490)
        tabs.addTab(self._scroll(self._camera_panel()), "相机参数")
        tabs.addTab(self._scroll(self._display_panel()), "电脑处理")
        tabs.addTab(self._scroll(annotation_panel(self)), "标注")
        tabs.addTab(self._scroll(capture_panel(self)), "拍摄保存")
        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1050, 365])
        layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def _camera_panel(self) -> QWidget:
        return camera_panel(self)

    def _display_panel(self) -> QWidget:
        return display_panel(self)

    def _build_toolbar(self) -> None:
        file_bar = QToolBar("常用操作", self)
        file_bar.setObjectName("mainToolbar")
        file_bar.setMovable(False)
        file_bar.setStyleSheet("QToolButton { padding: 7px 4px; } QPushButton { padding: 7px 6px; }")
        self.addToolBar(file_bar)
        self.menuBar().hide()
        self.settings_menu = QMenu("设置", self)
        self.objective_settings_menu = self.settings_menu.addMenu("物镜与标定")
        self.objective_settings_menu.addAction("管理物镜…", self._manage_objectives)
        self.objective_settings_menu.addAction("物镜标定…", self._show_calibration_settings)
        self.camera_settings_menu = self.settings_menu.addMenu("相机与方案")
        self.camera_settings_menu.addAction("设备设置…", self.device_settings_dialog.reveal)
        self.camera_settings_menu.addSeparator()
        naming_menu = self.settings_menu.addMenu("保存与命名")
        naming_menu.addAction("命名规则…", self.naming_settings_dialog.reveal)
        self.settings_menu.addAction("快捷键设置…", self._configure_shortcuts)
        self.settings_button = QToolButton()
        self.settings_button.setText("设置")
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.settings_button.setMenu(self.settings_menu)
        file_bar.addWidget(self.settings_button)
        file_bar.addSeparator()
        file_bar.addWidget(QLabel("物镜"))
        self.objective_selector = ObjectiveSelector(self)
        self.objective_selector.objective_clicked.connect(self._objective_button_clicked)
        file_bar.addWidget(self.objective_selector)
        file_bar.addSeparator()
        open_action = file_bar.addAction("打开图像…", self._open_image)
        self.snapshot_action = file_bar.addAction("拍照", self._capture_image)
        self.snapshot_action.setToolTip("保存点击时的这一帧；填写文件名和保存位置，格式及标注遵循右侧“拍摄保存”设置")
        self.quick_save_action = file_bar.addAction("快速保存", self._snap)
        quick = self.quick_save_action
        raw = QAction("选择原始 TIFF 后拍照", self)
        raw.triggered.connect(self._capture_raw_image)
        self.record_action = QAction("开始录像", self)
        self.record_action.setToolTip("按拍摄保存页设置的帧率开始或停止录像")
        self.record_action.setCheckable(True)
        self.record_action.toggled.connect(self._toggle_recording)
        self.record_button.setDefaultAction(self.record_action)
        file_bar.addSeparator()
        self.freeze_action = QAction("冻结画面", self)
        self.freeze_action.setCheckable(True)
        self.freeze_action.setToolTip("冻结或恢复显示；冻结时相机继续采集")
        self.freeze_action.toggled.connect(self._freeze)
        file_bar.addAction(self.freeze_action)
        fit = file_bar.addAction("适应窗口", self.viewer.fit_image)
        self.shortcut_actions = {"open": open_action, "snapshot": self.snapshot_action,
            "quick": quick, "raw": raw, "record": self.record_action,
            "freeze": self.freeze_action, "fit": fit}
        for name, label, callback in (
            ("actual_size", "原始像素比例 100%", self.viewer.actual_size),
            ("zoom_in", "放大", lambda: self.viewer.zoom_by(1.2)),
            ("zoom_out", "缩小", lambda: self.viewer.zoom_by(1 / 1.2))):
            action = QAction(label, self)
            action.triggered.connect(callback)
            self.shortcut_actions[name] = action
        self.zoom_button = QPushButton("缩放 —")
        self.zoom_button.setToolTip("点击输入显示缩放比例；滚轮缩放时同步更新，不改变测量结果")
        self.zoom_button.setEnabled(False)
        self.zoom_button.clicked.connect(self._choose_zoom)
        file_bar.addWidget(self.zoom_button)
        file_bar.addSeparator()
        group = QActionGroup(self)
        group.setExclusive(True)
        self.tool_actions = {}
        hints = {
            "pan": "移动视野：按住左键拖动，移动放大后的图像；滚轮缩放，按住中键也可移动。",
            "select": "选择标注：Ctrl＋单击多选、拖空白框选；拖动移动，角点等比例缩放，上方圆点旋转。",
            "measure": "测量长度：左键拖出线段；结果记录当前画面的物镜和标定，切换物镜后保持原值。",
            "calibrate": "校准物镜：画面已暂停。沿标尺上一段已知长度拖线，再输入实际长度。",
            "marker": "标记点：点击要标记的位置，十字中心就是点击位置；旁边自动显示序号。",
            "line": "线条标注：按住左键拖出说明线；测量长度请使用“测量长度”。",
            "rect": "矩形框：按住左键从一个角拖到对角，框出需要说明的区域。",
            "ellipse": "椭圆框：按住左键拖动，圈出需要说明的区域。",
            "freehand": "自由画笔：按住左键绘制，松开完成。",
            "polygon": "测量面积与平均 RGB：左键逐点圈选多边形，右键或双击完成；RGB 取完成时底图的显示像素（0–255，含边界），不含叠加标注；结果固定保存，Esc 取消。",
            "angle": "测量角度：依次点击第一条边上的点、顶点、第二条边上的点；显示 0°–180° 夹角。右键或 Esc 取消。",
            "polygon_shape": "多边形标注：左键逐点添加，右键完成且不添加点，双击添加最后一点并完成；只绘制轮廓，Esc 取消。",
            "text": "文字说明：点击文字放置位置，再输入说明内容。"}
        self.tool_hints = hints
        for name, label in (("pan", "移动视野"), ("select", "选择标注"),
                            ("measure", "测量长度"), ("calibrate", "用标尺校准物镜"),
                            ("marker", "标记点"), ("line", "线条"), ("rect", "矩形"),
                            ("ellipse", "椭圆"), ("freehand", "画笔"), ("polygon", "测量面积"),
                            ("angle", "测量角度"), ("polygon_shape", "多边形"), ("text", "文字")):
            action = QAction(label, self)
            if name not in ("pan", "calibrate"):
                action.setIcon(annotation_icon(name))
            action.setCheckable(True)
            action.setChecked(name == "pan")
            action.triggered.connect(lambda checked=False, tool=name: self._activate_tool(tool))
            action.setToolTip(hints[name])
            group.addAction(action)
            if name in ("pan", "select"):
                file_bar.addAction(action)
                file_bar.widgetForAction(action).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self.tool_actions[name] = action
            if name != "calibrate":
                self.shortcut_actions["tool_" + name] = action
        self.import_image_action = QAction("图片…", self)
        self.import_image_action.setToolTip("导入 PNG、JPG 等图片作为叠加标注，可设置不透明度、缩放和旋转")
        self.import_image_action.triggered.connect(self._import_image_annotation)
        self.shortcut_actions["import_image"] = self.import_image_action
        self.import_image_action.setIcon(annotation_icon("image"))
        self.import_image_button.setDefaultAction(self.import_image_action)
        for name, button in self.annotation_tool_buttons.items():
            button.setDefaultAction(self.tool_actions[name])
        self._update_annotation_color_button()
        for name, title, callback, button in (
                ("group", "组合", self.viewer.group_selected, self.group_button),
                ("ungroup", "取消组合", self.viewer.ungroup_selected, self.ungroup_button)):
            action = QAction(title, self)
            action.setIcon(annotation_icon(name))
            action.triggered.connect(callback)
            button.setDefaultAction(action)
            self.shortcut_actions[name] = action
        undo = QAction(annotation_icon("undo"), "撤销", self)
        undo.triggered.connect(self.viewer.undo)
        self.shortcut_actions["undo"] = undo
        redo = QAction(annotation_icon("redo"), "重做", self)
        redo.triggered.connect(self.viewer.redo)
        self.shortcut_actions["redo"] = redo
        self.annotation_undo_button.setDefaultAction(undo)
        self.annotation_redo_button.setDefaultAction(redo)
        undo.setToolTip("撤销上一次标注操作")
        redo.setToolTip("重做已撤销的标注操作")
        def update_history():
            undo.setEnabled(bool(self.viewer._history))
            redo.setEnabled(bool(self.viewer._redo_history))
        self.viewer.annotations_changed.connect(update_history)
        update_history()
        delete = QAction("删除所选标注或测量", self.viewer)
        delete.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        delete.triggered.connect(self.viewer.delete_selected)
        self.viewer.addAction(delete)
        self.shortcut_actions["delete"] = delete
        cancel = QAction("取消当前绘制或校准", self)
        cancel.triggered.connect(self.viewer.cancel_drawing)
        self.shortcut_actions["cancel"] = cancel
        for name, action in self.shortcut_actions.items():
            if name != "delete":
                self.addAction(action)
        self._sync_annotation_selection()
        self._activate_tool("pan")

    def _configure_shortcuts(self):
        dialog = ShortcutsDialog(self, self.shortcuts.bindings, self._save_shortcuts)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _save_shortcuts(self, bindings):
        preferences = self.store.data["preferences"]
        previous = preferences.get("shortcuts")
        preferences["shortcuts"] = bindings
        try:
            self.store.save()
        except (OSError, ValueError):
            if previous is None:
                preferences.pop("shortcuts", None)
            else:
                preferences["shortcuts"] = previous
            raise
        self.shortcuts.apply(bindings)
        self._refresh_shortcut_hints()
        self._update_snapshot_summary()

    def _refresh_shortcut_hints(self):
        quick = self.shortcuts.text("quick")
        self.quick_capture_section.header.setText("快速保存" + (" · " + quick if quick else ""))
        self.quick_capture_help.setText((f"按 {quick}，" if quick else "绑定快速保存快捷键后，") +
            "按本页的格式、标注、目录及设置中的命名规则保存一张。")
        self.quick_save_action.setToolTip("按右侧“拍摄保存”的选项和“设置 → 保存与命名”的规则直接保存" + (f"（{quick}）" if quick else ""))
        self.tool_hint.setText(self._current_tool_hint())

    def _current_tool_hint(self):
        tool = self.viewer.tool
        text = self.tool_hints[tool]
        if hasattr(self, "shortcuts"):
            name = "tool_" + tool
            keys = self.shortcuts.text(name) if name in self.shortcuts.bindings else ""
            if keys:
                text += f" 快捷键：{keys}。"
            cancel = self.shortcuts.text("cancel")
            if cancel and tool in ("calibrate", "freehand"):
                text += f" 按 {cancel} 取消当前操作。"
            delete = self.shortcuts.text("delete")
            if delete and tool == "select":
                text += f" 在图像上按 {delete} 删除所选。"
        return text

    def _set_camera_controls(self, enabled: bool) -> None:
        self.camera_group.setEnabled(enabled)

    def _connect_camera(self) -> None:
        if self.capture is not None:
            return
        self._new_source()
        self.error_label.setText("")
        serial = self.serial_edit.text().strip() or self.source_combo.currentData() or ""
        self.capture = CaptureWorker(self.raw_buffer, serial=serial)
        self.capture.connected.connect(self._connected)
        self.capture.settings_updated.connect(self._camera_settings_updated)
        self.capture.operation_finished.connect(self._profile_operation_finished)
        self.capture.error.connect(self._error)
        self.capture.finished.connect(self._capture_finished)
        self.capture.configure_context(self._context_for_profile(self._profile_key), None)
        self.connect_button.setEnabled(False)
        self.source_combo.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.serial_edit.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        self.source_badge.setText("正在连接…")
        self.capture.start()




    def _disconnect_camera(self) -> None:
        if self.capture is not None:
            self.capture.request_stop()
            self.disconnect_button.setEnabled(False)
            self._set_camera_controls(False)
            self.source_badge.setText("正在停止…")

    def _capture_finished(self) -> None:
        self._stop_timelapse()
        if self.recorder is not None:
            self.recorder.stop()
        if self.capture is not None:
            self.capture.deleteLater()
            self.capture = None
        self.connect_button.setEnabled(True)
        self.source_combo.setEnabled(True)
        self.scan_button.setEnabled(True)
        self.serial_edit.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        self._set_camera_controls(False)
        self._profile_requests.clear()
        self._pending_profile_key = None
        self._profile_verified = False
        self._refresh_profile_ui()
        self.source_badge.setText("采集已停止 · 保留最后画面")
        self.camera_fps_label.setText("相机已停止")
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _frame_painted(self, processed: ProcessedFrame) -> None:
        frame = processed.frame
        identity = (frame.source, frame.frame_id, frame.received_ns)
        if identity != self._last_displayed_identity and not self._frozen:
            self._displayed += 1
        self._last_displayed_identity = identity

    def _update_frame_parameters(self, frame: Frame) -> None:
        values = frame.camera_settings
        exposure, gain = values.get("ExposureTime"), values.get("Gain")
        # A snapshot is not the exposure of this frame when auto control runs.
        exposure_label = (f"曝光 {exposure / 1000:.3f} ms" if exposure is not None else "曝光 —")
        gain_label = f"增益 {gain:.2f} dB" if gain is not None else "增益 —"
        if "ExposureTime" not in frame.per_frame_fields and values.get("ExposureAuto") != "Off":
            exposure_label += "（最近读值）"
        if "Gain" not in frame.per_frame_fields and values.get("GainAuto") != "Off":
            gain_label += "（最近读值）"
        height, width = frame.raw.shape[:2]
        self.frame_parameters_label.setText(
            f"{width}×{height} · {frame.pixel_format} · {exposure_label} · {gain_label}")

    def _new_source(self, *, preserve_annotations=False) -> None:
        self._start_recovery_view()
        self._pending_recovery = None
        self._file_calibration_override = None
        self.processor.freeze(None)
        self.raw_buffer.clear()
        self._source_min_version = self.raw_buffer.get()[0]
        self.display_buffer.clear()
        self.current, self._geometry = None, None
        self._last_displayed_identity = None
        self._frozen = False
        self._frozen_frame = None
        with QSignalBlocker(self.freeze_action):
            self.freeze_action.setChecked(False)
        self.freeze_action.setText("冻结画面")
        self._last_calibration = None
        self.viewer.measurement_enabled = False
        if preserve_annotations:
            self.viewer.cancel_drawing()
            self.viewer.set_calibration(Calibration())
        else:
            self.viewer.reset_document()

    def _refresh(self) -> None:
        self.processor.acknowledge()
        version, processed = self.display_buffer.get()
        if version != self._output_version and processed is not None:
            # A processing result from the previous source can finish after a clear.
            _, latest = self.raw_buffer.get()
            if latest is None or processed.frame.source != latest.source:
                return
            if self._frozen and processed.frame is not self._frozen_frame:
                return
            if not self._frozen and processed.version[0] < self._source_min_version:
                return
            if (self.capture is not None and not self._frozen and
                    processed.frame.profile_context.get("generation", 0) < self._minimum_frame_generation):
                return
            if processed.settings != self.settings:
                return
            self._output_version = version
            geometry = processed.frame.geometry + (processed.settings.flip_x, processed.settings.flip_y, processed.settings.rotation)
            geometry += (processed.frame.profile_context.get("objective_id"),)
            geometry_changed = self._geometry != geometry
            if self._geometry is not None and geometry_changed:
                self._start_recovery_view()
                if self._geometry[-1] != geometry[-1]:
                    self.viewer.cancel_drawing()
                    self.statusBar().showMessage("已保留原标注，请核对位置；原测量保留创建时的倍率和标定。", 7000)
                else:
                    self.viewer.reset_document()
                    self.statusBar().showMessage("图像区域或方向变化，已清除标记；匹配的倍率标定继续使用。", 6000)
            self._geometry = geometry
            if self.current is None:
                logging.getLogger(__name__).info("First displayed frame: source=%s format=%s shape=%s",
                                                 processed.frame.source, processed.frame.pixel_format,
                                                 processed.frame.raw.shape)
            self.current = processed
            self._sync_annotation_context()
            self.viewer.measurement_enabled = not self._profile_requests and not self._profile_failure
            self._update_processing_state()
            if geometry_changed:
                self._apply_selected_calibration()
            self.viewer.set_frame(processed.rgb, processed)
            if self._pending_recovery is not None:
                annotations, calibration, length = self._pending_recovery
                self._pending_recovery = None
                self._file_calibration_override = calibration
                self.viewer.annotations = annotations
                self.viewer.set_scale_bar_length(length)
                self.viewer.set_calibration(calibration)
                self.statusBar().showMessage("已恢复离线视野和标注；可继续编辑或保存图像。", 10000)
            self._update_frame_parameters(processed.frame)
            self._update_histogram()
        if self.recorder is not None and self.current is not None:
            self.recorder.push(self.current.rgb)
        elapsed = monotonic() - self._fps_start
        if elapsed >= 1:
            self.display_fps_label.setText(f"显示 {self._displayed / elapsed:.1f} FPS")
            self._displayed, self._fps_start = 0, monotonic()

    def _freeze(self, frozen: bool) -> None:
        if frozen and self.current is None:
            with QSignalBlocker(self.freeze_action):
                self.freeze_action.setChecked(False)
            return
        self._frozen = frozen
        self._frozen_frame = self.current.frame if frozen else None
        self.processor.freeze(self._frozen_frame)
        self.freeze_action.setText("继续显示" if frozen else "冻结画面")
        self.statusBar().showMessage("显示已冻结，相机继续采集。" if frozen else "已恢复实时显示。", 4000)

    def _histogram_mode_changed(self, index: int) -> None:
        mode = self.histogram_mode.currentData()
        channel_index = int(mode) + 1 if mode in ("0", "1", "2") else 0
        with QSignalBlocker(self.channel_combo):
            self.channel_combo.setCurrentIndex(channel_index)
        self._channel_changed(channel_index)

    def _channel_changed(self, index: int) -> None:
        channel = index - 1
        mode = str(channel) if channel >= 0 else "overall"
        if channel < 0 and self.histogram_mode.currentData() == "rgb":
            mode = "rgb"
        with QSignalBlocker(self.histogram_mode):
            self.histogram_mode.setCurrentIndex(self.histogram_mode.findData(mode))
        black = self.settings.black if channel < 0 else self.settings.channel_black[channel]
        white = self.settings.white if channel < 0 else self.settings.channel_white[channel]
        with QSignalBlocker(self.black_box), QSignalBlocker(self.white_box):
            self.black_box.setValue(black)
            self.white_box.setValue(white)
        self._update_histogram()

    def _levels_changed(self, value: float) -> None:
        black, white = self.black_box.value(), self.white_box.value()
        if black >= white:
            if self.sender() == self.black_box:
                black = white - 0.1
            else:
                white = black + 0.1
            with QSignalBlocker(self.black_box), QSignalBlocker(self.white_box):
                self.black_box.setValue(black)
                self.white_box.setValue(white)
        channel = self.channel_combo.currentIndex() - 1
        if channel < 0:
            self.settings = replace(self.settings, black=black, white=white)
        else:
            lo, hi = list(self.settings.channel_black), list(self.settings.channel_white)
            lo[channel], hi[channel] = black, white
            self.settings = replace(self.settings, channel_black=tuple(lo), channel_white=tuple(hi))
        self.processor.configure(self.settings)
        self._update_histogram()
        self._remember_display()

    def _histogram_dragged(self, black: float, white: float) -> None:
        with QSignalBlocker(self.black_box), QSignalBlocker(self.white_box):
            self.black_box.setValue(black)
            self.white_box.setValue(white)
        self._levels_changed(black)

    def _gamma_changed(self, gamma: float) -> None:
        self.settings = replace(self.settings, gamma=gamma)
        self.processor.configure(self.settings)
        self._remember_display()

    def _reset_histogram_adjustments(self) -> None:
        self._set_display_settings(replace(
            self.settings,
            black=0.0, white=100.0, gamma=1.0,
            channel_black=(0.0, 0.0, 0.0),
            channel_white=(100.0, 100.0, 100.0),
        ))

    def _reset_display_settings(self):
        orientation_changed = self.settings.flip_x or self.settings.flip_y or self.settings.rotation != 0
        if self.recorder is not None and self.settings.rotation != 0:
            self.statusBar().showMessage("请先停止录像，再恢复图像方向。", 5000)
            return
        def reset():
            self._set_display_settings(DisplaySettings())
        if orientation_changed and self.viewer.annotations:
            self._confirm_new_view(reset)
        else:
            reset()

    def _set_display_settings(self, settings: DisplaySettings) -> None:
        self.settings = settings
        with QSignalBlocker(self.gamma_box):
            self.gamma_box.setValue(settings.gamma)
        self._channel_changed(self.channel_combo.currentIndex())
        self._sync_display_controls()
        self.processor.configure(settings)
        self._remember_display()


    def _hist_logarithmic(self, enabled: bool) -> None:
        self.histogram.logarithmic = enabled
        self.histogram.update()

    def _update_histogram(self, *args) -> None:
        if self.current is None:
            return
        self.histogram.configure(self.current.input_histogram, self.channel_combo.currentIndex() - 1,
                                 self.black_box.value(), self.white_box.value(), True,
                                 self.current.luminance_histogram, self.histogram_mode.currentData())

    def _sync_annotation_context(self):
        context = self.current.frame.profile_context if self.current else {}
        name = self._display_objective()
        objective = self.store.objective(context.get("objective_id") or name)
        self.viewer.objective_name = name
        self.viewer.objective_id = context.get("objective_id", objective["id"] if objective else "")
        magnification = context.get("magnification", objective["magnification"] if objective else None)
        try:
            magnification = float(magnification)
            if not math.isfinite(magnification) or magnification <= 0:
                magnification = None
        except (TypeError, ValueError):
            magnification = None
        self.viewer.magnification = magnification

    def _annotations_changed(self) -> None:
        if not hasattr(self, "annotation_list"):
            return
        with QSignalBlocker(self.annotation_list):
            expanded = {self.annotation_list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole + 1):
                        self.annotation_list.topLevelItem(i).isExpanded()
                        for i in range(self.annotation_list.topLevelItemCount())}
            self.annotation_list.clear()
            labels = {"marker": "标记点", "line": "线条标注", "rect": "矩形框", "ellipse": "椭圆框",
                      "text": "文字说明", "freehand": "自由画笔", "measure": "长度", "calibrate": "标定线", "polygon": "面积", "angle": "角度", "polygon_shape": "多边形", "image": "图片"}
            groups = {}
            for index, annotation in enumerate(self.viewer.annotations):
                measured = annotation.kind in ("measure", "calibrate", "polygon", "angle")
                detail = (annotation.measurement_label(self.viewer.calibration) if measured else
                          f"{annotation.image_name} · {annotation.image_opacity * 100:g}%" if annotation.kind == "image" else annotation.text)
                parent = None
                if annotation.group_id:
                    if annotation.group_id not in groups:
                        parent = QTreeWidgetItem(self.annotation_list, [f"组合 {len(groups) + 1}"])
                        parent.setData(0, Qt.ItemDataRole.UserRole, tuple(i for i, a in enumerate(self.viewer.annotations)
                                                                         if a.group_id == annotation.group_id))
                        parent.setData(0, Qt.ItemDataRole.UserRole + 1, annotation.group_id)
                        parent.setExpanded(expanded.get(annotation.group_id, True))
                        groups[annotation.group_id] = parent
                    parent = groups[annotation.group_id]
                item = QTreeWidgetItem(parent if parent is not None else self.annotation_list,
                                       [f"{index + 1:02d}  {labels.get(annotation.kind, annotation.kind)}  {detail}"])
                item.setData(0, Qt.ItemDataRole.UserRole, (index,))
                if parent is not None:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                if annotation.kind == "image":
                    item.setToolTip(0, f"{annotation.image_name}\n图片标注：可移动、缩放、旋转和组合；移动原文件后也能恢复标注。")
                if annotation.text:
                    item.setToolTip(0, annotation.text)
                if measured:
                    tooltip = detail + "\n原始测量值：移动、缩放和旋转不会重新计算；倍率及标定来自创建时的画面。"
                    if annotation.kind == "polygon":
                        tooltip += (f"\n平均 RGB：完成圈选时的底图显示像素，范围 0–255，含边界像素，共 {annotation.rgb_pixel_count} 像素；不含叠加标注。"
                                    if annotation.mean_rgb is not None else "\n此面积记录未保存 RGB 统计。")
                    item.setToolTip(0, tooltip)
        self._sync_annotation_selection()
        calibration = self.viewer.calibration
        if self._last_calibration != calibration:
            with QSignalBlocker(self.scale_box), QSignalBlocker(self.unit_combo):
                self.scale_box.setValue(calibration.units_per_pixel)
                self.unit_combo.setCurrentText(calibration.unit)
            self._last_calibration = calibration
        self.calibration_label.setText(f"{calibration.units_per_pixel:.6g} {calibration.unit}/px"
                                       if calibration.units_per_pixel else f"{self.objective_list.currentText()} 尚未标定 · 长度显示为 px")
        self._refresh_objectives()
        self._update_scale_bar_button()

    def _update_zoom(self, scale):
        if hasattr(self, "zoom_button"):
            self.zoom_button.setText(f"缩放 {scale * 100:.1f}%")
            self.zoom_button.setEnabled(True)

    def _choose_zoom(self):
        if self.current is None:
            return
        value, ok = QInputDialog.getDouble(self, "显示缩放比例", "缩放比例 / %：",
            self.viewer.transform().m11() * 100, 2, 3200, 1)
        if ok:
            self.viewer.set_zoom(value / 100)

    def _update_scale_bar_button(self):
        unit = self.viewer.calibration.unit
        factor = UNIT_TO_UM.get(unit, 1.0)
        self.scale_bar_button.setText(f"比例尺长度：{self.viewer.scale_bar_length_um / factor:g} {unit}…")

    def _choose_scale_bar_length(self):
        unit = self.viewer.calibration.unit
        factor = UNIT_TO_UM.get(unit, 1.0)
        value, ok = QInputDialog.getDouble(self, "比例尺的实际长度",
            f"实际长度 / {unit}：\n长度固定后，横线会随当前物镜标定和显示缩放变长或变短。",
            self.viewer.scale_bar_length_um / factor, 0.000001, 1e12, 6)
        if ok:
            self.viewer.set_scale_bar_length(value * factor)
            self.store.data["preferences"]["scale_bar_length_um"] = value * factor
            try:
                self.store.save()
            except (OSError, ValueError) as exc:
                self._error(f"比例尺设置保存失败：{exc}")
            self._update_scale_bar_button()

    def _annotation_tree_selection_changed(self):
        indices = set()
        for item in self.annotation_list.selectedItems():
            indices.update(item.data(0, Qt.ItemDataRole.UserRole) or ())
        self.viewer.select_annotations(indices)
        if self.viewer.tool != "select":
            self.tool_actions["select"].trigger()

    def _sync_annotation_selection(self):
        if not hasattr(self, "annotation_list"):
            return
        with QSignalBlocker(self.annotation_list):
            for index in range(self.annotation_list.topLevelItemCount()):
                item = self.annotation_list.topLevelItem(index)
                indices = set(item.data(0, Qt.ItemDataRole.UserRole) or ())
                item.setSelected(bool(indices) and indices <= self.viewer.selection)
        images = [self.viewer.annotations[i] for i in sorted(self.viewer.selection)
                  if self.viewer.annotations[i].kind == "image"]
        self.annotation_selection_label.setText(f"已选 {len(self.viewer.selection)} 项" if self.viewer.selection else "未选择")
        self.image_opacity_row.setVisible(bool(images))
        values = self.viewer.selection_transform_values()
        self.annotation_transform_row.setVisible(values is not None)
        if values is not None:
            with QSignalBlocker(self.annotation_angle_box), QSignalBlocker(self.annotation_scale_box):
                self.annotation_angle_box.setValue(values[0])
                self.annotation_scale_box.setValue(values[1] * 100)
        self.export_group_button.setEnabled(bool(self.viewer.selected_group_annotations()))
        measurements = [a for i in sorted(self.viewer.selection)
                        if (a := self.viewer.annotations[i]).kind in ("measure", "polygon", "calibrate", "angle")]
        self.annotation_measurement_label.setVisible(bool(measurements))
        if measurements:
            detail = measurements[0].measurement_label(self.viewer.calibration).replace(" · 平均 RGB ", "\n平均 RGB ")
            self.annotation_measurement_label.setText(
                f"原始测量：{detail}" if len(measurements) == 1 else f"已选 {len(measurements)} 项测量 · 保留原始数值")
        with QSignalBlocker(self.image_opacity_box):
            self.image_opacity_box.setEnabled(bool(images))
            if images:
                self.image_opacity_box.setValue(images[0].image_opacity * 100)
        if "group" in getattr(self, "shortcut_actions", {}):
            self.shortcut_actions["group"].setEnabled(self.viewer.can_group())
            self.shortcut_actions["ungroup"].setEnabled(any(self.viewer.annotations[i].group_id for i in self.viewer.selection))

    def _image_opacity_changed(self):
        self.viewer.set_image_opacity(self.image_opacity_box.value() / 100)

    def _annotation_angle_changed(self):
        try:
            self.viewer.set_selection_transform(angle=self.annotation_angle_box.value())
        except ValueError as exc:
            self._error(str(exc))
        self._sync_annotation_selection()

    def _annotation_scale_changed(self):
        try:
            self.viewer.set_selection_transform(scale=self.annotation_scale_box.value() / 100)
        except ValueError as exc:
            self._error(str(exc))
        self._sync_annotation_selection()

    def _export_annotation_group(self):
        annotations = self.viewer.selected_group_annotations()
        if not annotations:
            return
        try:
            # Snapshot before the dialog: no camera frame or capture metadata is read.
            image = annotation_group_image(annotations, self.viewer.calibration)
            filename, _ = QFileDialog.getSaveFileName(self, "导出标注组合", "标注组合.png", "PNG 图像 (*.png)")
            if not filename:
                return
            if not filename.lower().endswith(".png"):
                filename += ".png"
            output = QSaveFile(filename)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if not image.save(output, "PNG"):
                output.cancelWriting()
                raise OSError("PNG 写入失败")
            if not output.commit():
                raise OSError(output.errorString())
            self.statusBar().showMessage(f"标注组合已导出：{filename}", 6000)
        except (OSError, ValueError) as exc:
            self._error(f"标注组合导出失败：{exc}")

    def _import_image_annotation(self):
        if self.current is None or self._profile_requests:
            self.statusBar().showMessage("请先连接相机或打开底图，并等待物镜切换完成，再导入图片标注。", 6000)
            return
        filename, _ = QFileDialog.getOpenFileName(self, "导入图片标注", "",
            "图像 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)")
        if not filename:
            return
        try:
            data, image = load_image_asset(Path(filename))
            if self.current is None or self._profile_requests:
                self.statusBar().showMessage("视野正在变化，请等待画面稳定后重新导入。", 6000)
                return
            geometry = self._geometry
            visible = self.viewer.mapToScene(self.viewer.viewport().rect()).boundingRect().intersected(self.viewer.sceneRect())
            if visible.isEmpty():
                visible = self.viewer.sceneRect()
            scale = min(1., visible.width() * .7 / image.width(), visible.height() * .7 / image.height())
            dialog = ImageAnnotationDialog(self, data, Path(filename).name, self.viewer.exported_image(), visible.center(), scale)
            try:
                if dialog.exec() != dialog.DialogCode.Accepted:
                    return
                annotation = dialog.annotation()
            finally:
                dialog.deleteLater()
            if self.current is None or self._geometry != geometry or self._profile_requests:
                self.statusBar().showMessage("视野已经变化，请在当前画面重新导入图片标注。", 6000)
                return
            self.viewer.add_annotation(annotation)
            self.tool_actions["select"].trigger()
        except (OSError, ValueError) as exc:
            self._error(f"图片标注导入失败：{exc}")

    def _show_calibration_settings(self):
        self.calibration_dialog.reveal()

    def _remember_camera_serial(self):
        serial = self.serial_edit.text().strip()
        try:
            self.store.transaction(lambda: self.store.data["preferences"].update(manual_camera_serial=serial))
        except (OSError, ValueError) as exc:
            self._error(f"相机序列号设置保存失败：{exc}")

    def _activate_tool(self, tool):
        previous = self.viewer.tool
        if tool == "calibrate" and self.current is None:
            self.tool_actions[previous].setChecked(True)
            self.statusBar().showMessage("请先连接相机或打开标尺图像，再校准物镜。", 5000)
            return
        if previous == "calibrate" and tool != "calibrate":
            if self._resume_after_calibration:
                self.freeze_action.setChecked(False)
            self._resume_after_calibration = False
        if tool == "calibrate" and previous != "calibrate":
            self._resume_after_calibration = not self.freeze_action.isChecked()
            self.freeze_action.setChecked(True)
        self.viewer.color = self._annotation_color
        self.viewer.set_tool(tool)
        self.tool_hint.setText(self._current_tool_hint())
        if tool == "calibrate":
            self.calibration_dialog.hide()
            self.statusBar().showMessage("沿图中已知长度的标尺拖线，随后输入实际长度。", 7000)
        elif tool != "pan":
            self.tabs.setCurrentIndex(2)

    def _show_selected_annotation(self, index):
        if 0 <= index < len(self.viewer.annotations):
            self.tabs.setCurrentIndex(2)

    def _update_processing_state(self):
        source_gray = self.current is not None and self.current.frame.pixel_format.startswith("Mono")
        text = "电脑处理：默认" if self.settings == DisplaySettings() else "电脑处理：已调整"
        if source_gray:
            text += " · 相机 Mono"
        elif self.settings.grayscale:
            text += " · 灰度显示"
        elif self.settings.saturation == 0:
            text += " · 饱和度为 0"
        self.processing_state_label.setText(text)
        self.processing_state_label.setStyleSheet("color: #ffd166; font-size: 12px;" if source_gray or self.settings.grayscale or self.settings.saturation == 0 else "")



    def _add_text(self, point: tuple) -> None:
        text, ok = QInputDialog.getText(self, "文字标记", "文字：")
        if ok and text.strip():
            self.viewer.add_annotation(Annotation("text", (point,), self.viewer.color, text.strip()))

    def _update_annotation_color_button(self):
        swatch = QPixmap(18, 18)
        swatch.fill(QColor(self._annotation_color))
        self.annotation_color_button.setIcon(QIcon(swatch))

    def _choose_color(self, scope="annotation") -> None:
        color = QColorDialog.getColor(QColor(self._annotation_color), self, "标注颜色")
        if color.isValid():
            self._annotation_color = color.name()
            self.viewer.color = color.name()
            self.viewer.set_selection_color(color.name())
            self._update_annotation_color_button()

    def _open_image(self) -> None:
        if self.capture is not None:
            self.statusBar().showMessage("请先停止相机采集，再打开文件。", 5000)
            return
        filename, _ = QFileDialog.getOpenFileName(self, "打开图像", "", "图像 (*.png *.tif *.tiff *.jpg *.jpeg *.bmp)")
        if filename:
            try:
                frame = load_image(Path(filename))
                self._new_source()
                self.raw_buffer.put(frame)
                self.source_badge.setText("文件 · " + Path(filename).name)
            except Exception as exc:
                self._error(f"打开失败：{exc}")

    def _capture_image(self, checked=False, *, quick=False, after_saved=None) -> None:
        if self.current is None:
            self.statusBar().showMessage("请先采集或打开图像。", 4000)
            return
        if self._save_busy:
            self.statusBar().showMessage("正在保存上一张图像。", 4000)
            return
        if self.capture is not None and not self._frozen and (self._profile_requests or
                self.current.frame.profile_context.get("generation", 0) < self._minimum_frame_generation):
            self.statusBar().showMessage("相机参数正在应用，等待回读及新画面后再拍照。", 5000)
            return
        # Pin one frame before entering the dialog's event loop. Live preview
        # may keep advancing, but both export formats describe this same frame.
        processed, annotations, calibration = self.current, list(self.viewer.annotations), self.viewer.calibration
        scale_bar_length_um = self.viewer.scale_bar_length_um
        format_name = self.snapshot_format
        include = self.snapshot_annotations
        try:
            overwrite = False
            now = datetime.now()
            if quick:
                if self._naming_error:
                    raise ValueError(self._naming_error)
                used_settings = dict(self.snapshot_naming)
                path, sequence = available_capture_path(self.capture_directory, used_settings, now,
                    self._capture_objective_token(processed), format_name)
            else:
                filter_ = "PNG (*.png)" if format_name == "png" else "TIFF (*.tiff *.tif)"
                filename, _ = QFileDialog.getSaveFileName(self, "拍照：填写文件名并选择保存位置",
                    str(self.capture_directory) + "/", filter_, options=QFileDialog.Option.DontConfirmOverwrite)
                if not filename:
                    return
                path = Path(filename)
                allowed = (".png",) if format_name == "png" else (".tif", ".tiff")
                if path.suffix.lower() not in allowed:
                    path = path.with_suffix("." + format_name)
                validate_filename(path.stem)
                if path.exists():
                    if QMessageBox.question(self, "覆盖已有图像？", f"文件已存在：\n{path}\n是否覆盖？",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                        return
                    overwrite = True
            saved_annotations = annotations if include else []
            if format_name == "tiff":
                writer = lambda destination: save_raw(destination, processed, saved_annotations, calibration,
                    scale_bar_length_um=scale_bar_length_um if include else None)
            else:
                image = rgb_image(processed.rgb)
                if include:
                    from PySide6.QtGui import QPainter
                    painter = QPainter(image)
                    try:
                        paint_annotations(painter, annotations, calibration)
                        paint_scale_bar(painter, (image.width(), image.height()), calibration, scale_bar_length_um)
                    finally:
                        painter.end()
                metadata = export_metadata(processed, saved_annotations, calibration, "display")
                metadata["annotations_burned_in"] = include
                if include:
                    metadata["scale_bar_length_um"] = scale_bar_length_um
                writer = lambda destination: save_png(destination, image, metadata)
            if quick:
                self._snapshot_sequences[str(path)] = (used_settings, sequence, now)
            self._submit_save(path, lambda: write_snapshot(path, writer, overwrite=overwrite), after_saved)
        except (OSError, ValueError) as exc:
            self._error(f"保存失败：{exc}")

    def _update_snapshot_summary(self):
        if self.snapshot_format == "tiff":
            detail = "TIFF 保留相机原始像素和位深，不应用电脑处理。" + (
                "标注和比例尺信息写入文件记录，不绘入原始像素。" if self.snapshot_annotations else "不保存标注数据。")
        else:
            detail = "PNG 保留当前颜色与亮度，" + ("叠加标注和比例尺。" if self.snapshot_annotations else "不叠加标注和比例尺。")
        self.snapshot_summary.setText("“拍照”和“快速保存”共用这些选项。\n" + detail)
        self._update_capture_name_preview()

    def _submit_save(self, path: Path, callback, after_saved=None) -> None:
        self._pending_saves += 1
        self._save_busy = True
        if after_saved:
            self._save_callbacks[str(path)] = after_saved
        self.statusBar().showMessage("正在保存…")

        def task() -> None:
            try:
                callback()
                self.bridge.saved.emit(str(path))
            except Exception as exc:
                self._save_callbacks.pop(str(path), None)
                self._snapshot_sequences.pop(str(path), None)
                self.bridge.saved.emit("")
                self.bridge.error.emit(f"保存失败：{exc}")
        self._pool.submit(task)

    def _saved(self, path: str) -> None:
        self._pending_saves = max(0, self._pending_saves - 1)
        self._save_busy = self._pending_saves > 0
        if path:
            self.statusBar().showMessage(f"已保存：{path}", 8000)
            sequence = self._snapshot_sequences.pop(path, None)
            if sequence:
                self._advance_capture_sequence(*sequence)
            callback = self._save_callbacks.pop(path, None)
            if callback and not self._closing:
                callback()
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _error(self, message: str) -> None:
        logging.getLogger(__name__).error(message)
        self.error_label.setText(message)
        self.statusBar().showMessage(message.splitlines()[0], 15000)

    def closeEvent(self, event) -> None:
        self._stop_timelapse()
        if self.recorder is not None: self.recorder.stop()
        if self.capture is not None or self._save_busy or self.recorder is not None or self.discovery is not None:
            self._closing = True
            self._disconnect_camera()
            self.statusBar().showMessage("正在释放相机并完成保存…")
            event.ignore()
            return
        for dialog in (self.calibration_dialog, self.device_settings_dialog, self.naming_settings_dialog):
            dialog.close()
        self.timer.stop()
        self.naming_preview_timer.stop()
        self.processor.stop()
        self.processor.join(timeout=3)
        self._pool.shutdown(wait=True)
        self.wheel_guard.uninstall()
        self.shortcuts.uninstall()
        event.accept()
