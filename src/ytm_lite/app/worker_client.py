"""The worker process (see ytm_worker), from the app's side.

The worker starts on demand and exits by itself when idle. This class can be used from
any thread:
- `call_api()` blocks the calling (pool) thread until the answer arrives;
- other messages (stream resolves) are delivered on the UI thread via `message`.

If the worker dies with work outstanding, it's restarted and the API requests resent;
`exited` tells the resolver client to resend its own.
"""

from __future__ import annotations

import itertools
import logging
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future
from pathlib import Path
from typing import IO, Any

from PySide6.QtCore import QObject, Signal

from ytm_lite.app.config import app_root, is_frozen
from ytm_worker.protocol import ApiErrorKind, decode, encode

log = logging.getLogger(__name__)
worker_log = logging.getLogger("ytm_worker")

# The worker holds ~60-75 MB while alive (yt-dlp, ytmusicapi, requests). It's needed
# for a couple of seconds per track change and for browsing; skipping and browsing
# bursts keep it running anyway.
IDLE_TIMEOUT = 15.0
API_TIMEOUT = 120.0  # a full fetch of a huge playlist takes a while
MAX_RESTARTS = 3  # per minute, while work is outstanding


class WorkerError(Exception):
    """An API call failed; `kind` is an ApiErrorKind value."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def worker_command() -> list[str]:
    if is_frozen():
        name = "pythonw.exe" if sys.platform == "win32" else "bin/python3"
        python = app_root() / "worker" / name
    else:
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        if sys.platform == "win32" and pythonw.exists():
            python = pythonw  # no console window
    return [str(python), "-I", "-m", "ytm_worker"]


class WorkerProcess(QObject):
    message = Signal(object)  # resolver messages ("ready", "result", "error", "runtime")
    exited = Signal(bool)  # the worker ended; True if it should be restarted for pending work

    def __init__(self, cache_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[dict[str, Any], Future[Any]]] = {}
        self._restarts: deque[float] = deque(maxlen=MAX_RESTARTS)
        self._closing = False
        self.command = worker_command()
        self.config: dict[str, Any] = {
            "op": "configure",
            "cachedir": str(cache_dir / "yt-dlp"),
            "cookies": None,
            "js_runtime": "auto",
            "player_clients": [],
            "api_headers": None,
            "visitor_id": None,
            "idle_timeout": IDLE_TIMEOUT,
        }
        self.info: dict[str, Any] = {}  # latest "ready" and "runtime" details

    # -- public -------------------------------------------------------------------------

    def configure(self, **fields: Any) -> None:
        with self._lock:
            changed = any(self.config.get(k) != v for k, v in fields.items())
            self.config.update(fields)
            if changed and self._alive():
                self._write(self.config)

    def send(self, message: dict[str, Any]) -> None:
        """Send a message, starting the worker if needed."""
        with self._lock:
            self._ensure_running()
            self._write(message)

    def call_api(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        background: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Run an API method in the worker and wait for the result (don't call on the UI
        thread). Raises WorkerError."""
        future: Future[Any] = Future()
        request_id = next(self._ids)
        request: dict[str, Any] = {
            "op": "api",
            "id": request_id,
            "method": method,
            "args": args or {},
            "background": background,
            "headers": headers,
        }
        with self._lock:
            self._pending[request_id] = (request, future)
            self.send(request)
        try:
            return future.result(API_TIMEOUT)
        except TimeoutError:
            with self._lock:
                self._pending.pop(request_id, None)
            raise WorkerError(ApiErrorKind.NETWORK.value, "YouTube didn't answer in time") from None

    def is_running(self) -> bool:
        with self._lock:
            return self._alive()

    def shutdown(self) -> None:
        with self._lock:
            self._closing = True
            proc, self._proc = self._proc, None
            for _, future in self._pending.values():
                future.set_exception(WorkerError(ApiErrorKind.SERVER.value, "shutting down"))
            self._pending.clear()
        if proc is None:
            return
        try:
            proc.stdin.write(encode({"op": "shutdown"}))  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
            proc.wait(2)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            proc.kill()

    # -- process ------------------------------------------------------------------------

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure_running(self) -> None:
        if self._alive() or self._closing:
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=flags,
        )
        self._proc = proc
        log.info("worker started (pid %s)", proc.pid)
        assert proc.stdout is not None and proc.stderr is not None
        threading.Thread(
            target=self._read, args=(proc, proc.stdout), name="worker-out", daemon=True
        ).start()
        threading.Thread(
            target=self._read_log, args=(proc.stderr,), name="worker-err", daemon=True
        ).start()
        self._write(self.config)

    def _write(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(encode(message))
            proc.stdin.flush()
        except (OSError, ValueError):
            pass  # it died; the reader sees EOF and recovers

    def _read(self, proc: subprocess.Popen[bytes], stream: IO[bytes]) -> None:
        said_bye = False
        for line in stream:
            try:
                message = decode(line)
            except ValueError:
                log.warning("bad message from worker")
                continue
            op = message["op"]
            if op in ("api_result", "api_error"):
                with self._lock:
                    entry = self._pending.pop(int(message["id"]), None)
                if entry is not None:
                    _, future = entry
                    if op == "api_result":
                        future.set_result(message.get("result"))
                    else:
                        future.set_exception(
                            WorkerError(str(message["kind"]), str(message["message"]))
                        )
            elif op == "visitor":
                with self._lock:
                    self.config["visitor_id"] = message.get("visitor_id")
            elif op == "bye":
                said_bye = True
            else:
                if op in ("ready", "runtime"):
                    self.info.update({k: v for k, v in message.items() if k != "op"})
                self.message.emit(message)
        proc.wait()
        self._on_exit(proc, said_bye)

    def _on_exit(self, proc: subprocess.Popen[bytes], said_bye: bool) -> None:
        with self._lock:
            if self._proc is not proc or self._closing:
                return
            self._proc = None
            log.info("worker exited (%s%s)", proc.returncode, ", idle" if said_bye else "")
            if not said_bye:
                now = time.monotonic()
                recent = [t for t in self._restarts if now - t < 60]
                if len(recent) >= MAX_RESTARTS:
                    log.error("worker keeps crashing; giving up on pending work")
                    for _, future in self._pending.values():
                        future.set_exception(
                            WorkerError(ApiErrorKind.SERVER.value, "the worker keeps crashing")
                        )
                    self._pending.clear()
                    self.exited.emit(False)
                    return
                self._restarts.append(now)
            if self._pending:
                self._ensure_running()
                for request, _ in list(self._pending.values()):
                    self._write(request)
        self.exited.emit(True)

    def _read_log(self, stream: IO[bytes]) -> None:
        for raw in stream:
            line = raw.decode("utf-8", "replace").rstrip()
            level_name, _, rest = line.partition(" ")
            level = logging.getLevelName(level_name)
            if isinstance(level, int):
                worker_log.log(level, "%s", rest)
            elif line:
                worker_log.info("%s", line)
