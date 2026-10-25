"""Stream resolving, from the app's side: a URL cache in front of the worker process."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from ytm_lite.app.worker_client import WorkerProcess
from ytm_worker.protocol import ErrorKind, Priority

log = logging.getLogger(__name__)

_CODEC_NAMES = {"opus": "Opus", "mp4a": "AAC", "vorbis": "Vorbis"}


@dataclass(frozen=True, slots=True)
class StreamInfo:
    video_id: str
    url: str
    format_id: str
    codec: str
    abr: float | None
    expires_at: float
    available_at: float | None
    http_headers: Mapping[str, str] = field(default_factory=dict)
    duration: float | None = None
    premium: bool = False
    fast: bool = False  # resolved by the quick standard-quality path

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], fast: bool = False) -> StreamInfo:
        return cls(
            video_id=str(d["video_id"]),
            url=str(d["url"]),
            format_id=str(d.get("format_id") or ""),
            codec=str(d.get("codec") or "?"),
            abr=float(d["abr"]) if d.get("abr") else None,
            expires_at=float(d["expires_at"]),
            available_at=float(d["available_at"]) if d.get("available_at") else None,
            http_headers={str(k): str(v) for k, v in (d.get("http_headers") or {}).items()},
            duration=float(d["duration"]) if d.get("duration") else None,
            premium=bool(d.get("premium")),
            fast=fast,
        )

    def valid_for(self, seconds: float, now: float | None = None) -> bool:
        """True if the URL will still work `seconds` from now (plus a 10 minute margin)."""
        return self.expires_at - (time.time() if now is None else now) > seconds + 600

    def wait_seconds(self, now: float | None = None) -> float:
        """How long googlevideo will keep answering 403 for this URL."""
        if self.available_at is None:
            return 0.0
        return max(0.0, self.available_at - (time.time() if now is None else now))

    @property
    def quality_text(self) -> str:
        codec = _CODEC_NAMES.get(self.codec.split(".")[0].lower(), self.codec)
        text = f"{codec} {round(self.abr)} kbps" if self.abr else codec
        return f"{text} · Premium" if self.premium else text


PendingKey = tuple[str, bool]  # (video_id, fast)


class ResolverClient(QObject):
    """Signed in, a stream is resolved one of two ways:

    - "fast" (a track you click): the signed-out path, ~1.5 s, standard quality;
    - best (everything prefetched): the Premium path, ~4 s, but nobody waits for it.

    The cache remembers which kind it holds, so a fast URL never stands in for a
    Premium prefetch. Signed out, both are the same.
    """

    resolved = Signal(str, object)  # video_id, StreamInfo
    failed = Signal(str, str, str)  # video_id, ErrorKind value, message

    CACHE_SIZE = 64

    def __init__(self, worker: WorkerProcess, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker = worker
        self._cache: OrderedDict[str, StreamInfo] = OrderedDict()
        self._pending: dict[PendingKey, int] = {}
        self._formats: dict[PendingKey, str | None] = {}
        self._next_id = 1
        worker.message.connect(self._handle)
        worker.exited.connect(self._on_worker_exit)

    @property
    def signed_in(self) -> bool:
        return bool(self._worker.config.get("cookies"))

    # -- public API -----------------------------------------------------------------

    def configure(
        self,
        *,
        cookies: str | None,
        js_runtime: str = "auto",
        player_clients: list[str] | None = None,
    ) -> None:
        if cookies != self._worker.config.get("cookies"):
            self._cache.clear()  # signed-in and anonymous resolves return different formats
        self._worker.configure(
            cookies=cookies, js_runtime=js_runtime, player_clients=list(player_clients or [])
        )

    @property
    def worker_info(self) -> dict[str, Any]:
        return self._worker.info

    def is_running(self) -> bool:
        return self._worker.is_running()

    def cached(
        self, video_id: str, valid_for: float = 0.0, best: bool = False
    ) -> StreamInfo | None:
        """A cached stream; with `best`, only one resolved the Premium way (when signed in)."""
        stream = self._cache.get(video_id)
        if stream is None:
            return None
        if not stream.valid_for(valid_for):
            del self._cache[video_id]
            return None
        if best and stream.fast:
            return None
        self._cache.move_to_end(video_id)
        return stream

    def invalidate(self, video_id: str) -> None:
        self._cache.pop(video_id, None)

    def is_pending(self, video_id: str) -> bool:
        return any(key[0] == video_id for key in self._pending)

    def request(
        self,
        video_id: str,
        priority: Priority = Priority.NOW,
        *,
        fast: bool = False,
        format_override: str | None = None,
    ) -> None:
        """Resolve `video_id`; the answer arrives via `resolved` or `failed`."""
        fast = fast and self.signed_in  # signed out, both ways are the same
        stream = self.cached(video_id, best=not fast)
        if stream is not None and format_override is None:
            QTimer.singleShot(0, lambda: self.resolved.emit(video_id, stream))
            return
        key = (video_id, fast)
        previous = self._pending.get(key)
        if previous is not None and previous <= priority:
            return
        self._pending[key] = int(priority)
        self._formats[key] = format_override
        self._send(key)

    def _send(self, key: PendingKey) -> None:
        self._worker.send(
            {
                "op": "resolve",
                "id": self._next_id,
                "video_id": key[0],
                "fast": key[1],
                "priority": self._pending[key],
                "format": self._formats.get(key),
            }
        )
        self._next_id += 1

    # -- worker events ----------------------------------------------------------------

    def _handle(self, message: dict[str, Any]) -> None:
        op = message["op"]
        key = (str(message.get("video_id")), bool(message.get("fast")))
        if op == "result":
            stream = StreamInfo.from_dict(message["stream"], fast=key[1])
            self._pending.pop(key, None)
            if self._formats.pop(key, None) is None:
                self._store(stream)
            self.resolved.emit(stream.video_id, stream)
        elif op == "error":
            self._pending.pop(key, None)
            self._formats.pop(key, None)
            log.warning(
                "resolve failed for %s (%s): %s", key[0], message["kind"], message["message"]
            )
            self.failed.emit(key[0], str(message["kind"]), str(message["message"]))

    def _store(self, stream: StreamInfo) -> None:
        existing = self._cache.get(stream.video_id)
        if existing is not None and stream.fast and not existing.fast and existing.valid_for(0):
            return  # keep the Premium URL
        self._cache[stream.video_id] = stream
        self._cache.move_to_end(stream.video_id)
        while len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)

    def _on_worker_exit(self, restartable: bool) -> None:
        if not self._pending:
            return
        if restartable:
            for key in list(self._pending):
                self._send(key)
            return
        pending, self._pending = self._pending, {}
        for video_id, _fast in pending:
            self.failed.emit(video_id, ErrorKind.INTERNAL.value, "The worker keeps crashing")
