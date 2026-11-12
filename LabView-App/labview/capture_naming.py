"""Validated snapshot names and atomic image publication using standard libraries."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
from string import Formatter
import tempfile


MAX_SEQUENCE = 2_000_000_000
DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%Y_%m_%d")
DEFAULT_NAMING = {"template": "{date}_{time}_{seq}_{objective}", "date_format": DATE_FORMATS[0],
    "sequence_start": 1, "sequence_next": 1, "sequence_step": 1, "sequence_digits": 4,
    "reset_daily": False, "sequence_date": "", "sample_text": ""}
_FORMATTER = Formatter()
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)}


def validate_filename(stem: str) -> str:
    if not stem or stem in (".", "..") or _INVALID.search(stem) or stem.endswith((" ", ".")):
        raise ValueError('文件名不能为空，不能含 <>:"/\\|?*，也不能以空格或句点结尾')
    if stem.split(".")[0].upper() in _RESERVED:
        raise ValueError("文件名不能使用 CON、NUL、COM1 等系统保留名称")
    if len(stem.encode("utf-16-le")) // 2 > 180:
        raise ValueError("文件名过长，请缩短命名模板")
    return stem


def naming_settings(value=None) -> dict:
    result = dict(DEFAULT_NAMING)
    if isinstance(value, dict):
        result.update({key: value[key] for key in result if key in value})
    for key in ("sequence_start", "sequence_next", "sequence_step", "sequence_digits"):
        limit = 9 if key == "sequence_digits" else MAX_SEQUENCE
        if type(result[key]) is not int or not 1 <= result[key] <= limit:
            raise ValueError("序号设置超出有效范围")
    if result["date_format"] not in DATE_FORMATS or type(result["reset_daily"]) is not bool:
        raise ValueError("日期或序号重置设置无效")
    if not isinstance(result["sample_text"], str) or not isinstance(result["sequence_date"], str) or not isinstance(result["template"], str):
        raise ValueError("命名规则格式无效")
    render_name(result, datetime(2026, 10, 27, 12, 30, 45), "50x", result["sequence_next"])
    return result


def render_name(settings: dict, now: datetime, objective: str, sequence: int) -> str:
    if len(settings["template"]) > 180:
        raise ValueError("命名模板过长")
    values = {"date": now.strftime(settings["date_format"]), "time": now.strftime("%H%M%S"),
              "objective": objective}
    parts = []
    try:
        for literal, field, spec, conversion in _FORMATTER.parse(settings["template"]):
            parts.append(literal)
            if field is None:
                continue
            if field not in (*values, "seq") or conversion:
                raise ValueError("只支持 {date}、{time}、{seq}、{objective} 字段")
            if field == "seq":
                if spec and not re.fullmatch(r"0?[1-9][0-9]?d", spec):
                    raise ValueError("序号格式示例：{seq} 或 {seq:04d}")
                parts.append(format(sequence, spec or f"0{settings['sequence_digits']}d"))
            else:
                if spec:
                    raise ValueError("日期、时间和物镜字段不能附加格式")
                parts.append(values[field])
    except (ValueError, KeyError) as exc:
        raise ValueError(f"命名模板无效：{exc}") from exc
    prefix = settings.get("sample_text", "").strip()
    return validate_filename((prefix + "_" if prefix else "") + "".join(parts))


def effective_sequence(settings: dict, now: datetime) -> int:
    if settings["reset_daily"] and settings["sequence_date"] != now.strftime("%Y-%m-%d"):
        return settings["sequence_start"]
    return settings["sequence_next"]


def available_capture_path(directory: Path, settings: dict, now: datetime,
                           objective: str, extension: str) -> tuple[Path, int]:
    sequence = effective_sequence(settings, now)
    has_sequence = any(field == "seq" for _, field, _, _ in _FORMATTER.parse(settings["template"]))
    for collision in range(100000):
        if sequence + settings["sequence_step"] > MAX_SEQUENCE:
            raise ValueError("序号已到上限，请修改下一序号或命名规则")
        stem = render_name(settings, now, objective, sequence)
        if collision and not has_sequence:
            stem += f"_{collision + 1}"
        path = directory / (stem + "." + extension)
        if not path.exists():
            return path, sequence
        if has_sequence:
            sequence += settings["sequence_step"]
    raise ValueError("同名文件过多，请修改命名规则或保存目录")


def write_snapshot(path: Path, writer, *, overwrite=False) -> None:
    """Publish only a complete image; automatic saves never replace a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=".labview-", suffix=path.suffix, dir=path.parent)
    os.close(descriptor)
    temporary = Path(filename)
    try:
        writer(temporary)
        if overwrite:
            os.replace(temporary, path)
        elif os.name == "nt":
            os.rename(temporary, path)  # Windows rename refuses an existing destination.
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
