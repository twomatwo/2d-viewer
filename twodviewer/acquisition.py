"""Qt capture worker delegates device operations to a camera adapter."""
from __future__ import annotations

import logging
from PySide6.QtCore import QThread, Signal

from .camera_backends import CameraCallbacks, create_adapter
from .camera_backends.spinnaker import load_spinnaker  # compatibility for existing diagnostics
from .models import Frame, LatestBuffer

LOG = logging.getLogger(__name__)


class CaptureWorker(QThread):
    connected = Signal(dict)
    settings_updated = Signal(dict)
    error = Signal(str)
    disconnected = Signal()
    operation_finished = Signal(dict)

    def __init__(self, buffer: LatestBuffer[Frame], serial: str = "", *, backend="spinnaker") -> None:
        super().__init__()
        self.buffer, self.serial = buffer, serial.strip()
        self._adapter = create_adapter(buffer, self.serial, CameraCallbacks(
            self.connected.emit, self.settings_updated.emit, self.error.emit,
            self.operation_finished.emit), backend)

    def request_stop(self) -> None:
        self._adapter.request_stop()

    def configure(self, name: str, value: object) -> None:
        self.configure_many({name: value})

    def configure_many(self, settings: dict, request_id: str = "") -> None:
        self._adapter.configure_many(settings, request_id)

    def request_snapshot(self, request_id: str) -> None:
        self._adapter.request_snapshot(request_id)

    def configure_profile(self, payload: dict, context: dict, request_id: str, *, baseline=None) -> None:
        self._adapter.configure_profile(payload, context, request_id, baseline=baseline)

    def configure_context(self, context: dict, baseline: dict | None = None, request_id: str = "") -> None:
        self._adapter.configure_context(context, baseline, request_id)

    def run(self) -> None:
        try:
            self._adapter.run()
        except Exception as exc:
            LOG.exception("Acquisition failed")
            self.error.emit(str(exc))
        finally:
            self.disconnected.emit()
