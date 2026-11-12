"""Keep scrolling a panel from editing its parameters by accident."""
from __future__ import annotations

from weakref import ref

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QSignalBlocker, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox,
                              QLineEdit, QScrollArea, QSlider, QWidget,
                              QHeaderView, QTreeWidget, QTreeWidgetItem, QButtonGroup,
                              QHBoxLayout, QToolButton, QFrame)


class ObjectiveSelector(QScrollArea):
    """One-click objective buttons; long custom lists remain horizontally accessible."""
    objective_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(34)
        self._content = QWidget()
        self._layout = QHBoxLayout(self._content)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.setWidget(self._content)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.buttonClicked.connect(lambda button: self.objective_clicked.emit(button.property("objectiveId")))
        self.buttons = {}
        self._options = []

    def configure(self, options, selected):
        if options != self._options:
            for button in self.buttons.values():
                self._group.removeButton(button)
                self._layout.removeWidget(button)
                button.hide()
                button.deleteLater()
            self.buttons = {}
            for name, identity in options:
                button = QToolButton(self._content)
                button.setText(name)
                button.setCheckable(True)
                button.setProperty("objectiveId", identity)
                button.setStyleSheet("QToolButton { padding: 7px 6px; min-width: 26px; }")
                button.setToolTip(f"切换到 {name}，载入该物镜的相机参数和标定。物镜较多时可滚轮横向浏览。")
                self._group.addButton(button)
                self._layout.addWidget(button)
                self.buttons[identity] = button
            self._options = list(options)
            self._content.adjustSize()
            self.setFixedWidth(min(420, self._content.sizeHint().width()))
        button = self.buttons.get(selected)
        if button is not None:
            button.setChecked(True)
            self.ensureWidgetVisible(button, 0, 0)

    def wheelEvent(self, event):
        delta = event.angleDelta().x() or event.angleDelta().y()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - delta)
        event.accept()


class ObjectiveList(QTreeWidget):
    """An exclusive checkbox list with the scale visible on every objective row."""
    currentTextChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self._selected = ""
        self.setColumnCount(3)
        self.setHeaderLabels(["物镜", "每像素长度", "状态"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setMinimumHeight(192)
        self.setMaximumHeight(240)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.itemClicked.connect(lambda item, column: self.setCurrentText(item.text(0)))
        self.itemChanged.connect(self._check_changed)

    def addItem(self, name):
        if self.findText(name) >= 0:
            return
        with QSignalBlocker(self):
            item = QTreeWidgetItem(self, [name, "—", "未标定"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)

    def addItems(self, names):
        for name in names:
            self.addItem(name)

    def findText(self, name):
        return next((i for i in range(self.topLevelItemCount()) if self.topLevelItem(i).text(0) == name), -1)

    def currentText(self):
        return self._selected

    def setCurrentText(self, name):
        index = self.findText(name)
        if index < 0:
            return
        changed = self._selected != name
        self._selected = name
        with QSignalBlocker(self):
            for i in range(self.topLevelItemCount()):
                self.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked if i == index else Qt.CheckState.Unchecked)
            self.setCurrentItem(self.topLevelItem(index))
        if changed:
            self.currentTextChanged.emit(name)

    def _check_changed(self, item, column):
        if column != 0:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self.setCurrentText(item.text(0))
        elif item.text(0) == self._selected:
            self.setCurrentText(self._selected)


class ClickToEditWheelGuard(QObject):
    """Parameter wheels require explicit selection; other wheels scroll the panel."""

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self._root = ref(root)
        for widget in root.findChildren(QWidget):
            if isinstance(widget, (QAbstractSpinBox, QComboBox, QSlider)):
                widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        QApplication.instance().installEventFilter(self)

    @staticmethod
    def _control(widget):
        if isinstance(widget, (QAbstractSpinBox, QComboBox, QSlider)):
            return widget
        if isinstance(widget, QLineEdit) and isinstance(widget.parentWidget(), QAbstractSpinBox):
            return widget.parentWidget()
        return None

    def eventFilter(self, watched, event):
        root = self._root()
        if root is None or not isinstance(watched, QWidget) or not root.isAncestorOf(watched):
            return False
        control = self._control(watched)
        if control is None:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            control.setProperty("wheelSelected", True)
        elif kind == QEvent.Type.FocusIn:
            control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
                control.setProperty("wheelSelected", True)
        elif kind == QEvent.Type.FocusOut:
            if event.reason() != Qt.FocusReason.PopupFocusReason:
                control.setProperty("wheelSelected", False)
        elif kind == QEvent.Type.Wheel:
            focus = QApplication.focusWidget()
            focused = control.hasFocus() or (focus is not None and control.isAncestorOf(focus))
            if focused and control.property("wheelSelected"):
                return False
            event.ignore()
            # Forward to the surrounding panel so hovering a field still scrolls normally.
            parent = control.parentWidget()
            while parent is not None and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                viewport = parent.viewport()
                forwarded = QWheelEvent(
                    QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(), event.phase(), event.inverted(), event.source())
                QApplication.sendEvent(viewport, forwarded)
            return True
        return False

    def uninstall(self):
        QApplication.instance().removeEventFilter(self)
