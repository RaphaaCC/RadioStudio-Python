"""Asynchronous, bounded extraction of embedded audio cover art."""

from __future__ import annotations

import logging
import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (QByteArray, QObject, QRunnable, QBuffer, QIODevice,
                            QThreadPool, Signal, Slot, QSize, Qt)
from PySide6.QtGui import QImage, QImageReader
from tinytag import TinyTag


MAX_COVER_BYTES = 8 * 1024 * 1024
MAX_COVER_DIMENSION = 8192
MAX_COVER_RENDER_SIZE = 512
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_CACHE_ITEMS = 32
_logger = logging.getLogger("radiostudio")

_MIME_BY_FORMAT = {
    "bmp": "image/bmp",
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


@dataclass(frozen=True)
class CoverArtwork:
    data: bytes
    mime_type: str
    image: QImage


def _read_cover(path: str) -> CoverArtwork | None:
    try:
        # TinyTag only parses images while parsing tags. With tags=False its
        # internal _load() skips the parser entirely, so image=True alone
        # silently returns an empty Images object for every audio file.
        tag = TinyTag.get(path, tags=True, duration=False, image=True)
        cover = tag.images.front_cover or tag.images.any
        if cover is None or not cover.data or len(cover.data) > MAX_COVER_BYTES:
            return None

        return _artwork_from_bytes(bytes(cover.data), str(getattr(cover, "mime_type", "") or ""))
    except Exception:
        _logger.debug("Não foi possível ler a capa incorporada de %s", path, exc_info=True)
        return None


def _artwork_from_bytes(data: bytes, supplied_mime="") -> CoverArtwork | None:
    try:
        if not data or len(data) > MAX_COVER_BYTES:
            return None
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        if not buffer.open(QIODevice.ReadOnly):
            return None
        reader = QImageReader(buffer)
        reader.setAutoTransform(True)
        source_size = reader.size()
        if (not source_size.isValid()
                or source_size.width() > MAX_COVER_DIMENSION
                or source_size.height() > MAX_COVER_DIMENSION):
            return None
        if (source_size.width() > MAX_COVER_RENDER_SIZE
                or source_size.height() > MAX_COVER_RENDER_SIZE):
            reader.setScaledSize(source_size.scaled(
                QSize(MAX_COVER_RENDER_SIZE, MAX_COVER_RENDER_SIZE), Qt.KeepAspectRatio))
        image = reader.read()
        image_format = bytes(reader.format()).decode("ascii", "ignore").lower()
        if image.isNull():
            return None
        cover_mime = str(supplied_mime or "").lower()
        mime_type = cover_mime if cover_mime in _MIME_BY_FORMAT.values() else None
        mime_type = mime_type or _MIME_BY_FORMAT.get(image_format)
        if not mime_type:
            return None
        return CoverArtwork(data, mime_type, image)
    except Exception:
        _logger.debug("Não foi possível decodificar a capa incorporada", exc_info=True)
        return None


class _CoverSignals(QObject):
    finished = Signal(object, object)


class _CoverTask(QRunnable):
    def __init__(self, key: tuple, cover_data=None, mime_type=""):
        super().__init__()
        self.key = key
        self.cover_data = bytes(cover_data) if cover_data is not None else None
        self.mime_type = str(mime_type or "")
        self.signals = _CoverSignals()

    @Slot()
    def run(self):
        artwork = (_artwork_from_bytes(self.cover_data, self.mime_type)
                   if self.cover_data is not None else _read_cover(self.key[0]))
        self.signals.finished.emit(self.key, artwork)


class CoverArtLoader(QObject):
    """Loads embedded covers off the UI thread and keeps a small memory cache."""

    artwork_ready = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: OrderedDict[tuple, CoverArtwork | None] = OrderedDict()
        self._cache_bytes = 0
        self._pending: set[tuple] = set()
        # Retain QRunnables until their queued completion slot has run. This
        # keeps their signal objects alive even when image decoding is fast.
        self._jobs: dict[tuple, _CoverTask] = {}

    @staticmethod
    def _key(path: str) -> tuple[str, int, int]:
        normalized = str(Path(path))
        try:
            stat = Path(normalized).stat()
            return normalized, stat.st_mtime_ns, stat.st_size
        except (OSError, ValueError, TypeError):
            return normalized, 0, 0

    def request(self, path: str, cover_data=None, mime_type=""):
        if not path:
            self.artwork_ready.emit("", None)
            return
        base_key = self._key(path)
        digest = hashlib.blake2s(bytes(cover_data), digest_size=8).hexdigest() if cover_data is not None else ""
        key = (*base_key, digest)
        if key in self._cache:
            artwork = self._cache.pop(key)
            self._cache[key] = artwork
            self.artwork_ready.emit(key[0], artwork)
            return
        if key in self._pending:
            return
        self._pending.add(key)
        task = _CoverTask(key, cover_data, mime_type)
        task.signals.finished.connect(self._complete)
        self._jobs[key] = task
        QThreadPool.globalInstance().start(task)

    @Slot(object, object)
    def _complete(self, key: tuple, artwork: CoverArtwork | None):
        self._pending.discard(key)
        self._jobs.pop(key, None)
        image_bytes = (len(artwork.data) + artwork.image.sizeInBytes()) if artwork else 0
        if image_bytes <= MAX_CACHE_BYTES:
            while (self._cache and
                   (len(self._cache) >= MAX_CACHE_ITEMS
                    or self._cache_bytes + image_bytes > MAX_CACHE_BYTES)):
                _old_key, old_artwork = self._cache.popitem(last=False)
                if old_artwork:
                    self._cache_bytes -= len(old_artwork.data) + old_artwork.image.sizeInBytes()
            self._cache[key] = artwork
            self._cache_bytes += image_bytes
        self.artwork_ready.emit(key[0], artwork)
