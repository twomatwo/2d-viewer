"""Serializable camera configurations and comparisons independent of the GUI/SDK."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from uuid import uuid4


PROFILE_SCHEMA = "labview.camera-profile.v2"
LIBRARY_SCHEMA = "labview.camera-library.v2"
AUTO_MODES = ("ExposureAuto", "GainAuto", "BalanceWhiteAuto", "BlackLevelAuto")
READ_ONLY = {"AcquisitionResultingFrameRate", "DeviceLinkSpeed", "U3VCurrentSpeed"}
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def scalar(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    if isinstance(value, str) and len(value) <= 4096:
        return value
    raise ValueError("相机参数必须是有限数值、布尔值或字符串")


def validate_configuration(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("schema") != PROFILE_SCHEMA:
        raise ValueError("不是有效的相机参数方案")
    settings, features = payload.get("settings", {}), payload.get("features", [])
    if not isinstance(settings, dict) or not isinstance(features, list) or len(features) > 100000:
        raise ValueError("相机参数格式错误或条目过多")
    camera = payload.get("camera", {})
    if not isinstance(camera, dict):
        raise ValueError("相机标识格式错误")
    cleaned = deepcopy(payload)
    cleaned["camera"] = {key: str(camera.get(key, ""))[:256] for key in ("model", "serial")}
    cleaned["settings"] = {}
    for name, value in settings.items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in READ_ONLY:
            continue
        cleaned["settings"][name] = scalar(value)
    cleaned["features"] = []
    seen = set()
    for item in features:
        if not isinstance(item, dict) or not NAME.fullmatch(str(item.get("name", ""))):
            raise ValueError("相机功能名称格式错误")
        selectors = item.get("selectors", [])
        if not isinstance(selectors, list) or len(selectors) > 8:
            raise ValueError("相机参数选择器格式错误")
        selected = []
        for pair in selectors:
            if not isinstance(pair, list) or len(pair) != 2 or not NAME.fullmatch(str(pair[0])):
                raise ValueError("相机参数选择器内容错误")
            selected.append([pair[0], scalar(pair[1])])
        entry = {"name": item["name"], "value": scalar(item.get("value")), "selectors": selected}
        key = feature_key(entry)
        if key in seen:
            raise ValueError(f"重复的相机参数：{item['name']}")
        seen.add(key)
        cleaned["features"].append(entry)
    cleaned["complete"] = bool(payload.get("complete", False))
    return cleaned


def configuration(description: dict) -> dict:
    return validate_configuration({"schema": PROFILE_SCHEMA,
        "camera": {"model": description.get("model", ""), "serial": description.get("serial", "")},
        "settings": {k: v for k, v in description.get("settings", {}).items()
                     if k not in READ_ONLY and not k.startswith("_")},
        "features": description.get("features", []),
        "complete": description.get("features_complete", False),
        "unavailable": description.get("unavailable_features", [])})


def saved_configuration(payload: dict) -> dict:
    result = validate_configuration(payload)
    if any(result["settings"].get(name) == "Once" for name in AUTO_MODES):
        raise ValueError("请等待自动一次完成，再保存相机参数")
    result.update(saved_at=timestamp(), revision=uuid4().hex)
    return result


def feature_key(entry: dict) -> tuple:
    return entry["name"], tuple(sorted((str(k), str(v)) for k, v in entry.get("selectors", [])))


def automatic(name: str, settings: dict) -> bool:
    if name == "AcquisitionFrameRate":
        return not settings.get("AcquisitionFrameRateEnable", True)
    mode = ("ExposureAuto" if name.startswith("ExposureTime") else
            "GainAuto" if name == "Gain" else
            "BalanceWhiteAuto" if name.startswith("BalanceRatio") else
            "BlackLevelAuto" if name == "BlackLevel" else
            "AutoExposureTargetGreyValueAuto" if name == "AutoExposureTargetGreyValue" else "")
    return bool(mode) and settings.get(mode, "Off") != "Off"


def equal_value(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (float, int)) and isinstance(right, (float, int)):
        return math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-6)
    return left == right


def inactive_feature(entry: dict, settings: dict, features: list[dict]) -> bool:
    name = entry["name"]
    if automatic(name, settings):
        return True
    if name.startswith("Trigger") and name not in ("TriggerMode", "TriggerSelector"):
        selectors = entry.get("selectors", [])
        mode = next((row["value"] for row in features if row["name"] == "TriggerMode"
                     and row.get("selectors", []) == selectors), settings.get("TriggerMode"))
        return mode == "Off"
    return False


def differences(saved: dict, actual: dict) -> list[str]:
    """Compare modes/settings, never continuously generated automatic values."""
    expected, current = saved.get("settings", {}), actual.get("settings", {})
    changed = []
    for name, value in expected.items():
        if name in READ_ONLY or automatic(name, current):
            continue
        if name not in current or not equal_value(value, current[name]):
            changed.append(name)
    actual_features = {feature_key(entry): entry["value"] for entry in actual.get("features", [])}
    for entry in saved.get("features", []):
        name, key = entry["name"], feature_key(entry)
        if ((name in expected and not entry.get("selectors")) or name.endswith("Selector")
                or inactive_feature(entry, current, actual.get("features", []))):
            continue
        if key not in actual_features or not equal_value(entry["value"], actual_features[key]):
            changed.append(name + (" [" + ", ".join(str(v) for _, v in key[1]) + "]" if key[1] else ""))
    return list(dict.fromkeys(changed))


def same_configuration_values(target: dict, actual: dict) -> bool:
    """Reuse only the same camera and every captured value, including selector states.

    Unlike differences(), this does not ignore auto outputs or inactive features.
    """
    if (not target or not actual or not actual.get("camera", {}).get("serial") or
            target.get("camera") != actual.get("camera") or
            target.get("schema") != actual.get("schema")):
        return False
    expected, current = target.get("settings", {}), actual.get("settings", {})
    if not expected or expected.keys() != current.keys() or any(
            not equal_value(value, current[name]) for name, value in expected.items()):
        return False
    expected_rows = {feature_key(row): row["value"] for row in target.get("features", [])}
    current_rows = {feature_key(row): row["value"] for row in actual.get("features", [])}
    return (expected_rows.keys() == current_rows.keys() and all(
        equal_value(value, current_rows[key]) for key, value in expected_rows.items()))


def migrate_preset(payload: dict) -> dict:
    """Retain old camera values; computer processing never enters new profiles."""
    camera = dict(payload.get("camera", {}))
    roi = camera.pop("SensorROI", None)
    if isinstance(roi, list) and len(roi) == 4:
        camera.update(dict(zip(("OffsetX", "OffsetY", "Width", "Height"), roi)))
    for name in AUTO_MODES:
        if camera.get(name) == "Once":
            camera[name] = "Off"
    return validate_configuration({"schema": PROFILE_SCHEMA, "camera": {}, "settings": camera,
                                   "features": [], "complete": False, "legacy": True})
