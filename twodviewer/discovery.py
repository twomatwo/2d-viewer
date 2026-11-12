"""Device discovery through the same backend used by the capture worker."""
from PySide6.QtCore import QThread, Signal

from .camera_backends import discover_devices


class DiscoveryWorker(QThread):
    found = Signal(list)
    error = Signal(str)

    def __init__(self, *, backend="spinnaker"):
        super().__init__()
        self.backend = backend

    def run(self):
        try:
            self.found.emit(discover_devices(self.backend))
        except Exception as exc:
            self.error.emit(f"扫描相机失败：{exc}")
