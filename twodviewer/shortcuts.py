"""Configurable Qt actions, with normal keyboard editing kept in input fields."""
from __future__ import annotations

from dataclasses import dataclass
import logging

from PySide6.QtCore import QEvent, QKeyCombination, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QApplication, QComboBox,
    QDialog, QDialogButtonBox, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget)


@dataclass(frozen=True)
class ShortcutSpec:
    name: str
    label: str
    defaults: tuple[str, ...] = ()


SHORTCUTS = (
    ShortcutSpec("open", "打开图像", ("Ctrl+O",)),
    ShortcutSpec("snapshot", "拍照：填写文件名和保存位置", ("Ctrl+S",)),
    ShortcutSpec("quick", "按配置的目录和命名规则快速保存", ("F9",)),
    ShortcutSpec("raw", "选择原始 TIFF 后拍照", ("Ctrl+Shift+S",)),
    ShortcutSpec("freeze", "冻结／恢复画面", ("Space",)),
    ShortcutSpec("record", "开始／停止录像", ("Ctrl+Shift+R",)),
    ShortcutSpec("fit", "适应窗口", ("0",)),
    ShortcutSpec("actual_size", "原始像素比例 100%", ("1",)),
    ShortcutSpec("zoom_in", "放大", ("+", "=")),
    ShortcutSpec("zoom_out", "缩小", ("-",)),
    ShortcutSpec("tool_pan", "移动视野", ("H",)),
    ShortcutSpec("tool_select", "选择标注", ("V",)),
    ShortcutSpec("tool_measure", "测量长度", ("M",)),
    ShortcutSpec("tool_marker", "标记点", ("P",)),
    ShortcutSpec("tool_text", "文字说明", ("T",)),
    ShortcutSpec("tool_rect", "矩形框", ("R",)),
    ShortcutSpec("tool_line", "线条标注"),
    ShortcutSpec("tool_ellipse", "椭圆框"),
    ShortcutSpec("tool_freehand", "自由画笔"),
    ShortcutSpec("tool_polygon", "测量面积与平均 RGB", ("G",)),
    ShortcutSpec("tool_angle", "测量角度"),
    ShortcutSpec("tool_polygon_shape", "多边形标注"),
    ShortcutSpec("import_image", "导入图片标注"),
    ShortcutSpec("group", "组合所选标注", ("Ctrl+G",)),
    ShortcutSpec("ungroup", "取消标注组合", ("Ctrl+Shift+G",)),
    ShortcutSpec("redo", "重做标注操作", ("Ctrl+Y", "Ctrl+Shift+Z")),
    ShortcutSpec("undo", "撤销标注操作", ("Ctrl+Z",)),
    ShortcutSpec("delete", "删除所选标注或测量", ("Del", "Backspace")),
    ShortcutSpec("cancel", "取消当前绘制或校准", ("Esc",)),
)


def default_bindings() -> dict[str, list[str]]:
    return {spec.name: list(spec.defaults) for spec in SHORTCUTS}


def _canonical_sequence(sequence: QKeySequence) -> QKeySequence:
    combination = sequence[0]
    modifiers = combination.keyboardModifiers()
    # Main-keyboard Shift+= and numpad + represent the same zoom key.
    if combination.key() == Qt.Key.Key_Plus or (
        combination.key() == Qt.Key.Key_Equal and modifiers & Qt.KeyboardModifier.ShiftModifier):
        return QKeySequence(QKeyCombination(modifiers & ~Qt.KeyboardModifier.ShiftModifier,
                                            Qt.Key.Key_Plus))
    return sequence


def _key_variants(key: str) -> list[QKeySequence]:
    sequence = QKeySequence(key, QKeySequence.SequenceFormat.PortableText)
    variants = [sequence]
    if sequence[0].key() == Qt.Key.Key_Plus:
        shifted = sequence[0].keyboardModifiers() | Qt.KeyboardModifier.ShiftModifier
        variants.extend(QKeySequence(QKeyCombination(shifted, symbol))
                        for symbol in (Qt.Key.Key_Plus, Qt.Key.Key_Equal))
    return variants


def validate_bindings(bindings: dict) -> dict[str, list[str]]:
    """Store portable, single-chord sequences and reject ambiguous assignments."""
    normalized, owners = {}, {}
    for spec in SHORTCUTS:
        values = bindings.get(spec.name, list(spec.defaults))
        if not isinstance(values, (list, tuple)) or len(values) > 2:
            raise ValueError(f"{spec.label}：最多设置两个快捷键")
        normalized[spec.name] = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"{spec.label}：快捷键格式错误")
            if not value:
                continue
            sequence = QKeySequence(value, QKeySequence.SequenceFormat.PortableText)
            if sequence.count() != 1 or sequence[0].key() in (
                Qt.Key.Key_unknown, Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
                Qt.Key.Key_Return, Qt.Key.Key_Enter):
                raise ValueError(f"{spec.label}：请使用单次按键组合，保留 Tab 和回车用于界面操作")
            sequence = _canonical_sequence(sequence)
            key = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            if key in owners:
                raise ValueError(f"快捷键 {sequence.toString()} 冲突：{owners[key]} / {spec.label}")
            owners[key] = spec.label
            normalized[spec.name].append(key)
    return normalized


class ShortcutManager(QObject):
    def __init__(self, root: QWidget, actions: dict[str, QAction], saved=None):
        super().__init__(root)
        self.root, self.actions = root, actions
        self._tips = {name: action.toolTip() for name, action in actions.items()}
        self.bindings = default_bindings()
        try:
            if saved is not None:
                if not isinstance(saved, dict):
                    raise ValueError("快捷键设置格式错误")
                self.bindings = validate_bindings(saved)
        except ValueError as exc:
            logging.getLogger(__name__).warning("Using default shortcuts: %s", exc)
        self.apply(self.bindings)
        QApplication.instance().installEventFilter(self)

    def apply(self, bindings: dict) -> None:
        self.bindings = validate_bindings(bindings)
        for name, action in self.actions.items():
            action.setShortcuts([sequence for key in self.bindings[name] for sequence in _key_variants(key)])
            action.setAutoRepeat(False)
            keys = self.text(name)
            action.setToolTip(self._tips[name] + (f"\n快捷键：{keys}" if keys else "\n未绑定快捷键"))

    def text(self, name: str) -> str:
        return " / ".join(QKeySequence(key, QKeySequence.SequenceFormat.PortableText).toString(
            QKeySequence.SequenceFormat.NativeText) for key in self.bindings[name])

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.ShortcutOverride or not isinstance(watched, QWidget):
            return False
        if watched is not self.root and not self.root.isAncestorOf(watched):
            return False
        # Child dialogs own their keys, including shortcut recording, Save and Cancel.
        if watched.window() is not self.root:
            event.accept()
            return True
        widget = watched
        while widget is not None and widget is not self.root:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox,
                                   QComboBox, QKeySequenceEdit)):
                modifiers = event.modifiers() & (Qt.KeyboardModifier.ControlModifier |
                    Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
                if not modifiers:
                    event.accept()
                    return True
                break
            if isinstance(widget, QAbstractButton) and event.key() == Qt.Key.Key_Space:
                event.accept()
                return True
            widget = widget.parentWidget()
        return False

    def uninstall(self):
        QApplication.instance().removeEventFilter(self)


class ShortcutsDialog(QDialog):
    def __init__(self, parent, bindings: dict, save_bindings):
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.resize(740, 640)
        self.setMinimumSize(640, 440)
        self._save_bindings = save_bindings
        layout = QVBoxLayout(self)
        description = QLabel("点击按键框后直接按下所需组合。框内 × 可清除绑定；主键和备用键均可触发同一功能。\n"
                             "输入文字或数值时，字母、数字和空格用于正常输入。快捷键仅在本程序内生效。")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.table = QTableWidget(len(SHORTCUTS), 3)
        self.table.setHorizontalHeaderLabels(["功能", "主快捷键", "备用键"])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.editors = {}
        for row, spec in enumerate(SHORTCUTS):
            item = QTableWidgetItem(spec.label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            editors = []
            for column in (1, 2):
                editor = QKeySequenceEdit()
                editor.setMaximumSequenceLength(1)
                editor.setClearButtonEnabled(True)
                editor.findChild(QLineEdit).setPlaceholderText("未绑定，点击录入")
                editor.setToolTip("点击后按下单个按键或组合键；点击 × 清除")
                editor.keySequenceChanged.connect(self._validate)
                self.table.setCellWidget(row, column, editor)
                editors.append(editor)
            self.editors[spec.name] = editors
        layout.addWidget(self.table, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #ffd166;")
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                        QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        reset = QPushButton("恢复默认")
        self.buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(lambda: self._set_bindings(default_bindings()))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._set_bindings(bindings)

    def _set_bindings(self, bindings):
        for name, editors in self.editors.items():
            keys = bindings[name]
            for index, editor in enumerate(editors):
                editor.setKeySequence(QKeySequence(keys[index] if index < len(keys) else ""))
        self._validate()

    def bindings(self):
        return {name: [editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                       for editor in editors if not editor.keySequence().isEmpty()]
                for name, editors in self.editors.items()}

    def _validate(self, *args):
        if not hasattr(self, "buttons"):
            return
        try:
            validate_bindings(self.bindings())
            error = ""
        except ValueError as exc:
            error = str(exc)
        self.message.setText(error)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not error)

    def accept(self):
        try:
            self._save_bindings(validate_bindings(self.bindings()))
        except (OSError, ValueError) as exc:
            self.message.setText(f"无法保存快捷键：{exc}")
            return
        super().accept()
