from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import math
import shutil
from uuid import uuid4

from .models import Calibration, DisplaySettings, Frame
from .camera_profiles import (LIBRARY_SCHEMA, migrate_preset,
                              saved_configuration, validate_configuration)


DEFAULT_OBJECTIVES = ("4×", "10×", "20×", "40×", "60×", "100×")


def sampling_context(frame: Frame) -> dict:
    names = ("BinningHorizontal", "BinningVertical", "DecimationHorizontal", "DecimationVertical")
    context = {"camera": frame.camera_settings.get("_source_camera", frame.source),
               "sampling": [int(frame.camera_settings.get(name, 1)) for name in names]}
    if any(value != 1 for _, _, value in frame.sampling_features):
        context["sampling_features"] = [[name, [list(pair) for pair in selectors], value]
                                        for name, selectors, value in frame.sampling_features]
    return context


def display_from_dict(data: dict) -> DisplaySettings:
    values = {key: value for key, value in data.items() if key in DisplaySettings.__dataclass_fields__}
    for key in ("channel_black", "channel_white", "channel_gain"):
        if key in values:
            values[key] = tuple(values[key])
    return DisplaySettings(**values)


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.error = ""
        self._migration_pending = False
        self.data = {"schema": "labview.settings.v1", "selected_objective": "10×",
                     "objectives": list(DEFAULT_OBJECTIVES), "calibrations": {}, "camera_presets": {}, "preferences": {}}
        defaults = deepcopy(self.data)
        if path.exists():
            try:
                incoming = json.loads(path.read_text(encoding="utf-8"))
                if incoming.get("schema") != self.data["schema"]:
                    raise ValueError("未知配置文件版本")
                for key in ("calibrations", "camera_presets", "preferences"):
                    if not isinstance(incoming.get(key, {}), dict):
                        raise ValueError("配置内容格式错误")
                self.data.update(incoming)
            except (OSError, ValueError, AttributeError) as exc:
                self.error = f"配置文件无法读取，保留原文件：{exc}"
        if not self.error:
            try:
                self._upgrade_objectives()
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                self.error = f"物镜配置无法读取，保留原文件：{exc}"
        if self.error:
            self.data = defaults
            self._upgrade_objectives()

    def _upgrade_objectives(self):
        if "objective_entries" not in self.data:
            names = list(dict.fromkeys((*self.data.get("objectives", []),
                *self.data.get("calibrations", {}).keys(), self.data["selected_objective"])))
            names = [name for name in names if isinstance(name, str) and name.strip()]
            self.data["objective_entries"] = [{"id": uuid4().hex, "name": name,
                "magnification": self._magnification(name), "enabled": True} for name in names]
            self._migration_pending = self.path.exists()
        self.data.setdefault("objective_profiles", {})
        self.data.setdefault("profile_drafts", {})
        self.data.setdefault("archived_objectives", [])
        self.data.setdefault("calibration_records", {})
        for key in ("objective_profiles", "profile_drafts", "calibration_records"):
            if not isinstance(self.data[key], dict):
                raise ValueError(f"{key} 格式错误")
        if not isinstance(self.data["archived_objectives"], list):
            raise ValueError("物镜归档格式错误")
        self._validate_objectives(self.data["objective_entries"])
        for objective in self.data["objective_entries"]:
            identity, name = objective["id"], objective["name"]
            library = self.data["objective_profiles"].setdefault(identity, {"active": "", "schemes": {}})
            if not isinstance(library, dict) or not isinstance(library.get("schemes"), dict):
                raise ValueError("物镜方案库格式错误")
            if library.get("active") and library["active"] not in library["schemes"]:
                raise ValueError("物镜选中的方案不存在")
            library.setdefault("active", "")
            for payload in library["schemes"].values():
                validate_configuration(payload)
            records = self.data["calibration_records"].setdefault(identity, [])
            previous = self.data["calibrations"].get(name)
            if previous and not records:
                records.append(deepcopy(previous))
        if not self.data.get("camera_presets_migrated"):
            for name, payload in self.data.get("camera_presets", {}).items():
                if not isinstance(payload, dict):
                    continue
                objective = self.objective(payload.get("objective", self.data["selected_objective"]))
                if objective is None:
                    continue
                library = self.data["objective_profiles"][objective["id"]]
                if name not in library["schemes"]:
                    library["schemes"][name] = migrate_preset(payload)
                    if not library["active"]:
                        library["active"] = name
            self.data["camera_presets_migrated"] = True
        self._sync_objective_names()

    @staticmethod
    def _magnification(name):
        try:
            return float(name.replace("×", "").replace("x", "").strip())
        except ValueError:
            return 1.0

    @staticmethod
    def _validate_objectives(entries):
        if not isinstance(entries, list) or not entries or len(entries) > 200:
            raise ValueError("至少保留一个物镜，最多 200 个")
        names, identities = set(), set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("物镜格式错误")
            name, identity = entry.get("name", ""), entry.get("id", "")
            if not isinstance(name, str) or not name.strip() or len(name) > 80 or name in names:
                raise ValueError("物镜名称不能为空或重复，最多 80 字")
            if not isinstance(identity, str) or not identity or identity in identities:
                raise ValueError("物镜标识重复或缺失")
            value = float(entry.get("magnification", 0))
            if not math.isfinite(value) or value <= 0:
                raise ValueError("物镜倍率必须大于 0")
            names.add(name)
            identities.add(identity)
        if not any(entry.get("enabled", True) for entry in entries):
            raise ValueError("至少启用一个物镜")

    def _sync_objective_names(self):
        self.data["objectives"] = self.objective_names()
        if self.data["selected_objective"] not in self.data["objectives"]:
            self.data["selected_objective"] = self.data["objectives"][0]

    def objective(self, name_or_id):
        return next((entry for entry in self.data["objective_entries"]
                     if name_or_id in (entry["name"], entry["id"])), None)

    def objective_library(self, name_or_id=None):
        objective = self.objective(name_or_id or self.data["selected_objective"])
        if objective is None:
            raise ValueError("物镜不存在")
        return self.data["objective_profiles"][objective["id"]]

    def transaction(self, mutation):
        previous = deepcopy(self.data)
        try:
            result = mutation()
            self.save()
            return result
        except Exception:
            self.data = previous
            raise

    def save(self) -> None:
        if self.error:
            raise ValueError(self.error)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._migration_pending and self.path.exists():
            backup = self.path.with_name(self.path.stem + ".before-objective-profiles.json")
            if not backup.exists():
                shutil.copy2(self.path, backup)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self._migration_pending = False

    def objective_names(self) -> list[str]:
        return [entry["name"] for entry in self.data["objective_entries"] if entry.get("enabled", True)]

    def select_objective(self, name: str) -> None:
        if name not in self.objective_names():
            raise ValueError("物镜不存在或已停用")
        self.transaction(lambda: self.data.update(selected_objective=name))

    def update_objectives(self, entries):
        self._validate_objectives(entries)

        def update():
            old = {entry["id"]: entry for entry in self.data["objective_entries"]}
            retained = {entry["id"] for entry in entries}
            selected = self.objective(self.data["selected_objective"])["id"]
            renamed_calibrations = {}
            for entry in entries:
                previous = old.get(entry["id"])
                if previous and previous["name"] in self.data["calibrations"]:
                    renamed_calibrations[entry["name"]] = self.data["calibrations"][previous["name"]]
                self.data["objective_profiles"].setdefault(entry["id"], {"active": "", "schemes": {}})
                self.data["calibration_records"].setdefault(entry["id"], [])
                if entry["id"] == selected:
                    self.data["selected_objective"] = entry["name"]
            for identity, entry in old.items():
                if identity not in retained:
                    self.data["archived_objectives"].append({"objective": entry,
                        "profiles": self.data["objective_profiles"].pop(identity, {}),
                        "calibrations": self.data["calibration_records"].pop(identity, [])})
            self.data["calibrations"] = renamed_calibrations
            self.data["objective_entries"] = deepcopy(entries)
            self._sync_objective_names()
        self.transaction(update)

    def set_calibration(self, name: str, calibration: Calibration, context: dict) -> None:
        if not name.strip() or calibration.units_per_pixel <= 0:
            raise ValueError("请填写倍率名称和有效的标定比例")
        objective = self.objective(name)
        if objective is None:
            raise ValueError("物镜不存在")

        def update():
            record = {"scale": calibration.units_per_pixel, "unit": calibration.unit, "context": context}
            self.data["calibrations"][name] = record
            records = self.data["calibration_records"].setdefault(objective["id"], [])
            records[:] = [item for item in records if item.get("context") != context]
            records.append(deepcopy(record))
        self.transaction(update)

    def calibration_record(self, name: str, context: dict | None = None):
        objective = self.objective(name)
        records = self.data["calibration_records"].get(objective["id"], []) if objective else []
        if objective is None:
            archived = next((item for item in reversed(self.data["archived_objectives"])
                             if name in (item["objective"]["name"], item["objective"]["id"])), None)
            records = archived["calibrations"] if archived else []
        return next((item for item in reversed(records) if context is None or item.get("context") == context), None)

    def has_calibration(self, name):
        return self.calibration_record(name) is not None

    def calibration(self, name: str, context: dict | None) -> Calibration | None:
        item = self.calibration_record(name, context)
        if not item:
            return None
        return Calibration(float(item["scale"]), str(item["unit"]), name)

    def export_calibrations(self, path: Path) -> None:
        content = {"schema": "labview.calibrations.v2", "objectives": [dict(deepcopy(entry),
            records=deepcopy(self.data["calibration_records"].get(entry["id"], [])))
            for entry in self.data["objective_entries"]]}
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_calibrations(self, path: Path) -> None:
        content = json.loads(path.read_text(encoding="utf-8"))
        if content.get("schema") == "labview.calibrations.v1" and isinstance(content.get("calibrations"), dict):
            entries = [{"id": uuid4().hex, "name": name, "magnification": self._magnification(name),
                        "enabled": True, "records": [item]} for name, item in content["calibrations"].items()]
        elif content.get("schema") == "labview.calibrations.v2":
            entries = content.get("objectives")
        else:
            raise ValueError("不是 2D Viewer 标定文件")
        self._validate_objectives(entries)
        for entry in entries:
            if not isinstance(entry.get("records"), list) or len(entry["records"]) > 1000:
                raise ValueError("标定记录格式错误")
            for item in entry["records"]:
                calibration = Calibration(float(item["scale"]), str(item["unit"]), entry["name"])
                context = item.get("context")
                if calibration.units_per_pixel <= 0 or not isinstance(context, dict):
                    raise ValueError("标定数据不完整")
                sampling = context.get("sampling")
                if (not isinstance(context.get("camera"), str) or not isinstance(sampling, list)
                        or len(sampling) != 4 or any(not isinstance(v, int) or v < 1 for v in sampling)):
                    raise ValueError("标定缺少有效相机和采样配置")

        def update():
            for entry in entries:
                objective = self.objective(entry["id"]) or self.objective(entry["name"])
                if objective is None:
                    objective = {key: deepcopy(entry[key]) for key in ("name", "magnification", "enabled")}
                    objective["id"] = uuid4().hex
                    self.data["objective_entries"].append(objective)
                    self.data["objective_profiles"][objective["id"]] = {"active": "", "schemes": {}}
                records = self.data["calibration_records"].setdefault(objective["id"], [])
                for item in entry["records"]:
                    records[:] = [record for record in records if record.get("context") != item["context"]]
                    records.append(deepcopy(item))
                if records:
                    self.data["calibrations"][objective["name"]] = deepcopy(records[-1])
            self._sync_objective_names()
        self.transaction(update)

    def save_profile(self, objective_id, name, payload, *, clear_drafts=()):
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("方案名称不能为空，最多 80 字")
        saved = saved_configuration(payload)

        def update():
            library = self.objective_library(objective_id)
            if name in library["schemes"]:
                self.data.setdefault("previous_profiles", {})[objective_id + ":" + name] = deepcopy(library["schemes"][name])
            library["schemes"][name] = saved
            library["active"] = name
            for key in clear_drafts:
                self.data["profile_drafts"].pop(key, None)
        self.transaction(update)
        return saved

    def export_profiles(self, scope="all"):
        entries = self.data["objective_entries"]
        if scope != "all":
            entries = [self.objective(scope)]
        return {"schema": LIBRARY_SCHEMA, "objectives": [dict(deepcopy(entry),
            profiles=deepcopy(self.objective_library(entry["id"]))) for entry in entries if entry]}

    def import_profiles(self, content, duplicate=False):
        if not isinstance(content, dict) or content.get("schema") != LIBRARY_SCHEMA:
            raise ValueError("不是有效的物镜相机参数库")
        entries = deepcopy(content.get("objectives"))
        self._validate_objectives(entries)
        for entry in entries:
            library = entry.get("profiles")
            if not isinstance(library, dict) or not isinstance(library.get("schemes"), dict):
                raise ValueError("方案库格式错误")
            for name, payload in library["schemes"].items():
                if not isinstance(name, str) or not name.strip() or len(name) > 80:
                    raise ValueError("方案名称错误")
                library["schemes"][name] = validate_configuration(payload)
            if library.get("active") and library["active"] not in library["schemes"]:
                raise ValueError("当前方案不存在")

        def update():
            for incoming in entries:
                library = incoming.pop("profiles")
                existing = None if duplicate else self.objective(incoming["id"])
                if existing is None and not duplicate:
                    existing = self.objective(incoming["name"])
                if existing:
                    for name in library["schemes"]:
                        prior = self.objective_library(existing["id"])["schemes"].get(name)
                        if prior:
                            self.data.setdefault("previous_profiles", {})[existing["id"] + ":" + name] = deepcopy(prior)
                    self.objective_library(existing["id"])["schemes"].update(library["schemes"])
                    if not self.objective_library(existing["id"])["active"]:
                        self.objective_library(existing["id"])["active"] = library.get("active", "")
                else:
                    incoming["id"] = uuid4().hex
                    base, suffix = incoming["name"], 2
                    while self.objective(incoming["name"]):
                        incoming["name"] = f"{base} ({suffix})"
                        suffix += 1
                    self.data["objective_entries"].append(incoming)
                    self.data["objective_profiles"][incoming["id"]] = library
                    self.data["calibration_records"][incoming["id"]] = []
            self._sync_objective_names()
        self.transaction(update)
