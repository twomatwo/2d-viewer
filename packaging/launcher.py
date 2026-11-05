"""Entry point for the Windows application bundle."""
from multiprocessing import freeze_support

from labview.__main__ import main


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
