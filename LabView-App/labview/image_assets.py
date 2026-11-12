"""Self-contained PNG assets; decoded images are shared by preview and export."""
import base64
from collections import OrderedDict
from threading import RLock

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader

MAX_PIXELS = 20_000_000
MAX_ENCODED = 64 * 1024 * 1024
_CACHE_BYTES = 64 * 1024 * 1024
_CACHE_COUNT = 32
_cache = OrderedDict()
_cache_lock = RLock()


def decode_image_asset(data: str) -> QImage:
    if not isinstance(data, str) or not data or len(data) > MAX_ENCODED:
        raise ValueError("图片标注数据无效或过大")
    with _cache_lock:
        image = _cache.pop(data, None)
        if image is not None:
            _cache[data] = image
            return image
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("图片标注数据无法解码") from exc
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"PNG")
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > MAX_PIXELS:
        raise ValueError("图片标注尺寸无效或超过 2000 万像素")
    image = reader.read()
    if image.isNull():
        raise ValueError("图片标注内容无法读取")
    image.setDevicePixelRatio(1)
    with _cache_lock:
        _cache[data] = image
        while len(_cache) > 1 and (len(_cache) > _CACHE_COUNT or
                                   sum(item.sizeInBytes() for item in _cache.values()) > _CACHE_BYTES):
            _cache.popitem(last=False)
    return image


def load_image_asset(path) -> tuple[str, QImage]:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.width() * size.height() > MAX_PIXELS:
        raise ValueError("图片超过 2000 万像素，请先缩小后导入")
    image = reader.read()
    if image.isNull():
        raise ValueError("无法读取图片：" + reader.errorString())
    if image.width() * image.height() > MAX_PIXELS:
        raise ValueError("图片超过 2000 万像素，请先缩小后导入")
    image.setDevicePixelRatio(1)
    # Import raster pixels, without recursively embedding another capture's annotations.
    for key in image.textKeys():
        image.setText(key, "")
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("图片无法转换为标注")
    data = base64.b64encode(bytes(encoded)).decode("ascii")
    return data, decode_image_asset(data)
