"""Reusable dialogs for recording and timed capture tasks."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout


def explanation(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("muted")
    return label


class TaskDialog(QDialog):
    def __init__(self, parent, title: str, description: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.setMaximumWidth(620)
        layout = QVBoxLayout(self)
        layout.addWidget(explanation(description))
        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(self.form)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.close)
        layout.addWidget(close)

    def reveal(self):
        self.show()
        self.raise_()
        self.activateWindow()
