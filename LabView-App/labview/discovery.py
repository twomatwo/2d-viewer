from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .acquisition import load_spinnaker


class DiscoveryWorker(QThread):
    found = Signal(list)
    error = Signal(str)

    def run(self):
        system, cameras, camera = None, None, None
        try:
            spin = load_spinnaker()
            system = spin.System.GetInstance()
            cameras = system.GetCameras()
            devices = []
            for index in range(cameras.GetSize()):
                camera = cameras.GetByIndex(index)
                devices.append({"model": camera.TLDevice.DeviceModelName.GetValue(),
                                "serial": camera.TLDevice.DeviceSerialNumber.GetValue()})
                camera = None
            self.found.emit(devices)
        except Exception as exc:
            self.error.emit(f"扫描相机失败：{exc}")
        finally:
            camera = None
            if cameras is not None: cameras.Clear()
            if system is not None: system.ReleaseInstance()
