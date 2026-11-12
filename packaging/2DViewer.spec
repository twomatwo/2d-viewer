# Build with the existing, version-matched camera Python environment.
from importlib import metadata
from pathlib import Path
import ast
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, get_package_paths
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)

packaging = Path(SPECPATH)
root = packaging.parent
source = root
version_line = next(line for line in (source / "twodviewer" / "__init__.py").read_text(encoding="utf-8").splitlines()
                    if line.startswith("__version__"))
version = ast.literal_eval(version_line.split("=", 1)[1].strip())
version_tuple = tuple(map(int, version.split("."))) + (0,)
sdk = Path(get_package_paths("PySpin")[1])

# PySpin loads CTI/property files dynamically, in addition to its native DLLs.
datas = [(str(path), "PySpin") for path in sdk.iterdir()
         if path.suffix.lower() in (".cti", ".property")]
datas.append((str(root / "docs" / "licenses" / "Spinnaker"), "licenses/Spinnaker"))
for distribution in ("numpy", "opencv-python-headless", "PySide6-Essentials",
                     "shiboken6", "tifffile", "pyinstaller"):
    dist = metadata.distribution(distribution)
    for entry in dist.files or ():
        if entry.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
            # Preserve notices bundled by each dependency, including subprojects.
            relative = Path(*entry.parts[1:]) if ".dist-info" in entry.parts[0] else Path(entry)
            datas.append((str(dist.locate_file(entry)),
                          str(Path("licenses") / distribution / relative.parent)))
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.exists():
    datas.append((str(python_license), "licenses/Python"))
extra_licenses = packaging / "licenses"
if extra_licenses.exists():
    datas.append((str(extra_licenses), "licenses"))

a = Analysis(
    [str(packaging / "launcher.py")],
    pathex=[str(source)],
    binaries=collect_dynamic_libs("PySpin"),
    datas=datas,
    hiddenimports=["PySpin"],
    hookspath=[],
    runtime_hooks=[str(packaging / "runtime_pyspin.py")],
    excludes=["tkinter", "pytest", "unittest", "IPython", "matplotlib", "scipy"],
    noarchive=False,
)
# Qt 6.12 uses the unversioned ICU API supplied by supported Windows systems.
# A foreign Poppler ICU on the build host PATH has the same DLL name but
# version-suffixed exports; bundling it prevents QtCore from loading.
# Let Windows resolve its own ICU instead of copying build-host ICU binaries.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != "icuuc.dll"
              and not (Path(entry[0]).name.lower().startswith("icudt")
                       and Path(entry[0]).suffix.lower() == ".dll")]
pyz = PYZ(a.pure)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple, mask=0x3F,
                     flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", "2D Viewer microscope camera viewer"),
            StringStruct("FileVersion", version),
            StringStruct("InternalName", "2D Viewer"),
            StringStruct("OriginalFilename", "2D Viewer.exe"),
            StringStruct("ProductName", "2D Viewer"),
            StringStruct("ProductVersion", version),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="2D Viewer",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="2D Viewer")
