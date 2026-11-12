"""Camera backend entry points. Importing the application never loads a camera SDK."""
from .base import CameraCallbacks, CameraAdapter
from .spinnaker import SpinnakerAdapter, discover_devices as _discover_spinnaker


def create_adapter(buffer, serial, callbacks, backend="spinnaker") -> CameraAdapter:
    if backend != "spinnaker":
        raise ValueError(f"未知相机后端：{backend}")
    return SpinnakerAdapter(buffer, serial, callbacks)


def discover_devices(backend="spinnaker"):
    if backend != "spinnaker":
        raise ValueError(f"未知相机后端：{backend}")
    return _discover_spinnaker()
