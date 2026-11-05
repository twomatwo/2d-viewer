from __future__ import annotations

from threading import Event, Lock, Thread
from time import perf_counter
from typing import Callable

import cv2
import numpy as np

from .models import DisplaySettings, Frame, LatestBuffer, ProcessedFrame


BAYER_CODES = {
    # Use unambiguous four-letter CFA names; OpenCV's two-letter names differ.
    "BayerRG": cv2.COLOR_BayerRGGB2RGB,
    "BayerGR": cv2.COLOR_BayerGRBG2RGB,
    "BayerGB": cv2.COLOR_BayerGBRG2RGB,
    "BayerBG": cv2.COLOR_BayerBGGR2RGB,
}


def decode_rgb(frame: Frame) -> np.ndarray:
    raw = frame.raw
    if raw.dtype not in (np.uint8, np.uint16):
        raise ValueError("Only unsigned 8/16-bit images are supported")
    for prefix, code in BAYER_CODES.items():
        if frame.pixel_format.startswith(prefix):
            if frame.pixel_format not in (prefix + "8", prefix + "16"):
                raise ValueError("Packed Bayer requires an unpacked acquisition format")
            return cv2.cvtColor(raw, code)
    if raw.ndim == 2:
        return cv2.cvtColor(raw, cv2.COLOR_GRAY2RGB)
    if raw.ndim == 3 and raw.shape[2] in (3, 4):
        rgb = raw[:, :, :3]
        if frame.pixel_format.startswith("BGR"):
            rgb = rgb[:, :, ::-1]
        return np.ascontiguousarray(rgb)
    raise ValueError(f"Unsupported image shape: {raw.shape}")


def render_rgb(rgb: np.ndarray, white_level: float, settings: DisplaySettings) -> np.ndarray:
    """Display-only LUT. Does not modify either raw data or the decoded image."""
    if not np.isfinite(white_level) or white_level <= 0:
        raise ValueError("Image full scale must be positive")
    table_size = np.iinfo(rgb.dtype).max + 1
    values = np.arange(table_size, dtype=np.float32) / white_level
    tables = np.empty((3, table_size), dtype=np.uint8)
    for ch in range(3):
        lo, hi = settings.channel_black[ch] / 100, settings.channel_white[ch] / 100
        normalized = np.clip((values * settings.channel_gain[ch] - lo) / (hi - lo), 0, 1)
        normalized = np.clip((normalized - settings.black / 100)
                             / ((settings.white - settings.black) / 100), 0, 1)
        normalized = np.power(normalized, 1 / settings.gamma)
        normalized = np.clip((normalized - 0.5) * settings.contrast + 0.5 + settings.brightness / 100, 0, 1)
        tables[ch] = np.rint(normalized * 255).astype(np.uint8)
    if rgb.dtype == np.uint8:
        # OpenCV applies all three channel tables in one native pass. Avoid
        # three large strided NumPy gathers on full-resolution camera frames.
        output = cv2.LUT(rgb, np.ascontiguousarray(tables.T).reshape(256, 1, 3))
    else:
        output = np.empty(rgb.shape, dtype=np.uint8)
        for ch in range(3):
            output[:, :, ch] = tables[ch, rgb[:, :, ch]]
    if settings.grayscale or settings.saturation != 1:
        gray = cv2.cvtColor(output, cv2.COLOR_RGB2GRAY)
        if settings.grayscale:
            output = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        else:
            # Keep saturation in native 8-bit operations: full-resolution float
            # expressions allocate hundreds of MB for every camera frame.
            gray_rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
            output = cv2.addWeighted(output, settings.saturation, gray_rgb,
                                     1 - settings.saturation, 0)
    if settings.flip_x:
        output = output[:, ::-1]
    if settings.flip_y:
        output = output[::-1]
    if settings.rotation:
        output = np.rot90(output, -settings.rotation)
    return np.ascontiguousarray(output)


def histogram(rgb: np.ndarray, white_level: float = 255) -> np.ndarray:
    # Spatial sampling limits cost on 5 MP frames; histogram remains full-scale.
    step = max(1, int(np.ceil(np.sqrt(rgb.shape[0] * rgb.shape[1] / 180_000))))
    sampled = rgb[::step, ::step]
    if sampled.dtype != np.uint8 or white_level != 255:
        sampled = np.clip(sampled.astype(np.float32) * (255 / white_level), 0, 255).astype(np.uint8)
    return np.stack([np.bincount(sampled[:, :, c].ravel(), minlength=256) for c in range(3)])


def luminance_histogram(rgb: np.ndarray, white_level: float = 255) -> np.ndarray:
    step = max(1, int(np.ceil(np.sqrt(rgb.shape[0] * rgb.shape[1] / 180_000))))
    sample = rgb[::step, ::step]
    if sample.dtype != np.uint8 or white_level != 255:
        sample = np.clip(sample.astype(np.float32) * (255 / white_level), 0, 255).astype(np.uint8)
    gray = cv2.cvtColor(sample, cv2.COLOR_RGB2GRAY)
    return np.bincount(gray.ravel(), minlength=256)


class DisplayWorker(Thread):
    def __init__(self, source: LatestBuffer[Frame], output: LatestBuffer[ProcessedFrame],
                 on_error: Callable[[str], None], on_frame: Callable[[], None] | None = None) -> None:
        super().__init__(name="display-processing", daemon=True)
        self.source, self.output, self.on_error = source, output, on_error
        self.on_frame = on_frame
        self._notification_pending = False
        self._halt = Event()
        self._lock = Lock()
        self._settings = DisplaySettings()
        self._revision = 0
        self._frozen: Frame | None = None

    def configure(self, settings: DisplaySettings) -> None:
        with self._lock:
            self._settings = settings
            self._revision += 1
        self.source.updated.set()

    def freeze(self, frame: Frame | None) -> None:
        with self._lock:
            self._frozen = frame
            self._revision += 1
        self.source.updated.set()

    def acknowledge(self) -> None:
        with self._lock:
            self._notification_pending = False

    def stop(self) -> None:
        self._halt.set()
        self.source.updated.set()

    def run(self) -> None:
        cv2.setNumThreads(2)
        last_key = None
        decoded_key = None
        rgb = None
        input_hist = None
        overall_hist = None
        while not self._halt.is_set():
            self.source.updated.clear()
            started = perf_counter()
            with self._lock:
                settings, revision, frozen = self._settings, self._revision, self._frozen
            version, frame = self.source.get()
            if frozen is not None:
                frame, version = frozen, -id(frozen)
            key = (version, revision)
            if frame is not None and key != last_key:
                try:
                    if decoded_key != version:
                        rgb = decode_rgb(frame)
                        input_hist = histogram(rgb, frame.white_level)
                        overall_hist = luminance_histogram(rgb, frame.white_level)
                        decoded_key = version
                    output = render_rgb(rgb, frame.white_level, settings)
                    self.output.put(ProcessedFrame(frame, settings, output, input_hist,
                                                   overall_hist, key))
                    with self._lock:
                        notify = not self._notification_pending
                        self._notification_pending = True
                    if notify and self.on_frame is not None:
                        self.on_frame()
                except Exception as exc:
                    self.on_error(f"显示处理失败：{exc}")
                last_key = key
            self._halt.wait(max(0, 1 / 60 - (perf_counter() - started)))
            self.source.updated.wait(0.2)
