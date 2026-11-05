from __future__ import annotations

from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from typing import Callable

import cv2
import numpy as np


class VideoRecorder(Thread):
    """Bounded preview recorder. Timestamp-based repetition preserves wall duration."""
    def __init__(self, path: Path, size: tuple[int, int], fps: float,
                 finished: Callable[[str], None], metadata: dict) -> None:
        super().__init__(name="preview-video", daemon=True)
        self.path, self.size, self.fps, self.finished, self.metadata = path, size, fps, finished, metadata
        self.queue: Queue = Queue(maxsize=8)
        self._halt = Event()
        self.started_at = monotonic()
        self.stopped_at = 0.0
        self.dropped = 0
        self.frames_written = 0

    def push(self, rgb: np.ndarray) -> None:
        if self._halt.is_set():
            return
        if (rgb.shape[1], rgb.shape[0]) != self.size:
            self.stop()
            return
        try:
            self.queue.put_nowait((monotonic(), rgb))
        except Full:
            self.dropped += 1

    def stop(self) -> None:
        self.stopped_at = monotonic()
        self._halt.set()

    def run(self) -> None:
        writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"MJPG"), self.fps, self.size)
        previous = None
        try:
            if not writer.isOpened(): raise OSError("无法创建 MJPEG AVI 录像")
            while not self._halt.is_set() or not self.queue.empty():
                try:
                    timestamp, rgb = self.queue.get(timeout=0.05)
                except Empty:
                    continue
                current = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                target = int((timestamp - self.started_at) * self.fps) + 1
                if target <= self.frames_written:
                    previous = current
                    continue
                while self.frames_written < target - 1:
                    writer.write(previous if previous is not None else current)
                    self.frames_written += 1
                writer.write(current)
                self.frames_written += 1
                previous = current
            if previous is not None:
                target = max(self.frames_written, round((self.stopped_at - self.started_at) * self.fps))
                while self.frames_written < target:
                    writer.write(previous)
                    self.frames_written += 1
            writer.release()
            self.metadata.update(format="MJPEG AVI / 8-bit display preview", fps=self.fps,
                                 frames=self.frames_written, skipped_preview_inputs=self.dropped,
                                 duration_seconds=self.frames_written / self.fps)
            self.finished.emit(str(self.path)) if hasattr(self.finished, "emit") else self.finished(str(self.path))
        except Exception as exc:
            self.finished(f"ERROR: {exc}")
        finally:
            writer.release()
