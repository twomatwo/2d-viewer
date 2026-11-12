from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSlider, QSpinBox,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget, QTreeWidget, QAbstractItemView)

from .annotation_icons import annotation_icon
from .histogram import HistogramWidget
from .controls import ObjectiveList
from .dialogs import TaskDialog
from .capture_naming import DATE_FORMATS, MAX_SEQUENCE


def note(text):
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def number(low, high, value, digits=2):
    box = QDoubleSpinBox()
    box.setDecimals(digits)
    box.setRange(low, high)
    box.setValue(value)
    box.setKeyboardTracking(False)
    box.setSingleStep(0.1 if digits else 1)
    return box


def integer(low, high, value):
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(value)
    box.setKeyboardTracking(False)
    return box


class Section(QWidget):
    def __init__(self, title, expanded=True):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        header = QToolButton()
        header.setText(title)
        header.setCheckable(True)
        header.setChecked(expanded)
        header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.setStyleSheet("QToolButton { background: #263449; text-align: left; padding: 9px; }")
        body = QFrame()
        self.form = QFormLayout(body)
        self.form.setContentsMargins(8, 9, 8, 9)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        body.setVisible(expanded)
        self.header, self.body = header, body
        header.toggled.connect(body.setVisible)
        header.toggled.connect(lambda active: header.setArrowType(Qt.ArrowType.DownArrow if active else Qt.ArrowType.RightArrow))
        layout.addWidget(header)
        layout.addWidget(body)


def button_row(*buttons):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    for button in buttons: layout.addWidget(button)
    return row


def camera_panel(w):
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.addWidget(note("相机参数 · 直接改变采集，也会影响原始 TIFF。"))
    device = Section("设备连接")
    w.source_combo = QComboBox()
    w.source_combo.addItem("自动选择（仅一台相机时）", "")
    w.source_combo.setToolTip("选择要连接的相机；设备设置中指定序列号时优先使用该序列号。")
    w.scan_button = QPushButton("扫描相机")
    w.scan_button.clicked.connect(w._scan_cameras)
    w.serial_edit = QLineEdit()
    w.serial_edit.setPlaceholderText("留空时使用右侧选择的相机")
    w.serial_edit.setText(w.store.data["preferences"].get("manual_camera_serial", ""))
    w.device_settings_dialog = TaskDialog(w, "设备设置", "固定使用某台相机时填写序列号；留空则使用相机参数页的选择。")
    w.device_settings_dialog.form.addRow("手动指定序列号", w.serial_edit)
    w.serial_edit.editingFinished.connect(w._remember_camera_serial)
    w.connect_button = QPushButton("连接 / 开始预览")
    w.connect_button.setObjectName("primary")
    w.connect_button.clicked.connect(w._connect_camera)
    w.disconnect_button = QPushButton("停止预览")
    w.disconnect_button.clicked.connect(w._disconnect_camera)
    w.disconnect_button.setEnabled(False)
    w.device_label = note("Spinnaker 相机 · USB3／GigE")
    device.form.addRow(w.source_combo)
    device.form.addRow(w.scan_button)
    device.form.addRow(button_row(w.connect_button, w.disconnect_button))
    device.form.addRow(w.device_label)
    layout.addWidget(device)

    w.camera_profiles_section = Section("物镜相机参数")
    layout.addWidget(w.camera_profiles_section)

    w.camera_group = QWidget()
    settings_layout = QVBoxLayout(w.camera_group)
    settings_layout.setContentsMargins(0, 0, 0, 0)
    exposure = Section("曝光与增益")
    w.auto_exposure_check = QCheckBox("自动曝光")
    w.auto_gain_check = QCheckBox("自动增益")
    w.once_exposure_button = QPushButton("自动一次")
    w.once_exposure_button.clicked.connect(w._exposure_once)
    exposure.form.addRow(w.auto_exposure_check)
    w.numeric_boxes = {
        "ExposureTime": number(0.006, 30000, 10, 3),
        "Gain": number(0, 48, 0),
        "AcquisitionFrameRate": number(1, 73, 20, 1),
        "AutoExposureTargetGreyValue": number(0, 100, 45, 1),
        "AutoExposureExposureTimeUpperLimit": number(0.006, 30000, 100, 3),
        "AutoExposureGainUpperLimit": number(0, 48, 24),
        "BalanceRatioRed": number(0.1, 8, 1, 3),
        "BalanceRatioBlue": number(0.1, 8, 1, 3),
    }
    w.exposure_slider = QSlider(Qt.Orientation.Horizontal)
    w.exposure_slider.setRange(0, 1000)
    w.gain_slider = QSlider(Qt.Orientation.Horizontal)
    w.gain_slider.setRange(0, 1000)
    exposure.form.addRow("曝光时间 / ms", w.numeric_boxes["ExposureTime"])
    exposure.form.addRow(w.exposure_slider)
    exposure.form.addRow(w.auto_gain_check)
    exposure.form.addRow("增益 / dB", w.numeric_boxes["Gain"])
    exposure.form.addRow(w.gain_slider)
    w.auto_parameters = Section("自动控制 · 高级设置", False)
    form = w.auto_parameters.form
    form.addRow("目标亮度 / %", w.numeric_boxes["AutoExposureTargetGreyValue"])
    form.addRow("曝光上限 / ms", w.numeric_boxes["AutoExposureExposureTimeUpperLimit"])
    form.addRow("增益上限 / dB", w.numeric_boxes["AutoExposureGainUpperLimit"])
    form.addRow(w.once_exposure_button)
    form.addRow(note("可先设好目标与上限，再开启自动曝光或自动增益；未开启时保留设置。"))
    exposure.form.addRow(w.auto_parameters)
    w.actual_exposure_label = note("当前曝光 — · 增益 —")
    exposure.form.addRow(w.actual_exposure_label)
    w.auto_exposure_check.toggled.connect(w._exposure_mode_changed)
    w.auto_gain_check.toggled.connect(w._gain_mode_changed)
    w.exposure_slider.valueChanged.connect(w._exposure_slider_changed)
    w.exposure_slider.sliderReleased.connect(lambda: w._commit_numeric("ExposureTime"))
    w.gain_slider.valueChanged.connect(w._gain_slider_changed)
    w.gain_slider.sliderReleased.connect(lambda: w._commit_numeric("Gain"))
    for name, box in w.numeric_boxes.items():
        box.editingFinished.connect(lambda node=name: w._commit_numeric(node))
    w.numeric_boxes["ExposureTime"].valueChanged.connect(w._sync_exposure_slider)
    w.numeric_boxes["Gain"].valueChanged.connect(w._sync_gain_slider)
    settings_layout.addWidget(exposure)

    wb = Section("相机白平衡", False)
    w.auto_wb_check = QCheckBox("持续自动白平衡")
    w.auto_wb_check.toggled.connect(w._wb_mode_changed)
    w.once_wb_button = QPushButton("白平衡一次")
    w.once_wb_button.clicked.connect(lambda: w._camera_command({"BalanceWhiteAuto": "Once"}))
    wb.form.addRow(w.auto_wb_check)
    wb.form.addRow(w.once_wb_button)
    wb.form.addRow("R / G 比例", w.numeric_boxes["BalanceRatioRed"])
    wb.form.addRow("B / G 比例", w.numeric_boxes["BalanceRatioBlue"])
    wb.form.addRow(note("相机白平衡以绿色为基准。完整 RGB 调节在“电脑处理”页。白平衡前让白色或灰色占据主要视野。"))
    settings_layout.addWidget(wb)

    w.roi_group = Section("分辨率 / 采集区域", False)
    w.enum_boxes = {"PixelFormat": QComboBox()}
    w.enum_boxes["PixelFormat"].currentIndexChanged.connect(w._pixel_format_changed)
    w.roi_group.form.addRow("像素格式 / 位深", w.enum_boxes["PixelFormat"])
    w.frame_rate_check = QCheckBox("限制采集帧率")
    w.frame_rate_check.setToolTip("不勾选时由相机决定采集速度；勾选后设置上限，实际帧率还受曝光、格式和传输带宽限制")
    w.frame_rate_check.toggled.connect(w._frame_rate_mode_changed)
    w.roi_group.form.addRow(w.frame_rate_check)
    w.roi_group.form.addRow("帧率上限 / FPS", w.numeric_boxes["AcquisitionFrameRate"])
    w.resolution_combo = QComboBox()
    w.resolution_combo.addItems(["全幅", "1920 × 1200", "1280 × 1024", "640 × 480", "自定义"])
    w.resolution_combo.currentIndexChanged.connect(w._resolution_changed)
    w.roi_group.form.addRow("区域预设", w.resolution_combo)
    w.roi_boxes = {"OffsetX": integer(0, 10000, 0), "OffsetY": integer(0, 10000, 0),
                   "Width": integer(16, 10000, 1200), "Height": integer(16, 10000, 900)}
    for name, label in (("OffsetX", "起点 X"), ("OffsetY", "起点 Y"), ("Width", "宽 / px"), ("Height", "高 / px")):
        w.roi_group.form.addRow(label, w.roi_boxes[name])
    apply_roi, full_roi = QPushButton("应用区域"), QPushButton("恢复全幅")
    w.apply_roi_button, w.full_roi_button = apply_roi, full_roi
    apply_roi.clicked.connect(w._apply_roi)
    full_roi.clicked.connect(lambda: w._camera_command({"SensorROI": None}))
    w.roi_group.form.addRow(button_row(apply_roi, full_roi))
    w.roi_group.form.addRow(note("此处裁切传感器区域，像素尺寸不变；区域变小会缩小视野。"))
    settings_layout.addWidget(w.roi_group)
    w.camera_info = note("")
    settings_layout.addWidget(w.camera_info)
    layout.addWidget(w.camera_group)
    w.error_label = note("")
    w.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(w.error_label)
    layout.addStretch()
    return panel


def capture_panel(w):
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.addWidget(note("“拍照”填写文件名并选择位置；“快速保存”按下方目录、样品文字和设置中的命名规则保存。"))
    options = Section("图像保存选项")
    w.snapshot_format_box = QComboBox()
    w.snapshot_format_box.addItem("PNG · 当前显示图像", "png")
    w.snapshot_format_box.addItem("TIFF · 相机原始数据", "tiff")
    w.snapshot_format_box.setCurrentIndex(w.snapshot_format_box.findData(w.snapshot_format))
    w.snapshot_annotations_check = QCheckBox("保存标注、测量线和比例尺")
    w.snapshot_annotations_check.setChecked(w.snapshot_annotations)
    options.form.addRow("文件格式", w.snapshot_format_box)
    options.form.addRow(w.snapshot_annotations_check)
    w.snapshot_summary = note("")
    options.form.addRow(w.snapshot_summary)
    layout.addWidget(options)

    shooting = Section("快速保存", True)
    w.quick_capture_section = shooting
    w.capture_directory_label = note(str(w.capture_directory))
    folder = QPushButton("选择保存目录")
    folder.clicked.connect(w._choose_capture_directory)
    shooting.form.addRow(w.capture_directory_label)
    shooting.form.addRow(folder)
    w.capture_sample_text = QLineEdit(w.snapshot_naming["sample_text"])
    w.capture_sample_text.setMaxLength(100)
    w.capture_sample_text.setPlaceholderText("可留空；添加在文件名前")
    shooting.form.addRow("样品文字", w.capture_sample_text)
    w.naming_settings_dialog = TaskDialog(w, "保存与命名设置", "命名模板和编号规则自动保存。样品文字、下一序号和保存目录在右侧拍摄保存页调整。")
    rules = w.naming_settings_dialog.form
    w.capture_name_template = QLineEdit(w.snapshot_naming["template"])
    w.capture_name_template.setMaxLength(180)
    w.capture_name_template.setToolTip("可输入样品名称，自由排列日期、时间、序号和物镜字段；扩展名由文件格式决定")
    rules.addRow("命名模板", w.capture_name_template)
    fields = []
    for label, token in (("日期", "date"), ("时间", "time"), ("序号", "seq"), ("物镜", "objective")):
        button = QPushButton(label)
        button.setToolTip("在光标处插入 {" + token + "}")
        button.clicked.connect(lambda checked=False, field=token: w._insert_capture_name_field(field))
        fields.append(button)
    rules.addRow(button_row(*fields))
    rules.addRow(note("可插入字段并自由排列，例如 {date}_{seq}_{objective}；样品文字非空时自动作为前缀。"))
    w.capture_name_preview = note("")
    w.capture_name_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    shooting.form.addRow(w.capture_name_preview)
    w.capture_template_hint = note("命名模板可在左上角“设置 → 保存与命名 → 命名规则…”中修改。")
    shooting.form.addRow(w.capture_template_hint)
    naming = Section("日期与序号设置")
    w.capture_date_format = QComboBox()
    for label, pattern in zip(("20261027", "2026-10-27", "2026_10_27"), DATE_FORMATS):
        w.capture_date_format.addItem(label, pattern)
    w.capture_date_format.setCurrentIndex(w.capture_date_format.findData(w.snapshot_naming["date_format"]))
    w.capture_next_sequence = integer(1, MAX_SEQUENCE, w.snapshot_naming["sequence_next"])
    w.capture_sequence_digits = integer(1, 9, w.snapshot_naming["sequence_digits"])
    w.capture_sequence_step = integer(1, MAX_SEQUENCE, w.snapshot_naming["sequence_step"])
    w.capture_daily_reset = QCheckBox("每天重新编号")
    w.capture_daily_reset.setChecked(w.snapshot_naming["reset_daily"])
    w.capture_sequence_start = integer(1, MAX_SEQUENCE, w.snapshot_naming["sequence_start"])
    w.capture_sequence_start.setEnabled(w.snapshot_naming["reset_daily"])
    naming.form.addRow("日期格式", w.capture_date_format)
    shooting.form.addRow("下一序号", w.capture_next_sequence)
    naming.form.addRow("序号位数", w.capture_sequence_digits)
    naming.form.addRow("递增步长", w.capture_sequence_step)
    naming.form.addRow(w.capture_daily_reset)
    naming.form.addRow("每日起始序号", w.capture_sequence_start)
    naming.form.addRow(note("成功保存后递增；有同名文件时跳过已用序号。未使用序号字段时，同名文件追加 _2、_3…，保留已有图像。"))
    rules.addRow(naming)
    w.naming_settings_preview = note("")
    rules.addRow(w.naming_settings_preview)
    w.quick_capture_help = note("")
    shooting.form.addRow(w.quick_capture_help)
    layout.addWidget(shooting)
    w.snapshot_format_box.currentIndexChanged.connect(w._snapshot_options_changed)
    w.snapshot_annotations_check.toggled.connect(w._snapshot_options_changed)
    w.capture_name_template.textChanged.connect(w._capture_naming_changed)
    w.capture_sample_text.textChanged.connect(w._capture_naming_changed)
    w.capture_date_format.currentIndexChanged.connect(w._capture_naming_changed)
    w.capture_daily_reset.toggled.connect(w._capture_naming_changed)
    for control in (w.capture_next_sequence, w.capture_sequence_digits, w.capture_sequence_step, w.capture_sequence_start):
        control.valueChanged.connect(w._capture_naming_changed)

    recording = TaskDialog(w, "录制视频", "把连续画面保存成一个 AVI 视频，颜色与电脑显示一致。视频不包含标注或比例尺。")
    w.recording_dialog = recording
    w.record_button = QToolButton()
    w.record_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    w.video_fps_box = number(1, 60, 20, 1)
    recording.form.addRow("录像帧率 / FPS", w.video_fps_box)
    recording.form.addRow(w.record_button)
    recording.form.addRow(note("例如设为 20 FPS：每秒保存 20 帧；画面不足时重复上一帧。关闭此窗口后继续录像，可重新打开停止。"))
    w.recording_entry = QPushButton("录制视频…")
    w.recording_entry.clicked.connect(recording.reveal)
    layout.addWidget(w.recording_entry)
    layout.addWidget(note("连续画面 → 一个视频文件。"))
    shooting = TaskDialog(w, "定时拍照", "每隔指定秒数保存一张图，达到张数后停止。例如每隔 5 秒拍一张，共拍 20 张。")
    w.timelapse_dialog = shooting
    w.interval_box = number(0.1, 3600, 5, 1)
    w.capture_count_box = integer(1, 100000, 20)
    w.timelapse_format = QComboBox()
    w.timelapse_format.addItems(["TIFF · 相机原始数据", "PNG · 当前颜色与亮度，无标注"])
    shooting.form.addRow("拍照间隔 / 秒", w.interval_box)
    shooting.form.addRow("张数", w.capture_count_box)
    shooting.form.addRow("保存格式", w.timelapse_format)
    w.timelapse_button = QPushButton("开始定时拍照")
    w.timelapse_button.clicked.connect(w._toggle_timelapse)
    w.timelapse_status = note("尚未开始")
    shooting.form.addRow(w.timelapse_button)
    shooting.form.addRow(w.timelapse_status)
    shooting.form.addRow(note("每次拍摄读取新的一帧。关闭此窗口后继续拍摄，可重新打开停止。"))
    w.timelapse_entry = QPushButton("定时拍照…")
    w.timelapse_entry.clicked.connect(shooting.reveal)
    layout.addWidget(w.timelapse_entry)
    layout.addWidget(note("按时间间隔 → 多张独立图像。"))

    layout.addStretch()
    return panel


def display_panel(w):
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.addWidget(note("电脑处理 · 只改变预览、PNG 和预览录像，不写入相机或改变原始 TIFF。"))
    w.reset_display_button = QPushButton("恢复默认 · 不做额外电脑处理")
    w.reset_display_button.clicked.connect(w._reset_display_settings)
    layout.addWidget(w.reset_display_button)
    w.gray_check = QCheckBox("灰度显示（电脑处理）")
    w.gray_check.toggled.connect(w._display_controls_changed)
    layout.addWidget(w.gray_check)
    w.histogram_mode = QComboBox()
    for label, mode in (("整体亮度直方图", "overall"), ("RGB 三通道叠加", "rgb"),
                        ("R · 红色通道", "0"), ("G · 绿色通道", "1"), ("B · 蓝色通道", "2")):
        w.histogram_mode.addItem(label, mode)
    w.histogram_mode.setToolTip("与下方调节通道同步；RGB 三通道叠加时调节整体黑点和白点。")
    w.histogram_mode.currentIndexChanged.connect(w._histogram_mode_changed)
    layout.addWidget(w.histogram_mode)
    w.histogram = HistogramWidget()
    w.histogram.levels_changed.connect(w._histogram_dragged)
    layout.addWidget(w.histogram)
    log = QCheckBox("对数纵轴")
    log.setChecked(True)
    log.toggled.connect(w._hist_logarithmic)
    layout.addWidget(log)
    levels = Section("手动色阶 / 对比度")
    w.channel_combo = QComboBox()
    w.channel_combo.addItems(["整体", "R · 红", "G · 绿", "B · 蓝"])
    w.channel_combo.setToolTip("与上方直方图通道同步，各通道分别保留黑点和白点；Gamma 为整体调节。")
    w.black_box, w.white_box, w.gamma_box = number(0, 99.9, 0), number(0.1, 100, 100), number(0.1, 5, 1)
    w.black_box.valueChanged.connect(w._levels_changed)
    w.white_box.valueChanged.connect(w._levels_changed)
    w.gamma_box.valueChanged.connect(w._gamma_changed)
    w.channel_combo.currentIndexChanged.connect(w._channel_changed)
    levels.form.addRow("调节通道", w.channel_combo)
    levels.form.addRow("黑点 / %", w.black_box)
    levels.form.addRow("白点 / %", w.white_box)
    levels.form.addRow("Gamma", w.gamma_box)
    levels.form.addRow(note("拖动直方图的黑白标记，或输入数值。整体和各通道独立保存手动设置。"))
    reset = QPushButton("重置直方图调整")
    reset.clicked.connect(w._reset_histogram_adjustments)
    levels.form.addRow(reset)
    layout.addWidget(levels)
    color = Section("RGB 颜色 · 电脑处理", True)
    w.display_boxes = {"brightness": number(-100, 100, 0, 1), "contrast": number(0.1, 5, 1),
                       "saturation": number(0, 3, 1), "red_gain": number(0.1, 8, 1, 3),
                       "green_gain": number(0.1, 8, 1, 3), "blue_gain": number(0.1, 8, 1, 3)}
    w.rgb_sliders = {}
    for name, label in (("red_gain", "R · 红色"), ("green_gain", "G · 绿色"), ("blue_gain", "B · 蓝色")):
        box = w.display_boxes[name]
        box.setSingleStep(0.01)
        box.setSuffix(" ×")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(10, 800)
        slider.setValue(100)
        slider.setPageStep(10)
        slider.valueChanged.connect(lambda value, field=box: field.setValue(value / 100))
        box.valueChanged.connect(lambda value, channel=name: w._sync_rgb_slider(channel, value))
        w.rgb_sliders[name] = slider
        color.form.addRow(label, button_row(slider, box))
    color.form.addRow(note("R、G、B 独立调节；1.000× 保持该通道。"))
    layout.addWidget(color)
    appearance = Section("亮度 / 饱和度", False)
    for name, label in (("brightness", "亮度"), ("contrast", "对比度"), ("saturation", "饱和度")):
        appearance.form.addRow(label, w.display_boxes[name])
    for name in w.display_boxes:
        w.display_boxes[name].valueChanged.connect(w._display_controls_changed)
    layout.addWidget(appearance)
    orientation = Section("方向 / 辅助观察", False)
    w.flip_x_check, w.flip_y_check = QCheckBox("水平镜像"), QCheckBox("垂直镜像")
    w.flip_x_check.toggled.connect(w._display_controls_changed)
    w.flip_y_check.toggled.connect(w._display_controls_changed)
    w.rotation_combo = QComboBox()
    w.rotation_combo.addItems(["0°", "顺时针 90°", "180°", "顺时针 270°"])
    w.rotation_combo.currentIndexChanged.connect(w._display_controls_changed)
    w.grid_check = QCheckBox("显示网格")
    w.crosshair_check = QCheckBox("中心十字线")
    w.grid_check.toggled.connect(w._guides_changed)
    w.crosshair_check.toggled.connect(w._guides_changed)
    orientation.form.addRow(button_row(w.flip_x_check, w.flip_y_check))
    orientation.form.addRow("旋转", w.rotation_combo)
    orientation.form.addRow(w.grid_check)
    orientation.form.addRow(w.crosshair_check)
    layout.addWidget(orientation)
    layout.addStretch()
    return panel


def calibration_panel(w):
    panel = QWidget()
    layout = QVBoxLayout(panel)
    calibration = Section("物镜与标定比例")
    w.calibration_section = calibration
    w.objective_list = ObjectiveList()
    w.objective_list.addItems(w.store.objective_names())
    w.objective_list.setCurrentText(w.store.data["selected_objective"])
    w.objective_list.currentTextChanged.connect(w._objective_changed)
    calibration.form.addRow(w.objective_list)
    calibration.form.addRow(note("勾选当前物镜，每次只选一个。各倍率分别保存标定。"))
    w.calibration_label = note("当前物镜尚未标定")
    calibration.form.addRow(w.calibration_label)
    w.calibrate_button = QPushButton("用标尺校准当前物镜")
    w.calibrate_button.clicked.connect(lambda: w.tool_actions["calibrate"].trigger())
    calibration.form.addRow(w.calibrate_button)
    calibration.form.addRow(note("校准用于把像素换算成 µm 等实际长度，每个物镜只需保存一次。放入带刻度的标尺，点击上方按钮，沿已知刻度拖线，再输入刻度长度。"))
    w.scale_bar_button = QPushButton()
    w.scale_bar_button.setToolTip("点击设置比例尺代表的实际长度；修改标定后，横线长度会相应改变")
    w.scale_bar_button.clicked.connect(w._choose_scale_bar_length)
    calibration.form.addRow(w.scale_bar_button)
    layout.addWidget(calibration)
    edit = Section("手动填写比例 / 标定库", False)
    add = QPushButton("管理物镜…")
    add.clicked.connect(w._add_objective)
    edit.form.addRow(add)
    w.scale_box, w.unit_combo = number(0, 1e9, 0, 6), QComboBox()
    w.unit_combo.addItems(["µm", "mm", "nm"])
    edit.form.addRow("每像素长度", w.scale_box)
    edit.form.addRow("单位", w.unit_combo)
    save = QPushButton("保存到当前倍率")
    save.clicked.connect(w._apply_scale)
    edit.form.addRow(save)
    export, import_ = QPushButton("导出标定库"), QPushButton("导入标定库")
    export.clicked.connect(w._export_calibrations)
    import_.clicked.connect(w._import_calibrations)
    edit.form.addRow(button_row(export, import_))
    layout.addWidget(edit)
    layout.addStretch()
    return panel


def annotation_panel(w):
    panel = QWidget()
    panel.setObjectName("annotationPanel")
    panel.setStyleSheet("""
        QToolButton { padding: 3px 2px; }
        QPushButton { padding: 4px 6px; }
        QDoubleSpinBox { padding: 3px 4px; }
        QToolButton[annotationTool="true"] {
            background: #222f42; border: 1px solid #35465f; border-radius: 5px;
        }
        QToolButton[annotationTool="true"]:hover { background: #30445b; border-color: #557a94; }
        QToolButton[annotationTool="true"]:checked {
            background: #294f53; border-color: #69cbb3; color: #b6f4e1;
        }
    """)
    layout = QVBoxLayout(panel)
    layout.setSpacing(2)
    w.annotation_tool_buttons = {}

    def tool_button(name, icon_size=18):
        button = QToolButton()
        button.setProperty("annotationTool", True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(icon_size, icon_size))
        button.setMinimumHeight(28)
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        w.annotation_tool_buttons[name] = button
        return button

    w.select_button = tool_button("select", 16)
    w.select_button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    w.annotation_undo_button, w.annotation_redo_button = QToolButton(), QToolButton()
    history = QHBoxLayout()
    history.setContentsMargins(0, 0, 0, 0)
    history.setSpacing(4)
    history.addWidget(w.select_button)
    history.addStretch()
    for button in (w.annotation_undo_button, w.annotation_redo_button):
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(16, 16))
        history.addWidget(button)
    layout.addLayout(history)

    layout.addWidget(note("测量"))
    measurement_tools = QGridLayout()
    measurement_tools.setSpacing(4)
    for column, name in enumerate(("measure", "angle", "polygon")):
        measurement_tools.addWidget(tool_button(name), 0, column)
        measurement_tools.setColumnStretch(column, 1)
    layout.addLayout(measurement_tools)
    w.measure_button = w.annotation_tool_buttons["measure"]

    layout.addWidget(note("绘图标注"))
    drawing_tools = QGridLayout()
    drawing_tools.setSpacing(4)
    for index, name in enumerate(("rect", "ellipse", "line", "polygon_shape", "marker", "text", "freehand")):
        drawing_tools.addWidget(tool_button(name, 16), index // 4, index % 4)
    w.import_image_button = QToolButton()
    w.import_image_button.setProperty("annotationTool", True)
    w.import_image_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    w.import_image_button.setIconSize(QSize(16, 16))
    w.import_image_button.setMinimumHeight(28)
    w.import_image_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    drawing_tools.addWidget(w.import_image_button, 1, 3)
    for column in range(4):
        drawing_tools.setColumnStretch(column, 1)
    layout.addLayout(drawing_tools)

    w.annotation_color_button = QPushButton("标注颜色…")
    w.annotation_color_button.setToolTip("设置后续绘制颜色；选中标注时也会修改所选对象的颜色，可撤销。图片不受影响。")
    w.annotation_color_button.clicked.connect(lambda: w._choose_color("annotation"))
    layout.addWidget(w.annotation_color_button)
    divider = QFrame()
    divider.setFrameShape(QFrame.Shape.HLine)
    divider.setStyleSheet("color: #354156;")
    layout.addWidget(divider)

    w.annotation_selection_label = QLabel("未选择")
    w.group_button, w.ungroup_button = QToolButton(), QToolButton()
    selection = QHBoxLayout()
    selection.setContentsMargins(0, 0, 0, 0)
    selection.setSpacing(4)
    selection.addWidget(w.annotation_selection_label)
    selection.addStretch()
    for button in (w.group_button, w.ungroup_button):
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(16, 16))
        selection.addWidget(button)
    layout.addLayout(selection)
    w.image_opacity_row = QWidget()
    opacity_layout = QHBoxLayout(w.image_opacity_row)
    opacity_layout.setContentsMargins(0, 0, 0, 0)
    w.image_opacity_box = number(0, 100, 50, 1)
    w.image_opacity_box.setSuffix(" %")
    w.image_opacity_box.setToolTip("按回车或结束编辑应用。多选时同时修改所选图片；数值不同时显示第一张图片的不透明度。")
    w.image_opacity_box.editingFinished.connect(w._image_opacity_changed)
    opacity_layout.addWidget(QLabel("图片不透明度"))
    opacity_layout.addWidget(w.image_opacity_box, 1)
    layout.addWidget(w.image_opacity_row)
    w.annotation_transform_row = QWidget()
    transform_layout = QHBoxLayout(w.annotation_transform_row)
    transform_layout.setContentsMargins(0, 0, 0, 0)
    w.annotation_angle_box = number(-180, 180, 0, 3)
    w.annotation_angle_box.setSuffix(" °")
    w.annotation_scale_box = number(.01, 1000000, 100, 3)
    w.annotation_scale_box.setSuffix(" %")
    for label, box, callback in (("角度", w.annotation_angle_box, w._annotation_angle_changed),
                                 ("缩放", w.annotation_scale_box, w._annotation_scale_changed)):
        box.setMinimumWidth(0)
        box.setToolTip("回车或结束编辑应用；绕所选整体中心变换，可撤销。多选以列表第一项为数值基准，保持相对位置和变换。图片 100% 对应原图像素，正角度为顺时针。")
        box.editingFinished.connect(callback)
        transform_layout.addWidget(QLabel(label))
        transform_layout.addWidget(box, 1)
    layout.addWidget(w.annotation_transform_row)
    w.annotation_measurement_label = note("")
    w.annotation_measurement_label.setToolTip("记录创建时的物镜与标定；移动、缩放和旋转不重新计算原始测量值。")
    layout.addWidget(w.annotation_measurement_label)

    w.annotation_list = QTreeWidget()
    w.annotation_list.setHeaderHidden(True)
    w.annotation_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    w.annotation_list.setTextElideMode(Qt.TextElideMode.ElideRight)
    w.annotation_list.setMinimumHeight(140)
    w.annotation_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    w.annotation_list.setToolTip("Ctrl＋单击多选；在画面拖动对象移动，拖角点等比例缩放，拖上方圆点旋转。缩放后保留原始测量值。")
    w.annotation_list.itemSelectionChanged.connect(w._annotation_tree_selection_changed)
    layout.addWidget(w.annotation_list, 1)
    delete, clear = QPushButton("删除所选标注"), QPushButton("清空标注")
    delete.setIcon(annotation_icon("delete"))
    clear.setIcon(annotation_icon("clear"))
    delete.clicked.connect(lambda: w.viewer.delete_selected())
    clear.clicked.connect(lambda: w.viewer.clear_annotations())
    layout.addWidget(button_row(delete, clear))
    w.export_group_button = QPushButton("导出组合 PNG…")
    w.export_group_button.setIcon(annotation_icon("export"))
    w.export_group_button.setToolTip("选择一个组合后导出；透明背景，按内容裁切，只包含该组标注，不含相机画面、比例尺或选择框。")
    w.export_group_button.clicked.connect(w._export_annotation_group)
    export_csv = QPushButton("导出测量 CSV")
    export_csv.setIcon(annotation_icon("csv"))
    export_csv.clicked.connect(w._export_measurements)
    layout.addWidget(button_row(w.export_group_button, export_csv))
    return panel
