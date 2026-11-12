from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from .crash_logging import install_crash_logging
from . import APP_NAME, __version__


def default_data_directory() -> Path:
    """Keep installed data in the legacy location so renaming preserves settings."""
    if not getattr(sys, "frozen", False):
        root = Path(__file__).resolve().parents[1]
        legacy = root / "LabView-App"
        if any((legacy / name).is_dir() for name in ("settings", "captures", "recovery")):
            return legacy
        return root / "data"
    from PySide6.QtCore import QStandardPaths
    return Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericDataLocation)) / "LabView"


def default_settings_path(data_directory: Path) -> Path:
    current = data_directory / "settings" / "twodviewer.json"
    legacy = data_directory / "settings" / "labview.json"
    return legacy if not current.exists() and legacy.exists() else current


def run(window_class, data_directory: Path, *, connect: bool = False, name: str = APP_NAME) -> int:
    """Run one application with its own settings, captures and logs."""
    log_directory = data_directory / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(log_directory / "twodviewer.log", maxBytes=2_000_000,
                                    backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s PID=%(process)d %(levelname)s %(message)s",
        handlers=handlers,
    )
    install_crash_logging(log_directory)
    logger = logging.getLogger(__name__)
    logger.info("Starting %s: PID=%s Python=%s data=%s", name, os.getpid(), sys.version.split()[0], data_directory)
    import PySide6
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    logger.info("PySide6=%s", PySide6.__version__)
    app = QApplication(sys.argv)
    app.setApplicationName(name)
    app.setApplicationVersion(__version__)
    window = window_class(data_directory=data_directory)
    window.show()
    if connect:
        QTimer.singleShot(100, window._connect_camera)
    result = app.exec()
    logger.info("GUI event loop ended: exit_code=%s", result)
    return result
