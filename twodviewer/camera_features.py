"""GenICam imaging values, including SDK-described selector combinations.

Only the acquisition thread may call this module. Commands/registers and
transport/runtime control nodes are deliberately never serialized. The BFS
firmware does not mark most imaging parameters streamable, so standard imaging
categories supplement that flag.
"""
from __future__ import annotations

from itertools import product

from .camera_profiles import AUTO_MODES, automatic, equal_value, inactive_feature

CORE_SETTINGS = {"ExposureAuto", "ExposureTime", "GainAuto", "Gain", "BalanceWhiteAuto",
    "BalanceRatioRed", "BalanceRatioBlue", "PixelFormat", "AcquisitionFrameRateEnable",
    "AcquisitionFrameRate", "AutoExposureTargetGreyValue", "AutoExposureTargetGreyValueAuto",
    "AutoExposureExposureTimeUpperLimit", "AutoExposureGainUpperLimit", "Width", "Height",
    "OffsetX", "OffsetY", "BinningHorizontal", "BinningVertical", "DecimationHorizontal",
    "DecimationVertical", "GammaEnable", "Gamma"}
IMAGING_CATEGORIES = ("AcquisitionControl", "AnalogControl", "ImageFormatControl",
    "ColorTransformationControl", "AutoAlgorithmControl", "FlatFieldCorrectionControl",
    "DefectivePixelCorrection", "LUTControl")
DERIVED_VALUES = {"SensorWidth", "SensorHeight", "WidthMax", "HeightMax", "PixelSize",
    "PixelColorFilter", "PixelDynamicRangeMin", "PixelDynamicRangeMax",
    "AcquisitionResultingFrameRate", "AcquisitionLineRate"}


def imaging_names(spin, nodemap):
    names = set(CORE_SETTINGS) | {"DeviceLinkThroughputLimit", "DeviceLinkBandwidthReserve"}
    if not hasattr(spin, "intfICategory"):
        return names

    def visit(node):
        if node is None:
            return
        if node.GetPrincipalInterfaceType() == spin.intfICategory:
            for child in spin.CCategoryPtr(node).GetFeatures():
                visit(child)
        else:
            names.add(node.GetName())
    for category in IMAGING_CATEGORIES:
        visit(nodemap.GetNode(category))
    return names - DERIVED_VALUES


def read_value(spin, node):
    kind = node.GetPrincipalInterfaceType()
    if kind == spin.intfIFloat:
        return float(spin.CFloatPtr(node).GetValue())
    if kind == spin.intfIInteger:
        return int(spin.CIntegerPtr(node).GetValue())
    if kind == spin.intfIBoolean:
        return bool(spin.CBooleanPtr(node).GetValue())
    if kind == spin.intfIEnumeration:
        return str(spin.CEnumerationPtr(node).GetCurrentEntry().GetSymbolic())
    if kind == spin.intfIString:
        return str(spin.CStringPtr(node).GetValue())
    raise ValueError("不是可持久化标量节点")


def write_value(spin, node, value):
    if spin.IsReadable(node) and equal_value(read_value(spin, node), value):
        return
    if not spin.IsWritable(node):
        raise ValueError(f"{node.GetName()} 当前不可写")
    kind = node.GetPrincipalInterfaceType()
    if kind == spin.intfIFloat:
        spin.CFloatPtr(node).SetValue(float(value))
    elif kind == spin.intfIInteger:
        spin.CIntegerPtr(node).SetValue(int(value))
    elif kind == spin.intfIBoolean:
        spin.CBooleanPtr(node).SetValue(bool(value))
    elif kind == spin.intfIEnumeration:
        pointer = spin.CEnumerationPtr(node)
        entry = pointer.GetEntryByName(str(value))
        if not spin.IsReadable(entry):
            raise ValueError(f"不支持 {node.GetName()}={value}")
        pointer.SetIntValue(entry.GetValue())
    elif kind == spin.intfIString:
        spin.CStringPtr(node).SetValue(str(value))
    else:
        raise ValueError(f"{node.GetName()} 不是可写标量节点")


def _selectors(node):
    result, seen = [], set()

    def visit(target):
        for selector in target.GetSelectingFeatures():
            name = selector.GetName()
            if name in seen:
                continue
            seen.add(name)
            visit(selector)
            result.append(selector)
    visit(node)
    return result


def _choices(spin, selector):
    kind = selector.GetPrincipalInterfaceType()
    if kind == spin.intfIEnumeration and spin.IsWritable(selector):
        return [str(spin.CEnumEntryPtr(entry).GetSymbolic())
                for entry in spin.CEnumerationPtr(selector).GetEntries() if spin.IsReadable(entry)]
    if kind == spin.intfIInteger and spin.IsWritable(selector):
        pointer = spin.CIntegerPtr(selector)
        return range(pointer.GetMin(), pointer.GetMax() + 1, max(1, pointer.GetInc()))
    return [read_value(spin, selector)]


def capture_features(spin, nodemap) -> tuple[list[dict], list[str]]:
    entries, unavailable = [], []
    scope = imaging_names(spin, nodemap)
    allowed = {spin.intfIFloat, spin.intfIInteger, spin.intfIBoolean,
               spin.intfIEnumeration, spin.intfIString}
    for node in nodemap.GetNodes():
        if ((not node.IsStreamable() and node.GetName() not in scope)
                or node.GetPrincipalInterfaceType() not in allowed or not spin.IsAvailable(node)):
            continue
        name, originals = node.GetName(), []
        try:
            selectors = _selectors(node)
            originals = [(selector, read_value(spin, selector)) for selector in selectors]
            if node.IsSelector():
                if spin.IsReadable(node):
                    entries.append({"name": name, "value": read_value(spin, node),
                        "selectors": [[selector.GetName(), value] for selector, value in originals]})
                continue
            choices = [_choices(spin, selector) for selector in selectors]
            count = 1
            for values in choices:
                count *= len(values)
            if count > 100000 or len(entries) + count > 100000:
                raise ValueError("选择器组合过多，未完整保存")
            for combination in product(*choices):
                try:
                    selected = []
                    for selector, value in zip(selectors, combination):
                        write_value(spin, selector, value)
                        selected.append([selector.GetName(), value])
                    if spin.IsReadable(node):
                        entries.append({"name": name, "value": read_value(spin, node), "selectors": selected})
                except Exception as exc:
                    unavailable.append(f"{name}: {exc}")
        except Exception as exc:
            unavailable.append(f"{name}: {exc}")
        finally:
            for selector, value in reversed(originals):
                try:
                    write_value(spin, selector, value)
                except Exception as exc:
                    raise RuntimeError(f"读取配置后无法恢复选择器 {selector.GetName()}：{exc}") from exc
    return entries, unavailable


def apply_features(spin, nodemap, payload: dict) -> list[str]:
    """Replay scalar nodes with dependency retries and restore auto modes last."""
    settings = payload["settings"]
    scope = imaging_names(spin, nodemap)
    rows = list(payload.get("features", []))
    by_name = {entry["name"] for entry in rows}
    for name, value in settings.items():
        if name in ("BalanceRatioRed", "BalanceRatioBlue"):
            selected = [["BalanceRatioSelector", name.removeprefix("BalanceRatio")]]
            if not any(row["name"] == "BalanceRatio" and row.get("selectors") == selected for row in rows):
                rows.append({"name": "BalanceRatio", "value": value, "selectors": selected})
        elif name not in by_name:
            rows.append({"name": name, "value": value, "selectors": []})
    if not rows:
        raise ValueError("方案未包含任何可载入的相机参数")
    supported = []
    for entry in rows:
        node = nodemap.GetNode(entry["name"])
        if node is None:
            raise ValueError(f"当前相机不支持 {entry['name']}")
        if not node.IsStreamable() and entry["name"] not in scope:
            raise ValueError(f"{entry['name']} 不是相机可持久化参数")
        if node.GetPrincipalInterfaceType() not in {spin.intfIFloat, spin.intfIInteger,
                spin.intfIBoolean, spin.intfIEnumeration, spin.intfIString}:
            raise ValueError(f"禁止载入非标量节点 {entry['name']}")
        kind, value = node.GetPrincipalInterfaceType(), entry["value"]
        if ((kind == spin.intfIBoolean and not isinstance(value, bool)) or
                (kind == spin.intfIInteger and (not isinstance(value, int) or isinstance(value, bool))) or
                (kind == spin.intfIFloat and (not isinstance(value, (int, float)) or isinstance(value, bool))) or
                (kind in (spin.intfIEnumeration, spin.intfIString) and not isinstance(value, str))):
            raise ValueError(f"{entry['name']} 的参数值类型与相机不匹配")
        if entry["name"] == "AcquisitionMode" and value != "Continuous":
            raise ValueError("当前预览程序仅支持连续采集模式")
        if entry["name"] == "TriggerMode" and value != "Off":
            raise ValueError("当前预览程序不支持启用外部触发的相机方案")
        supported.append(entry)
    auto_modes = set(AUTO_MODES) | {"AutoExposureTargetGreyValueAuto"}
    enable_flags = {row["name"] for row in rows if isinstance(row["value"], bool) and row["name"].endswith("Enable")}
    deferred = auto_modes | enable_flags
    selector_rows = [row for row in supported if nodemap.GetNode(row["name"]).IsSelector()]
    for name in auto_modes:
        node = nodemap.GetNode(name)
        if any(row["name"] == name for row in rows) and spin.IsWritable(node):
            write_value(spin, node, "Off")
    for row in rows:
        if row["name"] in enable_flags and row["value"]:
            node = nodemap.GetNode(row["name"])
            if spin.IsWritable(node):
                write_value(spin, node, True)
    # Make geometry independent of the previously selected ROI.
    geometry_names = ("Width", "Height", "OffsetX", "OffsetY", "BinningHorizontal", "BinningVertical",
                      "DecimationHorizontal", "DecimationVertical")
    geometry_changed = any(row["name"] in geometry_names and not row.get("selectors")
        and spin.IsReadable(nodemap.GetNode(row["name"]))
        and not equal_value(read_value(spin, nodemap.GetNode(row["name"])), row["value"]) for row in rows)
    geometry_changed |= any(name in settings and spin.IsReadable(nodemap.GetNode(name))
        and not equal_value(read_value(spin, nodemap.GetNode(name)), settings[name]) for name in geometry_names)
    if geometry_changed:
        for name in ("OffsetX", "OffsetY", "Width", "Height"):
            node = nodemap.GetNode(name)
            if spin.IsWritable(node):
                spin.CIntegerPtr(node).SetValue(spin.CIntegerPtr(node).GetMin())
    pending = [row for row in supported if row["name"] not in deferred
               and not nodemap.GetNode(row["name"]).IsSelector()
               and not inactive_feature(row, settings, rows)]
    # Geometry first; offsets after width/height. Final verification catches clamps.
    order = {name: index for index, name in enumerate(("BinningSelector", "DecimationSelector",
        "BinningHorizontal", "BinningVertical", "DecimationHorizontal", "DecimationVertical",
        "PixelFormat", "Width", "Height", "OffsetX", "OffsetY"))}
    pending.sort(key=lambda row: order.get(row["name"], 100))
    failures = {}
    for _ in range(4):
        retry = []
        for row in pending:
            try:
                for selector, value in row.get("selectors", []):
                    write_value(spin, nodemap.GetNode(selector), value)
                node = nodemap.GetNode(row["name"])
                if not spin.IsWritable(node) and spin.IsReadable(node) and not equal_value(read_value(spin, node), row["value"]):
                    dependency = {"Gamma": "GammaEnable", "LUTValue": "LUTEnable",
                                  "Sharpening": "SharpeningEnable", "Saturation": "SaturationEnable"}.get(row["name"])
                    enable = nodemap.GetNode(dependency) if dependency else None
                    if dependency in enable_flags and spin.IsWritable(enable):
                        write_value(spin, enable, True)
                write_value(spin, node, row["value"])
            except Exception as exc:
                retry.append(row)
                failures[row["name"]] = str(exc)
        if not retry:
            break
        if len(retry) == len(pending):
            pending = retry
            break
        pending = retry
    else:
        retry = pending
    errors = [failures[row["name"]] for row in retry]
    for row in supported:
        if row["name"] in deferred:
            try:
                for selector, value in row.get("selectors", []):
                    write_value(spin, nodemap.GetNode(selector), value)
                write_value(spin, nodemap.GetNode(row["name"]), row["value"])
            except Exception as exc:
                errors.append(str(exc))
    for row in selector_rows:
        try:
            for selector, value in row.get("selectors", []):
                write_value(spin, nodemap.GetNode(selector), value)
            write_value(spin, nodemap.GetNode(row["name"]), row["value"])
        except Exception as exc:
            errors.append(str(exc))
    return errors
