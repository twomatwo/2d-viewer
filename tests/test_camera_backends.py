"""Backend contract and SDK-boundary regression tests; never open real hardware."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from twodviewer.acquisition import CaptureWorker
from twodviewer.camera_backends import CameraCallbacks, create_adapter, discover_devices
from twodviewer.camera_backends.spinnaker import SpinnakerAdapter, preferred_formats, select_camera
from twodviewer.models import LatestBuffer
from twodviewer.camera_features import apply_features
from twodviewer.main_window import MainWindow
from pathlib import Path
import tempfile


class Node:
    def __init__(self, value=None, choices=(), writable=False):
        self.value, self.choices, self.writable = value, list(choices), writable
    def GetValue(self): return self.value
    def SetValue(self, value): self.value = value
    def GetSymbolic(self): return self.value
    def GetCurrentEntry(self): return Node(self.value)
    def GetEntryByName(self, name): return Node(name if name in self.choices else None)
    def GetEntries(self): return [Node(value) for value in self.choices]
    def SetIntValue(self, value):
        if not self.writable: raise ValueError("read-only")
        self.value = value
    def GetMin(self): return 1 if self.value is not None else 0
    def GetMax(self): return self.value
    def GetInc(self): return 1


class NodeMap:
    def __init__(self, nodes=None): self.nodes = nodes or {}
    def GetNode(self, name): return self.nodes.get(name, Node())
    def GetNodes(self): return []


class CameraList:
    def __init__(self, cameras): self.cameras, self.cleared = cameras, False
    def GetSize(self): return len(self.cameras)
    def GetByIndex(self, index): return self.cameras[index]
    def GetBySerial(self, serial):
        return next((c for c in self.cameras if c.TLDevice.DeviceSerialNumber.GetValue() == serial),
                    SimpleNamespace(IsValid=lambda: False))
    def Clear(self): self.cleared = True


class Camera:
    def __init__(self, model="Other FLIR Mono", serial="serial-a", pixel_format="Mono16", writable=False,
                 choices=("Mono16", "Mono8")):
        self.TLDevice = SimpleNamespace(DeviceModelName=Node(model), DeviceSerialNumber=Node(serial))
        self.map = NodeMap({"AcquisitionMode": Node("Continuous", ("Continuous",)),
                            "PixelFormat": Node(pixel_format, choices, writable),
                            "Width": Node(3), "Height": Node(2)})
        self.initialized = self.deinitialized = self.started = self.stopped = self.released = False
        self.raw = np.arange(6, dtype=np.uint16).reshape(2, 3)
    def IsValid(self): return True
    def Init(self): self.initialized = True
    def DeInit(self): self.deinitialized = True
    def GetNodeMap(self): return self.map
    def GetTLStreamNodeMap(self): return NodeMap()
    def BeginAcquisition(self): self.started = True
    def EndAcquisition(self): self.stopped = True
    def GetNextImage(self, timeout): return self
    def IsIncomplete(self): return False
    def GetNDArray(self): return self.raw
    def GetFrameID(self): return 1
    def GetPixelFormatName(self): return self.map.GetNode("PixelFormat").value
    def GetTimeStamp(self): return 123
    def Release(self): self.released = True; self.adapter.request_stop()


def sdk_for(cameras):
    camera_list = CameraList(cameras)
    system = SimpleNamespace(GetCameras=lambda: camera_list, released=False)
    def release(): system.released = True
    system.ReleaseInstance = release
    sdk = SimpleNamespace(System=SimpleNamespace(GetInstance=lambda: system),
        IsReadable=lambda n: n is not None and n.value is not None,
        IsWritable=lambda n: n is not None and n.writable,
        CFloatPtr=lambda n:n, CIntegerPtr=lambda n:n, CBooleanPtr=lambda n:n,
        CEnumerationPtr=lambda n:n, CEnumEntryPtr=lambda n:n,
        intfIFloat=1, intfIInteger=2, intfIBoolean=3, intfIEnumeration=4, intfIString=5)
    return sdk, system, camera_list


class BackendTests(unittest.TestCase):
    def make_adapter(self, camera):
        self.descriptions, self.errors, self.results = [], [], []
        adapter = SpinnakerAdapter(LatestBuffer(), "", CameraCallbacks(
            self.descriptions.append, self.descriptions.append, self.errors.append, self.results.append))
        camera.adapter = adapter
        return adapter

    def test_import_does_not_load_sdk(self):
        result = subprocess.run([sys.executable, "-c", "import sys; import twodviewer.acquisition; import twodviewer.discovery; assert 'PySpin' not in sys.modules"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_single_camera_any_model(self):
        camera = Camera()
        self.assertIs(select_camera(CameraList([camera])), camera)

    def test_multiple_cameras_require_selection(self):
        with self.assertRaisesRegex(RuntimeError, "2"):
            select_camera(CameraList([Camera(), Camera(serial="serial-b")]))

    def test_explicit_selection(self):
        a, b = Camera(), Camera(serial="serial-b")
        self.assertIs(select_camera(CameraList([a,b]), "serial-b"), b)

    def test_missing_serial_does_not_fall_back(self):
        with self.assertRaisesRegex(RuntimeError, "not-present"):
            select_camera(CameraList([Camera()]), "not-present")

    def test_no_camera(self):
        with self.assertRaisesRegex(RuntimeError, "Spinnaker"):
            select_camera(CameraList([]))

    def test_preserve_supported_format(self):
        self.assertEqual(preferred_formats("BayerRG16", ["Mono8", "BayerRG16"]), ["BayerRG16"])

    def test_unpack_monochrome_preserves_family_and_precision(self):
        self.assertEqual(preferred_formats("Mono12p", ["RGB8", "Mono8", "Mono16"])[0], "Mono16")

    def test_unpack_color_preserves_cfa(self):
        self.assertEqual(preferred_formats("BayerBG12Packed", ["BayerRG8", "BayerBG16"])[0], "BayerBG16")

    def test_no_compatible_pixel_format(self):
        self.assertEqual(preferred_formats("Mono12p", ["Mono12p"]), [])

    def test_factory_rejects_unknown_backend(self):
        with self.assertRaises(ValueError): create_adapter(None, "", None, "unknown")
        with self.assertRaises(ValueError): discover_devices("unknown")

    def test_fixed_mono_camera_without_optional_nodes(self):
        camera = Camera(); adapter = self.make_adapter(camera); sdk, system, cameras = sdk_for([camera])
        with patch("twodviewer.camera_backends.spinnaker.load_spinnaker", return_value=sdk): adapter.run()
        description = self.descriptions[0]
        self.assertEqual(description["numeric"], {})
        self.assertNotIn("BalanceWhiteAuto", description["enums"])
        self.assertFalse(description["enum_writable"]["PixelFormat"])
        self.assertFalse(description["integers"]["Width"]["writable"])
        _, frame = adapter.buffer.get()
        self.assertEqual(frame.pixel_format, "Mono16")
        self.assertEqual(frame.raw.dtype, np.uint16)
        self.assertFalse(frame.raw.flags.writeable)
        self.assertFalse(np.shares_memory(frame.raw, camera.raw))
        self.assertTrue(camera.released and camera.stopped and camera.deinitialized and cameras.cleared and system.released)

    def test_packed_camera_chooses_available_unpacked_format(self):
        camera = Camera(pixel_format="Mono12p", writable=True, choices=("Mono12p", "Mono16"))
        adapter = self.make_adapter(camera); sdk, _, _ = sdk_for([camera])
        with patch("twodviewer.camera_backends.spinnaker.load_spinnaker", return_value=sdk): adapter.run()
        self.assertEqual(adapter.buffer.get()[1].pixel_format, "Mono16")

    def test_open_failure_releases_handles(self):
        camera = Camera(); adapter = self.make_adapter(camera); sdk, system, cameras = sdk_for([camera])
        camera.GetNodeMap = lambda: (_ for _ in ()).throw(RuntimeError("nodemap unavailable"))
        with patch("twodviewer.camera_backends.spinnaker.load_spinnaker", return_value=sdk):
            with self.assertRaisesRegex(RuntimeError, "nodemap unavailable"): adapter.run()
        self.assertTrue(camera.deinitialized and cameras.cleared and system.released)
        self.assertFalse(camera.started)

    def test_discovery_failure_releases_handles(self):
        camera = Camera(); sdk, system, cameras = sdk_for([camera])
        camera.TLDevice.DeviceModelName.GetValue = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
        with patch("twodviewer.camera_backends.spinnaker.load_spinnaker", return_value=sdk):
            with self.assertRaises(RuntimeError): discover_devices()
        self.assertTrue(cameras.cleared and system.released)

    def test_profile_retries_controls_after_enabling_final_auto_mode(self):
        mode = Node("Continuous", ("Off", "Continuous"), True)
        class AutoMode(Node):
            def GetPrincipalInterfaceType(self): return 4
            def IsStreamable(self): return True
            def IsSelector(self): return False
        mode.__class__ = AutoMode
        class Dependent(Node):
            def GetPrincipalInterfaceType(self): return 1
            def GetName(self): return "AutoExposureEVCompensation"
            def IsStreamable(self): return True
            def IsSelector(self): return False
        control = Dependent(0.)
        nodemap = NodeMap({"ExposureAuto":mode, "AutoExposureEVCompensation":control})
        sdk, _, _ = sdk_for([])
        sdk.IsReadable = lambda n: n.value is not None and (n is not control or mode.value == "Continuous")
        sdk.IsWritable = lambda n: n.writable if n is not control else mode.value == "Continuous"
        payload = {"settings":{"ExposureAuto":"Continuous"},"features":[
            {"name":"AutoExposureEVCompensation","value":1.25,"selectors":[]}]}
        self.assertEqual(apply_features(sdk, nodemap, payload), [])
        self.assertEqual(mode.value,"Continuous")
        self.assertEqual(control.value,1.25)

    def test_queue_and_profile_contract(self):
        camera = Camera(); adapter = self.make_adapter(camera)
        adapter.configure_many({"Gain": 2}, "control")
        adapter.request_snapshot("snapshot")
        self.assertEqual(adapter._commands.get()["__request_id"], "control")
        self.assertTrue(adapter._commands.get()["__snapshot"])
        adapter._configuration = {"schema":"x", "camera":{"serial":"a"}, "settings":{"Gain":2}}
        adapter.configure_context({"objective_id":"o"}, None, "context")
        self.assertTrue(adapter._try_context_switch(adapter._commands.get()))
        self.assertEqual(self.results[-1]["context"]["objective_id"], "o")


class WorkerAndUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_worker_keeps_public_commands_and_qt_signals(self):
        calls=[]
        class Adapter:
            def run(self): raise RuntimeError("device error")
            def request_stop(self): calls.append("stop")
            def configure_many(self,*a): calls.append(("configure",a))
            def request_snapshot(self,*a): calls.append(("snapshot",a))
            def configure_profile(self,*a,**kw): calls.append(("profile",a,kw))
            def configure_context(self,*a): calls.append(("context",a))
        with patch("twodviewer.acquisition.create_adapter", return_value=Adapter()): worker=CaptureWorker(LatestBuffer())
        errors=[];disconnected=[]
        worker.error.connect(errors.append, Qt.ConnectionType.DirectConnection)
        worker.disconnected.connect(lambda:disconnected.append(True), Qt.ConnectionType.DirectConnection)
        worker.configure("Gain",2);worker.request_snapshot("x");worker.configure_profile({}, {}, "x");worker.configure_context({},None,"x");worker.request_stop()
        with self.assertLogs("twodviewer.acquisition",level="ERROR"): worker.run()
        self.assertEqual(errors,["device error"]);self.assertEqual(disconnected,[True]);self.assertEqual(len(calls),5)

    def test_ui_uses_capabilities_and_never_selects_first_of_many(self):
        with tempfile.TemporaryDirectory() as folder:
            w=MainWindow(data_directory=Path(folder))
            try:
                devices=[{"model":"FLIR A","serial":"a"},{"model":"FLIR B","serial":"b"}]
                w._devices_found(devices);self.assertEqual(w.source_combo.currentData(), "")
                w.source_combo.setCurrentIndex(2);w._devices_found(devices);self.assertEqual(w.source_combo.currentData(),"b")
                w._devices_found(devices[:1]);self.assertEqual(w.source_combo.currentData(),"a")
                w._set_camera_controls(True)
                description={"model":"FLIR Mono","serial":"a","settings":{"PixelFormat":"Mono16","Width":3,"Height":2},
                    "numeric":{"ExposureTime":{"min":1,"max":1000,"value":10,"writable":False}},
                    "enums":{"PixelFormat":["Mono16"]},"enum_writable":{"PixelFormat":False},
                    "integers":{"Width":{"min":1,"max":3,"writable":False},"Height":{"min":1,"max":2,"writable":False}},
                    "booleans":{},"sensor_size":[3,2],"features":[],"features_complete":True}
                w._camera_settings_updated(description)
                self.assertFalse(w.numeric_boxes["ExposureTime"].isEnabled());self.assertFalse(w.exposure_slider.isEnabled())
                self.assertFalse(w.auto_wb_check.isEnabled());self.assertFalse(w.once_wb_button.isEnabled())
                self.assertFalse(w.numeric_boxes["BalanceRatioRed"].isEnabled());self.assertFalse(w.enum_boxes["PixelFormat"].isEnabled())
                self.assertFalse(w.apply_roi_button.isEnabled());self.assertFalse(w.resolution_combo.isEnabled())
                self.assertIn("—",w._camera_readout({}))
                description["numeric"] = {"AcquisitionFrameRate":{"min":1,"max":120,"value":30,"writable":True}}
                description["settings"]["AcquisitionFrameRate"] = 30
                w._camera_settings_updated(description)
                self.assertTrue(w.numeric_boxes["AcquisitionFrameRate"].isEnabled())
                self.assertFalse(w.frame_rate_check.isEnabled())
                w._camera_settings_updated({"telemetry":{"AcquisitionFrameRate":30},"modes":{}})
                self.assertNotIn("BalanceWhiteAuto",w._camera_description["settings"])
            finally: w.close();self.app.processEvents()


if __name__ == "__main__": unittest.main()
