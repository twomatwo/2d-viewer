from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Generic, TypeVar
import math

import numpy as np


T = TypeVar("T")


class LatestBuffer(Generic[T]):
    """A single replaceable slot. Consumers never build a frame backlog."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._value: T | None = None
        self._version = 0
        self.updated = Event()

    def put(self, value: T) -> int:
        with self._lock:
            self._version += 1
            self._value = value
            self.updated.set()
            return self._version

    def get(self) -> tuple[int, T | None]:
        with self._lock:
            return self._version, self._value

    def clear(self) -> None:
        with self._lock:
            self._version += 1
            self._value = None
            self.updated.set()


@dataclass(frozen=True)
class Frame:
    raw: np.ndarray
    pixel_format: str
    white_level: float
    frame_id: int
    captured_at: str
    source: str
    timestamp_ns: int = 0
    camera_settings: dict = field(default_factory=dict)
    per_frame_fields: tuple[str, ...] = ()
    received_ns: int = 0  # Host perf_counter clock, separate from the camera timestamp.
    configuration: dict = field(default_factory=dict)
    profile_context: dict = field(default_factory=dict)
    capture_calibration: dict = field(default_factory=dict)

    @property
    def geometry(self) -> tuple:
        names = ("OffsetX", "OffsetY", "BinningHorizontal", "BinningVertical",
                 "DecimationHorizontal", "DecimationVertical")
        return self.raw.shape[:2] + tuple(self.camera_settings.get(n, 0) for n in names) + self.sampling_features

    @property
    def sampling_features(self) -> tuple:
        names = {"BinningHorizontal", "BinningVertical", "DecimationHorizontal", "DecimationVertical"}
        return tuple(sorted((entry["name"], tuple(sorted((str(k), str(v))
            for k, v in entry.get("selectors", []))), entry["value"])
            for entry in self.configuration.get("features", []) if entry["name"] in names))


@dataclass(frozen=True)
class DisplaySettings:
    # Levels are percentages of the acquisition format's full scale.
    black: float = 0.0
    white: float = 100.0
    gamma: float = 1.0
    channel_black: tuple[float, float, float] = (0.0, 0.0, 0.0)
    channel_white: tuple[float, float, float] = (100.0, 100.0, 100.0)
    channel_gain: tuple[float, float, float] = (1.0, 1.0, 1.0)
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    grayscale: bool = False
    flip_x: bool = False
    flip_y: bool = False
    rotation: int = 0

    def __post_init__(self) -> None:
        pairs = [(self.black, self.white), *zip(self.channel_black, self.channel_white)]
        if len(self.channel_black) != 3 or len(self.channel_white) != 3:
            raise ValueError("RGB levels require three channels")
        if any(not (0 <= lo < hi <= 100) for lo, hi in pairs):
            raise ValueError("Each black point must be below its white point (0–100)")
        if not math.isfinite(self.gamma) or not 0.1 <= self.gamma <= 5:
            raise ValueError("Gamma must be between 0.1 and 5")
        if len(self.channel_gain) != 3 or any(not math.isfinite(v) or not 0.1 <= v <= 8 for v in self.channel_gain):
            raise ValueError("RGB multipliers must be between 0.1 and 8")
        if not -100 <= self.brightness <= 100 or not 0.1 <= self.contrast <= 5 or not 0 <= self.saturation <= 3:
            raise ValueError("Invalid brightness, contrast or saturation")
        if self.rotation not in (0, 1, 2, 3):
            raise ValueError("Rotation must be a quarter turn")


@dataclass(frozen=True)
class ProcessedFrame:
    frame: Frame
    settings: DisplaySettings
    rgb: np.ndarray
    input_histogram: np.ndarray
    luminance_histogram: np.ndarray
    version: tuple[int, int]


@dataclass(frozen=True)
class Annotation:
    kind: str
    points: tuple[tuple[float, float], ...]
    color: str = "#ffd166"
    text: str = ""

    @property
    def length_px(self) -> float:
        if len(self.points) < 2:
            return 0.0
        return math.dist(self.points[0], self.points[-1])


@dataclass(frozen=True)
class Calibration:
    units_per_pixel: float = 0.0
    unit: str = "µm"
    profile_name: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.units_per_pixel) or self.units_per_pixel < 0:
            raise ValueError("Scale must be finite and nonnegative")

    @classmethod
    def from_reference(cls, pixels: float, known_length: float, unit: str) -> Calibration:
        if not math.isfinite(pixels) or not math.isfinite(known_length) or pixels <= 0 or known_length <= 0:
            raise ValueError("Reference and physical lengths must be positive")
        return cls(known_length / pixels, unit)

    def label(self, pixels: float) -> str:
        if self.units_per_pixel > 0:
            return f"{pixels * self.units_per_pixel:.3f} {self.unit}"
        return f"{pixels:.2f} px"
