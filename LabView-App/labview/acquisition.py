from __future__ import annotations

from datetime import datetime, timezone
from queue import Empty, Queue
from threading import Event
from time import monotonic, perf_counter_ns
import importlib
import logging
import sys

import numpy as np
from PySide6.QtCore import QThread, Signal

from .models import Frame, LatestBuffer
from .camera_features import apply_features, capture_features
from .camera_profiles import configuration, differences, validate_configuration


LOG = logging.getLogger(__name__)
SUPPORTED_FORMATS = {"BayerRG8", "BayerRG16", "BayerGR8", "BayerGR16",
                     "BayerGB8", "BayerGB16", "BayerBG8", "BayerBG16",
                     "RGB8", "RGB8Packed", "BGR8", "BGR8Packed", "Mono8", "Mono16"}
NUMERIC_NODES = ("ExposureTime", "Gain", "AcquisitionFrameRate", "AutoExposureTargetGreyValue",
                 "AutoExposureExposureTimeUpperLimit", "AutoExposureGainUpperLimit")
ENUM_NODES = ("ExposureAuto", "GainAuto", "BalanceWhiteAuto", "PixelFormat", "AutoExposureTargetGreyValueAuto")
WRITABLE_INTEGERS = ("Width", "Height", "OffsetX", "OffsetY", "BinningHorizontal", "BinningVertical")
GEOMETRY_NODES = ("Width", "Height", "OffsetX", "OffsetY", "BinningHorizontal",
                  "BinningVertical", "DecimationHorizontal", "DecimationVertical")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_spinnaker():
    try:
        spin = importlib.import_module("PySpin")
        if not hasattr(spin, "System"):
            raise ImportError("导入的 PySpin 不是 Teledyne 相机 SDK")
        return spin
    except (ImportError, OSError) as exc:
        if getattr(sys, "frozen", False):
            help_text = ("无法加载相机运行库。请重新运行 2D Viewer 安装程序进行修复，"
                         "并确认已安装配套的 Spinnaker 运行库和 USB3 相机驱动。")
        else:
            help_text = ("无法加载相机 PySpin。请安装完整 Spinnaker SDK，再安装与 SDK、Python 版本"
                         "和 x64 架构匹配的官方 spinnaker_python wheel。")
        raise RuntimeError(f"{help_text}\n原始错误：{exc}") from exc


class CaptureWorker(QThread):
    connected = Signal(dict)
    settings_updated = Signal(dict)
    error = Signal(str)
    disconnected = Signal()
    operation_finished = Signal(dict)

    def __init__(self, buffer: LatestBuffer[Frame], serial: str = "") -> None:
        super().__init__()
        self.buffer, self.serial = buffer, serial.strip()
        self._stop_event = Event()
        self._commands: Queue[dict] = Queue()
        self._settings: dict = {}
        self._configuration: dict = {}
        self._profile_context: dict = {}
        self._profile_baseline: dict | None = None
        self._configuration_generation = 0

    def request_stop(self) -> None:
        self._stop_event.set()

    def configure(self, name: str, value: object) -> None:
        self.configure_many({name: value})

    def configure_many(self, settings: dict, request_id: str = "") -> None:
        self._commands.put(dict(settings, __request_id=request_id))

    def request_snapshot(self, request_id: str) -> None:
        self._commands.put({"__snapshot": True, "__request_id": request_id})

    def configure_profile(self, payload: dict, context: dict, request_id: str, *, baseline=None) -> None:
        self._commands.put({"__profile": validate_configuration(payload), "__context": dict(context),
                            "__request_id": request_id, "__baseline": baseline or payload})

    def configure_context(self, context: dict, baseline: dict | None = None, request_id: str = "") -> None:
        self._commands.put({"__context_only": True, "__context": dict(context),
                            "__baseline": baseline, "__request_id": request_id})

    def _update_profile_status(self):
        self._profile_context = dict(self._profile_context,
            generation=self._configuration_generation,
            temporary=not self._profile_baseline or bool(differences(self._profile_baseline, self._configuration)))

    def run(self) -> None:
        try:
            self._run_camera()
        except Exception as exc:
            LOG.exception("Acquisition failed")
            self.error.emit(str(exc))
        finally:
            self.disconnected.emit()

    def _run_camera(self) -> None:
        spin = load_spinnaker()

        system, cameras, camera = None, None, None
        initialized, acquiring = False, False
        try:
            system = spin.System.GetInstance()
            cameras = system.GetCameras()
            if cameras.GetSize() == 0:
                raise RuntimeError("未找到相机。请检查 USB3 连接，并关闭占用相机的 SpinView。")
            if self.serial:
                camera = cameras.GetBySerial(self.serial)
                if not camera.IsValid():
                    raise RuntimeError(f"未找到序列号 {self.serial} 的相机")
            else:
                matches = []
                for index in range(cameras.GetSize()):
                    candidate = cameras.GetByIndex(index)
                    model = candidate.TLDevice.DeviceModelName.GetValue()
                    if "BFS-U3-51S5C" in model:
                        matches.append(index)
                    del candidate
                if len(matches) != 1:
                    raise RuntimeError("没有唯一的 BFS-U3-51S5C 相机，请填写目标相机序列号。")
                camera = cameras.GetByIndex(matches[0])
            camera.Init()
            initialized = True
            nodemap = camera.GetNodeMap()

            def set_enum(name: str, value: str, required: bool = False) -> None:
                node = spin.CEnumerationPtr(nodemap.GetNode(name))
                if not spin.IsWritable(node):
                    if required:
                        raise RuntimeError(f"相机参数 {name} 不可写")
                    return
                entry = node.GetEntryByName(value)
                if not spin.IsReadable(entry):
                    if required:
                        raise RuntimeError(f"相机不支持 {name}={value}")
                    return
                node.SetIntValue(entry.GetValue())

            set_enum("AcquisitionMode", "Continuous", True)
            set_enum("TriggerMode", "Off")
            current_format = spin.CEnumerationPtr(nodemap.GetNode("PixelFormat"))
            if not spin.IsReadable(current_format) or current_format.GetCurrentEntry().GetSymbolic() not in SUPPORTED_FORMATS:
                set_enum("PixelFormat", "BayerRG8", True)
            del current_format
            stream_map = camera.GetTLStreamNodeMap()
            handling = spin.CEnumerationPtr(stream_map.GetNode("StreamBufferHandlingMode"))
            if spin.IsWritable(handling):
                entry = handling.GetEntryByName("NewestOnly")
                if spin.IsReadable(entry):
                    handling.SetIntValue(entry.GetValue())
                del entry
            del handling, stream_map

            chunk_fields = []
            try:
                active = spin.CBooleanPtr(nodemap.GetNode("ChunkModeActive"))
                if spin.IsWritable(active):
                    active.SetValue(True)
                    selector = spin.CEnumerationPtr(nodemap.GetNode("ChunkSelector"))
                    if spin.IsWritable(selector):
                        for field in ("ExposureTime", "Gain"):
                            entry = selector.GetEntryByName(field)
                            if spin.IsReadable(entry):
                                selector.SetIntValue(entry.GetValue())
                                enable = spin.CBooleanPtr(nodemap.GetNode("ChunkEnable"))
                                if spin.IsWritable(enable):
                                    enable.SetValue(True)
                                    chunk_fields.append(field)
                                del enable
                            del entry
                    del selector
                del active
            except Exception:
                LOG.debug("Per-frame chunks unavailable", exc_info=True)

            def snapshot() -> dict:
                settings, numeric, enums, integers, booleans = {}, {}, {}, {}, {}
                for name in NUMERIC_NODES:
                    node = spin.CFloatPtr(nodemap.GetNode(name))
                    target_auto, previous_target_auto = None, None
                    try:
                        # Blackfly S hides the target value while automatic
                        # target selection is active. Read its capabilities
                        # while stopped, then preserve that camera mode.
                        if name == "AutoExposureTargetGreyValue" and not spin.IsReadable(node):
                            target_auto = spin.CEnumerationPtr(nodemap.GetNode("AutoExposureTargetGreyValueAuto"))
                            if spin.IsWritable(target_auto):
                                off = target_auto.GetEntryByName("Off")
                                if spin.IsReadable(off):
                                    previous_target_auto = target_auto.GetCurrentEntry().GetValue()
                                    target_auto.SetIntValue(off.GetValue())
                                    node = spin.CFloatPtr(nodemap.GetNode(name))
                        if spin.IsReadable(node):
                            settings[name] = node.GetValue()
                            numeric[name] = {"min": node.GetMin(), "max": node.GetMax(),
                                             "value": node.GetValue(), "writable": spin.IsWritable(node)}
                    finally:
                        if previous_target_auto is not None:
                            target_auto.SetIntValue(previous_target_auto)
                for name in ENUM_NODES:
                    node = spin.CEnumerationPtr(nodemap.GetNode(name))
                    if spin.IsReadable(node):
                        settings[name] = node.GetCurrentEntry().GetSymbolic()
                        values = []
                        for entry in node.GetEntries():
                            typed = spin.CEnumEntryPtr(entry)
                            if spin.IsReadable(typed):
                                value = typed.GetSymbolic()
                                if name != "PixelFormat" or value in SUPPORTED_FORMATS:
                                    values.append(value)
                        enums[name] = values
                rate_enabled = spin.CBooleanPtr(nodemap.GetNode("AcquisitionFrameRateEnable"))
                if spin.IsReadable(rate_enabled):
                    settings["AcquisitionFrameRateEnable"] = rate_enabled.GetValue()
                    booleans["AcquisitionFrameRateEnable"] = spin.IsWritable(rate_enabled)
                resulting = spin.CFloatPtr(nodemap.GetNode("AcquisitionResultingFrameRate"))
                if spin.IsReadable(resulting):
                    settings["AcquisitionResultingFrameRate"] = resulting.GetValue()
                link_speed = spin.CIntegerPtr(nodemap.GetNode("DeviceLinkSpeed"))
                if spin.IsReadable(link_speed):
                    settings["DeviceLinkSpeed"] = int(link_speed.GetValue())
                usb_speed = spin.CEnumerationPtr(nodemap.GetNode("U3VCurrentSpeed"))
                if spin.IsReadable(usb_speed):
                    settings["U3VCurrentSpeed"] = usb_speed.GetCurrentEntry().GetSymbolic()
                for name in GEOMETRY_NODES:
                    node = spin.CIntegerPtr(nodemap.GetNode(name))
                    if spin.IsReadable(node):
                        settings[name] = node.GetValue()
                        if name in WRITABLE_INTEGERS:
                            integers[name] = {"min": node.GetMin(), "max": node.GetMax(),
                                              "inc": node.GetInc() if hasattr(node, "GetInc") else 1}
                selector = spin.CEnumerationPtr(nodemap.GetNode("BalanceRatioSelector"))
                if spin.IsWritable(selector):
                    previous = selector.GetCurrentEntry().GetValue()
                    for channel in ("Red", "Blue"):
                        entry = selector.GetEntryByName(channel)
                        if spin.IsReadable(entry):
                            selector.SetIntValue(entry.GetValue())
                            ratio = spin.CFloatPtr(nodemap.GetNode("BalanceRatio"))
                            if spin.IsReadable(ratio):
                                name = "BalanceRatio" + channel
                                settings[name] = ratio.GetValue()
                                numeric[name] = {"min": ratio.GetMin(), "max": ratio.GetMax(), "value": ratio.GetValue()}
                    selector.SetIntValue(previous)
                gamma_enabled = spin.CBooleanPtr(nodemap.GetNode("GammaEnable"))
                if spin.IsReadable(gamma_enabled):
                    settings["GammaEnable"] = gamma_enabled.GetValue()
                features, unavailable = capture_features(spin, nodemap)
                for feature in features:
                    if not feature.get("selectors"):
                        settings.setdefault(feature["name"], feature["value"])
                self._settings = settings
                description = {"model": camera.TLDevice.DeviceModelName.GetValue(),
                        "serial": camera.TLDevice.DeviceSerialNumber.GetValue(),
                        "settings": dict(settings), "numeric": numeric, "enums": enums,
                        "integers": integers, "booleans": booleans,
                        "features": features, "features_complete": not unavailable,
                        "unavailable_features": unavailable,
                        "sensor_size": [int(integers.get("Width", {}).get("max", settings.get("Width", 0)) + settings.get("OffsetX", 0)),
                                        int(integers.get("Height", {}).get("max", settings.get("Height", 0)) + settings.get("OffsetY", 0))]}
                self._configuration = configuration(description)
                self._update_profile_status()
                return description

            self.connected.emit(snapshot())
            camera.BeginAcquisition()
            acquiring = True
            has_frames = False
            last_telemetry, last_image = monotonic(), monotonic()
            while not self._stop_event.is_set():
                while True:
                    try:
                        changes = self._commands.get_nowait()
                    except Empty:
                        break
                    camera.EndAcquisition()
                    acquiring = False
                    request_id = changes.pop("__request_id", "")
                    target = changes.pop("__profile", None)
                    context = changes.pop("__context", None)
                    baseline = changes.pop("__baseline", None)
                    context_only = changes.pop("__context_only", False)
                    snapshot_only = changes.pop("__snapshot", False)
                    previous = self._configuration
                    failure = ""
                    result = None
                    try:
                        if target is not None:
                            if target["settings"].get("PixelFormat", "BayerRG8") not in SUPPORTED_FORMATS:
                                raise ValueError("方案的像素格式不在当前程序支持范围内")
                            errors = apply_features(spin, nodemap, target)
                            if errors:
                                raise ValueError("；".join(errors[:8]))
                            result = snapshot()
                            mismatch = differences(target, self._configuration)
                            if mismatch:
                                raise ValueError("参数回读不一致：" + ", ".join(mismatch[:12]))
                        elif context_only or snapshot_only:
                            result = snapshot()
                        else:
                            result = None
                        if target is not None or context_only or snapshot_only:
                            changes = {}
                        if "AutoExposureTargetGreyValue" in changes:
                            set_enum("AutoExposureTargetGreyValueAuto", "Off")
                        if "AcquisitionFrameRateEnable" in changes:
                            node = spin.CBooleanPtr(nodemap.GetNode("AcquisitionFrameRateEnable"))
                            if not spin.IsWritable(node): raise RuntimeError("相机不支持切换帧率限制")
                            node.SetValue(bool(changes["AcquisitionFrameRateEnable"]))
                            del node
                        # Automatic modes are established before dependent numeric controls.
                        for name in ENUM_NODES:
                            if name in changes: set_enum(name, str(changes[name]), True)
                        for name, value in changes.items():
                            if name in NUMERIC_NODES or name.startswith("BalanceRatio"):
                                if name in ("ExposureTime", "Gain"):
                                    automatic = "ExposureAuto" if name == "ExposureTime" else "GainAuto"
                                    mode = changes.get(automatic, self._settings.get(automatic, "Off"))
                                    if mode != "Off": continue
                                    set_enum(automatic, "Off")
                                if name.startswith("BalanceRatio"):
                                    set_enum("BalanceRatioSelector", name.removeprefix("BalanceRatio"), True)
                                    node_name = "BalanceRatio"
                                else: node_name = name
                                node = spin.CFloatPtr(nodemap.GetNode(node_name))
                                if not spin.IsWritable(node): raise RuntimeError(f"{name} 当前不可写")
                                node.SetValue(float(np.clip(float(value), node.GetMin(), node.GetMax())))
                                del node
                            elif name in ("BinningHorizontal", "BinningVertical"):
                                node = spin.CIntegerPtr(nodemap.GetNode(name))
                                if not spin.IsWritable(node): raise RuntimeError(f"{name} 当前不可写")
                                node.SetValue(int(value))
                                del node
                        if "SensorROI" in changes:
                            for name in ("OffsetX", "OffsetY"):
                                node = spin.CIntegerPtr(nodemap.GetNode(name))
                                if spin.IsWritable(node): node.SetValue(node.GetMin())
                            width_node = spin.CIntegerPtr(nodemap.GetNode("Width"))
                            height_node = spin.CIntegerPtr(nodemap.GetNode("Height"))
                            maximum = (width_node.GetMax(), height_node.GetMax())
                            x, y, width, height = changes["SensorROI"] or (0, 0, *maximum)
                            for name, value in (("Width", width), ("Height", height), ("OffsetX", x), ("OffsetY", y)):
                                node = spin.CIntegerPtr(nodemap.GetNode(name))
                                if not spin.IsWritable(node): raise RuntimeError(f"{name} 当前不可写")
                                increment = node.GetInc() if hasattr(node, "GetInc") else 1
                                value = int(np.clip(value, node.GetMin(), node.GetMax()))
                                node.SetValue(node.GetMin() + (value - node.GetMin()) // increment * increment)
                            del node, width_node, height_node
                    except Exception as exc:
                        failure = str(exc)
                        if target is not None:
                            try:
                                rollback = apply_features(spin, nodemap, previous)
                                if rollback:
                                    failure += "；恢复原参数未完成：" + "；".join(rollback[:4])
                            except Exception as rollback_error:
                                failure += f"；恢复原参数失败：{rollback_error}"
                        self.error.emit(f"相机参数设置失败：{failure}")
                    result = snapshot() if result is None or failure else result
                    if not failure and context is not None:
                        self._profile_context = dict(context)
                        self._profile_baseline = baseline
                    self._configuration_generation += 1
                    self._update_profile_status()
                    self.settings_updated.emit(result)
                    if request_id:
                        self.operation_finished.emit({"request_id": request_id, "ok": not failure,
                            "error": failure, "configuration": self._configuration,
                            "context": dict(self._profile_context)})
                    camera.BeginAcquisition()
                    acquiring = True
                    last_image = monotonic()

                image = None
                try:
                    image = camera.GetNextImage(500)
                    last_image = monotonic()
                    if not image.IsIncomplete():
                        raw = image.GetNDArray().copy()
                        received_ns = perf_counter_ns()
                        raw.setflags(write=False)
                        frame_id = int(image.GetFrameID())
                        white_level = float(np.iinfo(raw.dtype).max)
                        frame_settings = dict(self._settings)
                        actual_fields = []
                        if chunk_fields:
                            try:
                                chunks = image.GetChunkData()
                                for field in chunk_fields:
                                    try:
                                        frame_settings[field] = float(getattr(chunks, "Get" + field)())
                                        actual_fields.append(field)
                                    except Exception:
                                        LOG.debug("Chunk %s unavailable", field)
                                del chunks
                            except Exception:
                                LOG.debug("Image chunks unavailable")
                        self.buffer.put(Frame(raw, image.GetPixelFormatName(), white_level,
                                              frame_id, utc_now(), camera.TLDevice.DeviceSerialNumber.GetValue(),
                                              int(image.GetTimeStamp()), frame_settings, tuple(actual_fields), received_ns,
                                              self._configuration, dict(self._profile_context)))
                        has_frames = True
                except spin.SpinnakerException as exc:
                    if getattr(exc, "errorcode", None) != getattr(spin, "SPINNAKER_ERR_TIMEOUT", -1011):
                        raise
                    exposure_seconds = float(self._settings.get("ExposureTime", 0)) / 1e6
                    if monotonic() - last_image > max(15, exposure_seconds * 2 + 5):
                        raise RuntimeError("相机长时间未返回图像，请检查连接、曝光及 USB 带宽。") from exc
                finally:
                    if image is not None:
                        image.Release()
                        del image
                if monotonic() - last_telemetry >= 1:
                    resulting = spin.CFloatPtr(nodemap.GetNode("AcquisitionResultingFrameRate"))
                    if spin.IsReadable(resulting):
                        self._settings["AcquisitionResultingFrameRate"] = resulting.GetValue()
                    del resulting
                    # Read automatic controls for UI feedback without touching pending manual inputs.
                    completed_once = False
                    for name in ("ExposureAuto", "GainAuto", "BalanceWhiteAuto"):
                        node = spin.CEnumerationPtr(nodemap.GetNode(name))
                        if spin.IsReadable(node):
                            mode = node.GetCurrentEntry().GetSymbolic()
                            completed_once |= self._settings.get(name) == "Once" and mode == "Off"
                            self._settings[name] = mode
                    telemetry = dict(frame_settings) if has_frames else dict(self._settings)
                    self._configuration = dict(self._configuration,
                        settings=dict(self._configuration.get("settings", {}), **{
                            name: self._settings[name] for name in ("ExposureAuto", "GainAuto", "BalanceWhiteAuto")
                            if name in self._settings}))
                    self._update_profile_status()
                    telemetry["AcquisitionResultingFrameRate"] = self._settings.get("AcquisitionResultingFrameRate")
                    self.settings_updated.emit({"telemetry": telemetry,
                                                "modes": {n: self._settings.get(n, "Off") for n in ("ExposureAuto", "GainAuto", "BalanceWhiteAuto")}})
                    if completed_once:
                        camera.EndAcquisition()
                        acquiring = False
                        self.settings_updated.emit(snapshot())
                        camera.BeginAcquisition()
                        acquiring = True
                    has_frames, last_telemetry = False, monotonic()
            del nodemap
        finally:
            if camera is not None:
                if acquiring:
                    try:
                        camera.EndAcquisition()
                    except Exception:
                        LOG.exception("Could not stop acquisition")
                if initialized:
                    try:
                        camera.DeInit()
                    except Exception:
                        LOG.exception("Could not deinitialize camera")
                camera = None
            if cameras is not None:
                cameras.Clear()
                cameras = None
            if system is not None:
                system.ReleaseInstance()
