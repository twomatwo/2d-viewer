"""Locate the bundled Spinnaker binaries without changing system settings."""
import os
from pathlib import Path
import sys


_sdk_directory = Path(sys._MEIPASS) / "PySpin"
if _sdk_directory.is_dir():
    # Keep the handle alive: closing it removes the directory from DLL search.
    _sdk_dll_handle = os.add_dll_directory(str(_sdk_directory))
    _cti = _sdk_directory / "Spinnaker_GenTL_v140.cti"
    if _cti.is_file():
        os.environ["SPINNAKER_GENTL64_CTI_VS140"] = str(_cti)
