"""Qt-free camera adapter contract; SDK handles belong to the capture thread."""
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class CameraCallbacks:
    connected: Callable[[dict], None]
    settings_updated: Callable[[dict], None]
    error: Callable[[str], None]
    operation_finished: Callable[[dict], None]


class CameraAdapter(Protocol):
    backend_id: str

    def run(self) -> None: ...
    def request_stop(self) -> None: ...
    def configure_many(self, settings: dict, request_id: str = "") -> None: ...
    def request_snapshot(self, request_id: str) -> None: ...
    def configure_profile(self, payload: dict, context: dict, request_id: str, *, baseline=None) -> None: ...
    def configure_context(self, context: dict, baseline: dict | None = None, request_id: str = "") -> None: ...
