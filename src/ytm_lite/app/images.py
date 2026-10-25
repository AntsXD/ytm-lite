"""Cover art: fetched at the size it's shown, cached on disk and in a small memory LRU.

Images are requested only by delegates painting visible rows. Newest requests go first,
and the queue is capped, so fast scrolling doesn't build a backlog of off-screen covers.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkDiskCache,
    QNetworkReply,
    QNetworkRequest,
)

DISK_CACHE_BYTES = 200 * 1024 * 1024
MEMORY_BYTES = 6 * 1024 * 1024
MEMORY_ITEMS = 150
MAX_IN_FLIGHT = 6
MAX_QUEUED = 120

_GOOGLE_IMAGE = re.compile(
    r"^(https://(?:lh\d|yt\d)\.(?:googleusercontent|ggpht)\.com/[^=?#]+)(?:=[^?#]*)?$"
)
_YTIMG = re.compile(r"^(https://i\d?\.ytimg\.com/vi(?:_webp)?/[^/]+/)")


def sized_url(url: str, px: int) -> str:
    """Ask Google's image servers for roughly `px` pixels instead of the full size."""
    match = _GOOGLE_IMAGE.match(url)
    if match:
        return f"{match.group(1)}=w{px}-h{px}-l90-rj"
    match = _YTIMG.match(url)
    if match:
        variant = "default.jpg" if px <= 90 else "mqdefault.jpg" if px <= 180 else "hqdefault.jpg"
        return match.group(1) + variant
    return url


def image_key(url: str, px: int) -> str:
    return f"{px}|{url}"


class ImageLoader(QObject):
    loaded = Signal(str)  # image_key of a newly available pixmap

    def __init__(self, cache_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        disk = QNetworkDiskCache(self)
        disk.setCacheDirectory(str(cache_dir / "images"))
        disk.setMaximumCacheSize(DISK_CACHE_BYTES)
        self._nam.setCache(disk)
        self._memory: OrderedDict[str, QPixmap] = OrderedDict()
        self._memory_bytes = 0
        self._in_flight: set[str] = set()
        self._queue: OrderedDict[str, tuple[str, int]] = OrderedDict()
        self._failed: set[str] = set()

    def get(self, url: str | None, px: int) -> QPixmap | None:
        """The pixmap if it's ready; otherwise start loading it and return None."""
        if not url:
            return None
        key = image_key(url, px)
        pixmap = self._memory.get(key)
        if pixmap is not None:
            self._memory.move_to_end(key)
            return pixmap
        if key in self._failed or key in self._in_flight:
            return None
        self._queue[key] = (url, px)
        self._queue.move_to_end(key)
        while len(self._queue) > MAX_QUEUED:
            self._queue.popitem(last=False)  # oldest requests are most likely off-screen now
        self._pump()
        return None

    def clear_memory(self) -> None:
        self._memory.clear()
        self._memory_bytes = 0
        self._queue.clear()

    def memory_usage(self) -> tuple[int, int]:
        return len(self._memory), self._memory_bytes

    def _pump(self) -> None:
        while self._queue and len(self._in_flight) < MAX_IN_FLIGHT:
            key, (url, px) = self._queue.popitem(last=True)  # newest first
            self._in_flight.add(key)
            request = QNetworkRequest(QUrl(sized_url(url, px)))
            request.setAttribute(
                QNetworkRequest.Attribute.CacheLoadControlAttribute,
                QNetworkRequest.CacheLoadControl.PreferCache,
            )
            reply = self._nam.get(request)
            reply.finished.connect(lambda r=reply, k=key, p=px: self._finished(r, k, p))

    def _finished(self, reply: QNetworkReply, key: str, px: int) -> None:
        self._in_flight.discard(key)
        pixmap = None
        if reply.error() == QNetworkReply.NetworkError.NoError:
            pixmap = self._decode(reply.readAll(), px)
        reply.deleteLater()
        if pixmap is None:
            self._failed.add(key)
        else:
            self._store(key, pixmap)
            self.loaded.emit(key)
        self._pump()

    @staticmethod
    def _decode(data: QByteArray, px: int) -> QPixmap | None:
        buffer = QBuffer()
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if size.isValid() and (size.width() > px or size.height() > px):
            # Decode straight to display size; the shorter side fills `px`.
            reader.setScaledSize(
                size.scaled(QSize(px, px), Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            )
        image = reader.read()
        return None if image.isNull() else QPixmap.fromImage(image)

    def _store(self, key: str, pixmap: QPixmap) -> None:
        cost = pixmap.width() * pixmap.height() * 4
        self._memory[key] = pixmap
        self._memory_bytes += cost
        while self._memory and (
            self._memory_bytes > MEMORY_BYTES or len(self._memory) > MEMORY_ITEMS
        ):
            _, old = self._memory.popitem(last=False)
            self._memory_bytes -= old.width() * old.height() * 4
