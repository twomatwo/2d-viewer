from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import APP_NAME, __version__


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=f"{APP_NAME} FLIR camera viewer")
    startup = parser.add_mutually_exclusive_group()
    startup.add_argument("--connect", dest="connect", action="store_true",
                         help="Connect the real camera on startup")
    startup.add_argument("--no-connect", dest="connect", action="store_false",
                         help="Start offline without connecting the camera")
    parser.set_defaults(connect=bool(getattr(sys, "frozen", False)))
    parser.add_argument("--data-directory", type=Path,
                        help="Directory for settings, captures and logs")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args()
    from .app import default_data_directory, run
    from .main_window import MainWindow
    data_directory = (args.data_directory.expanduser().resolve()
                      if args.data_directory is not None else default_data_directory())
    return run(MainWindow, data_directory, connect=args.connect)


if __name__ == "__main__":
    raise SystemExit(main())
