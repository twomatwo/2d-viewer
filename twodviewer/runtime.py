"""Register fonts needed for Chinese labels and image exports."""
from pathlib import Path
import os

_fonts_registered = False

def register_fonts() -> None:
    global _fonts_registered
    if _fonts_registered or os.name != "nt":
        return
    from PySide6.QtGui import QFontDatabase
    fonts = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "Fonts"
    # Offscreen Qt does not enumerate all Windows fonts. Explicit fonts also
    # keep Chinese labels and scientific units legible in saved PNGs.
    for filename in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc"):
        path = fonts / filename
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    _fonts_registered = True
