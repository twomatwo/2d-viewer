"""Keep native crash diagnostics available through interpreter shutdown."""
from __future__ import annotations

from datetime import datetime
import faulthandler
import logging
import os
from pathlib import Path
import sys


_crash_fd: int | None = None


def install_crash_logging(directory: Path) -> None:
    global _crash_fd
    if _crash_fd is not None:
        return
    path = directory / "twodviewer-crash.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        header = (f"\n--- {datetime.now().isoformat(timespec='seconds')} "
                  f"PID={os.getpid()} Python={sys.version.split()[0]} ---\n"
                  f"Executable: {sys.executable}\n")
        os.write(fd, header.encode("utf-8"))
        faulthandler.enable(file=fd, all_threads=True)
    except BaseException:
        os.close(fd)
        raise
    # Use an OS descriptor, rather than a Python file object that could close
    # before Qt's native objects are destroyed during interpreter shutdown.
    # Windows releases it when the process exits; leave faulthandler enabled.
    _crash_fd = fd
    previous_hook = sys.excepthook

    def log_uncaught(kind, value, traceback):
        logging.getLogger(__name__).critical("Unhandled exception", exc_info=(kind, value, traceback))
        previous_hook(kind, value, traceback)

    sys.excepthook = log_uncaught
    logging.getLogger(__name__).info("Native crash log: %s", path)
