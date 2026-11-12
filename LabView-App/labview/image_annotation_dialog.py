"""Image overlay import with initial opacity and geometric placement preview."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFormLayout, QLabel, QVBoxLayout)

from .image_assets import decode_image_asset
from .models import Annotation


class ImageAnnotationDialog(QDialog):
    def __init__(self, parent, data, name, background, center, scale):
        super().__init__(parent)
        self.setWindowTitle("导入图片标注")
        self.setMinimumWidth(480)
        self.setMaximumWidth(620)
        self.data, self.name, self.background = data, name, background
        self.image = decode_image_asset(data)
        self.center = QPointF(center)
        layout = QVBoxLayout(self)
        filename = QLabel(name)
        filename.setWordWrap(True)
        layout.addWidget(filename)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)
        form = QFormLayout()
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix(" %")
        self.opacity.setValue(50)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(.1, 10000)
        self.scale.setDecimals(2)
        self.scale.setSuffix(" %")
        self.scale.setValue(max(.1, scale * 100))
        self.rotation = QDoubleSpinBox()
        self.rotation.setRange(-360, 360)
        self.rotation.setSuffix(" °")
        for title, box in (("不透明度", self.opacity), ("等比例缩放（相对原图像素）", self.scale),
                           ("旋转角度", self.rotation)):
            box.setKeyboardTracking(False)
            form.addRow(title, box)
            box.valueChanged.connect(self._update_preview)
        layout.addLayout(form)
        hint = QLabel("0% 为完全透明，100% 为不透明。PNG 自带透明区域会保留。导入后可继续移动、缩放、旋转或组合。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_preview()

    def image_transform(self):
        transform = QTransform()
        transform.translate(self.center.x(), self.center.y())
        transform.rotate(self.rotation.value())
        transform.scale(self.scale.value() / 100, self.scale.value() / 100)
        transform.translate(-self.image.width() / 2, -self.image.height() / 2)
        return transform

    def annotation(self):
        matrix = self.image_transform()
        return Annotation("image", ((0., 0.), (float(self.image.width()), float(self.image.height()))),
                          image_name=self.name, image_data=self.data, image_opacity=self.opacity.value() / 100,
                          transform=(matrix.m11(), matrix.m12(), matrix.m21(), matrix.m22(), matrix.dx(), matrix.dy()))

    def _update_preview(self):
        output = QImage(440, 250, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(QColor("#0c1018"))
        painter = QPainter(output)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        factor = min(output.width() / self.background.width(), output.height() / self.background.height())
        painter.translate((output.width() - self.background.width() * factor) / 2,
                          (output.height() - self.background.height() * factor) / 2)
        painter.scale(factor, factor)
        painter.drawImage(QPointF(0, 0), self.background)
        painter.setOpacity(self.opacity.value() / 100)
        painter.setTransform(self.image_transform(), True)
        painter.drawImage(QRectF(0, 0, self.image.width(), self.image.height()), self.image)
        painter.end()
        self.preview.setPixmap(QPixmap.fromImage(output))
