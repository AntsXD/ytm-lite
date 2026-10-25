"""Client side of the resolver worker (see ytm_resolver).

The worker runs only while there is work: it starts on demand and is told to exit after
a short idle period, so yt-dlp's memory isn't held while music just plays.
"""

from __future__ import annotations

import logging
import sys
import time
from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ytm_lite.app.config import app_root, is_frozen
from ytm_resolver.protocol import ErrorKind, Priority, decode, encode

log = logging.getLogger(__name__)
worker_log = logging.getLogger("ytm_resolver.worker")

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

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> StreamInfo:
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


class ResolverClient(QObject):
    resolved = Signal(str, object)  # video_id, StreamInfo
    failed = Signal(str, str, str)  # video_id, ErrorKind value, message
    worker_ready = Signal(object)  # the worker's "ready" message (versions, JS runtime)

    # The worker holds ~45-60 MB while alive; bursts of skipping keep it running anyway.
    IDLE_TIMEOUT_MS = 15_000
    CACHE_SIZE = 64
    MAX_RESTARTS = 3  # per minute, while requests are pending

    def __init__(self, cache_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache: OrderedDict[str, StreamInfo] = OrderedDict()
        self._pending: dict[str, int] = {}
        self._formats: dict[str, str | None] = {}
        self._config: dict[str, Any] = {
            "op": "configure",
            "cachedir": str(cache_dir / "yt-dlp"),
            "cookies": None,
            "js_runtime": "auto",
            "player_clients": [],
        }
        self._proc: QProcess | None = None
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._next_id = 1
        self._restarts: deque[float] = deque(maxlen=self.MAX_RESTARTS)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(self.IDLE_TIMEOUT_MS)
        self._idle.timeout.connect(self._on_idle)
        self.worker_info: dict[str, Any] | None = None

    # -- public API -----------------------------------------------------------------

    def configure(
        self,
        *,
        cookies: str | None,
        js_runtime: str = "auto",
        player_clients: list[str] | None = None,
    ) -> None:
        if cookies != self._config["cookies"]:
            self._cache.clear()  # signed-in and anonymous resolves return different formats
        self._config.update(
            cookies=cookies, js_runtime=js_runtime, player_clients=list(player_clients or [])
        )
        if self.is_running():
            self._write(self._config)

    def cached(self, video_id: str, valid_for: float = 0.0) -> StreamInfo | None:
        stream = self._cache.get(video_id)
        if stream is None:
            return None
        if not stream.valid_for(valid_for):
            del self._cache[video_id]
            return None
        self._cache.move_to_end(video_id)
        return stream

    def invalidate(self, video_id: str) -> None:
        self._cache.pop(video_id, None)

    def is_pending(self, video_id: str) -> bool:
        return video_id in self._pending

    def request(
        self,
        video_id: str,
        priority: Priority = Priority.NOW,
        *,
        format_override: str | None = None,
    ) -> None:
        """Resolve `video_id`; the answer arrives via `resolved` or `failed`."""
        stream = self.cached(video_id)
        if stream is not None and format_override is None:
            QTimer.singleShot(0, lambda: self.resolved.emit(video_id, stream))
            return
        previous = self._pending.get(video_id)
        if previous is not None and previous <= priority:
            return
        self._pending[video_id] = int(priority)
        self._formats[video_id] = format_override
        self._ensure_running()
        self._send_resolve(video_id)

    def warm_up(self) -> None:
        """Start the worker ahead of the first request."""
        self._ensure_running()
        self._idle.start()

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def shutdown(self) -> None:
        self._idle.stop()
        proc, self._proc = self._proc, None
        if proc is None:
            return
        proc.finished.disconnect()
        proc.errorOccurred.disconnect()
        if proc.state() != QProcess.ProcessState.NotRunning:
            proc.write(encode({"op": "shutdown"}))
            proc.closeWriteChannel()
            if not proc.waitForFinished(2000):
                proc.kill()
                proc.waitForFinished(1000)

    # -- process management ---------------------------------------------------------

    @staticmethod
    def worker_command() -> tuple[str, list[str]]:
        if is_frozen():
            name = "pythonw.exe" if sys.platform == "win32" else "bin/python3"
            python = app_root() / "resolver" / name
        else:
            python = Path(sys.executable)
            pythonw = python.with_name("pythonw.exe")
            if sys.platform == "win32" and pythonw.exists():
                python = pythonw  # no console window
        return str(python), ["-I", "-m", "ytm_resolver"]

    def _ensure_running(self) -> None:
        self._idle.stop()
        if self.is_running():
            return
        program, args = self.worker_command()
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._stdout.clear()
        self._stderr.clear()
        self._proc = proc
        proc.start()
        log.info("resolver worker starting")
        self._write(self._config)

    def _retire(self, proc: QProcess) -> None:
        """Let a worker finish on its own; new requests will start a fresh one."""
        for signal in (
            proc.finished,
            proc.errorOccurred,
            proc.readyReadStandardOutput,
            proc.readyReadStandardError,
        ):
            signal.disconnect()
        proc.finished.connect(proc.deleteLater)
        proc.write(encode({"op": "shutdown"}))
        proc.closeWriteChannel()

    def _write(self, message: dict[str, Any]) -> None:
        if self._proc is not None:
            self._proc.write(encode(message))

    def _send_resolve(self, video_id: str) -> None:
        self._write(
            {
                "op": "resolve",
                "id": self._next_id,
                "video_id": video_id,
                "priority": self._pending[video_id],
                "format": self._formats.get(video_id),
            }
        )
        self._next_id += 1

    def _on_idle(self) -> None:
        if self._pending or self._proc is None:
            return
        log.info("resolver worker idle; stopping it")
        proc, self._proc = self._proc, None
        self._retire(proc)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart:
            return  # crashes are handled in _on_finished
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.deleteLater()
        log.error("could not start the resolver worker: %s", self.worker_command()[0])
        pending, self._pending = self._pending, {}
        for video_id in pending:
            self.failed.emit(video_id, ErrorKind.INTERNAL.value, "Could not start the resolver")

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.deleteLater()
        if not self._pending:
            log.info("resolver worker exited (%s)", exit_code)
            return
        now = time.monotonic()
        recent = [t for t in self._restarts if now - t < 60]
        if len(recent) >= self.MAX_RESTARTS:
            log.error("resolver worker keeps crashing (exit %s); giving up", exit_code)
            pending, self._pending = self._pending, {}
            for video_id in pending:
                self.failed.emit(video_id, ErrorKind.INTERNAL.value, "The resolver keeps crashing")
            return
        log.warning("resolver worker died (exit %s); restarting", exit_code)
        self._restarts.append(now)
        self._ensure_running()
        for video_id in self._pending:
            self._send_resolve(video_id)

    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        self._stdout += self._proc.readAllStandardOutput().data()
        while (end := self._stdout.find(b"\n")) >= 0:
            line = bytes(self._stdout[:end])
            del self._stdout[: end + 1]
            try:
                self._handle(decode(line))
            except (ValueError, KeyError):
                log.warning("bad message from resolver worker")

    def _handle(self, message: dict[str, Any]) -> None:
        op = message["op"]
        if op == "ready":
            self.worker_info = message
            self.worker_ready.emit(message)
        elif op == "result":
            stream = StreamInfo.from_dict(message["stream"])
            video_id = stream.video_id
            self._pending.pop(video_id, None)
            if self._formats.pop(video_id, None) is None:
                self._cache[video_id] = stream
                self._cache.move_to_end(video_id)
                while len(self._cache) > self.CACHE_SIZE:
                    self._cache.popitem(last=False)
            self.resolved.emit(video_id, stream)
        elif op == "error":
            video_id = str(message["video_id"])
            self._pending.pop(video_id, None)
            self._formats.pop(video_id, None)
            log.warning(
                "resolve failed for %s (%s): %s", video_id, message["kind"], message["message"]
            )
            self.failed.emit(video_id, str(message["kind"]), str(message["message"]))
        if not self._pending:
            self._idle.start()

    def _on_stderr(self) -> None:
        if self._proc is None:
            return
        self._stderr += self._proc.readAllStandardError().data()
        while (end := self._stderr.find(b"\n")) >= 0:
            line = bytes(self._stderr[:end]).decode("utf-8", "replace").rstrip()
            del self._stderr[: end + 1]
            level_name, _, rest = line.partition(" ")
            level = logging.getLevelName(level_name)
            if isinstance(level, int):
                worker_log.log(level, "%s", rest)
            elif line:
                worker_log.info("%s", line)
