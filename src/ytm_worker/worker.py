"""The worker loop: read requests from stdin, work on small thread pools, reply on stdout.

- Stream resolves (yt-dlp): requests for the same videoId are merged, and a NOW request
  overtakes queued PREFETCH work.
- API calls (ytmusicapi): run on their own two threads.

The process exits by itself after `idle_timeout` seconds with nothing to do (saying
"bye" first), and when stdin closes, so it never outlives the app.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, BinaryIO

from ytm_worker import jsruntime
from ytm_worker.jsruntime import JsRuntime
from ytm_worker.protocol import ApiErrorKind, ErrorKind, Priority, decode, encode
from ytm_worker.resolve import ResolveError, Resolver, ResolverConfig
from ytm_worker.ytm import ServiceError, YtmService

log = logging.getLogger("ytm_worker")

RESOLVE_THREADS = 2
API_THREADS = 2
DEFAULT_IDLE_TIMEOUT = 30.0


@dataclass(slots=True)
class _Job:
    video_id: str
    priority: int
    format: str | None
    request_ids: list[int] = field(default_factory=list)
    started: bool = False


def _versions() -> dict[str, str]:
    out = {"python": sys.version.split()[0]}
    for dist in ("yt-dlp", "yt-dlp-ejs", "ytmusicapi"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = "missing"
    return out


def lower_priority() -> None:
    """Run below normal priority so work never competes with a game for CPU.

    Child processes (the JS runtime) inherit this on Windows and POSIX.
    """
    try:
        if sys.platform == "win32":
            import ctypes

            below_normal = 0x00004000
            kernel32 = ctypes.windll.kernel32
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), below_normal)
        else:
            os.nice(5)
    except OSError:
        log.debug("could not lower priority", exc_info=True)


class Worker:
    def __init__(
        self,
        out: BinaryIO,
        threads: int = RESOLVE_THREADS,
        service: YtmService | None = None,
        exit_process: Callable[[], None] | None = None,
    ) -> None:
        self._out = out
        self._out_lock = threading.Lock()
        self._cond = threading.Condition()
        self._heap: list[tuple[int, int, str]] = []
        self._jobs: dict[str, _Job] = {}
        self._seq = itertools.count()
        self._config: ResolverConfig | None = None
        self._config_gen = 0
        self._runtime_pref = "auto"
        self._runtimes: dict[str, JsRuntime | None] = {}
        self._stopping = False
        self._local = threading.local()
        self._api_busy = 0
        self._last_activity = time.monotonic()
        self.idle_timeout = DEFAULT_IDLE_TIMEOUT
        self._exit_process = exit_process or (lambda: os._exit(0))
        self.service = service or YtmService()
        self.service.on_visitor = lambda visitor_id: self._send(
            {"op": "visitor", "visitor_id": visitor_id}
        )
        self._api_pool = ThreadPoolExecutor(max_workers=API_THREADS, thread_name_prefix="api")
        self._threads = [
            threading.Thread(target=self._run, name=f"resolve-{i}", daemon=True)
            for i in range(threads)
        ]
        for thread in self._threads:
            thread.start()
        threading.Thread(target=self._watch_idle, name="idle", daemon=True).start()

    # -- stdin side ---------------------------------------------------------------------

    def serve(self, source: BinaryIO) -> None:
        for raw in source:
            if not raw.strip():
                continue
            try:
                message = decode(raw)
            except ValueError:
                log.warning("ignoring malformed request")
                continue
            if not self.handle(message):
                break
        self.stop()

    def handle(self, message: dict[str, Any]) -> bool:
        """Process one request. Returns False when the worker should exit."""
        self._touch()
        op = message["op"]
        if op == "configure":
            self._configure(message)
        elif op == "resolve":
            self._enqueue(message)
        elif op == "api":
            with self._cond:
                self._api_busy += 1
            self._api_pool.submit(self._run_api, message)
        elif op == "shutdown":
            return False
        else:
            log.warning("unknown op %r", op)
        return True

    def _configure(self, message: dict[str, Any]) -> None:
        config = ResolverConfig(
            cachedir=str(message["cachedir"]),
            cookies=message.get("cookies") or None,
            player_clients=tuple(message.get("player_clients") or ()),
        )
        self.idle_timeout = float(message.get("idle_timeout") or DEFAULT_IDLE_TIMEOUT)
        self.service.configure(message.get("api_headers"), message.get("visitor_id"))
        # Announce first, so "ready" always precedes results made with this config.
        self._send(
            {
                "op": "ready",
                "pid": os.getpid(),
                "versions": _versions(),
                "signed_in": config.signed_in,
            }
        )
        with self._cond:
            self._runtime_pref = str(message.get("js_runtime") or "auto")
            self._config = config
            self._config_gen += 1
            self._cond.notify_all()

    def _enqueue(self, message: dict[str, Any]) -> None:
        video_id = str(message["video_id"])
        request_id = int(message.get("id", 0))
        priority = int(message.get("priority", Priority.NOW))
        with self._cond:
            job = self._jobs.get(video_id)
            if job is None:
                job = _Job(video_id, priority, message.get("format"))
                self._jobs[video_id] = job
                heapq.heappush(self._heap, (priority, next(self._seq), video_id))
            elif not job.started and priority < job.priority:
                job.priority = priority
                heapq.heappush(self._heap, (priority, next(self._seq), video_id))
            job.request_ids.append(request_id)
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()

    # -- idle exit -----------------------------------------------------------------------

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    def _watch_idle(self) -> None:
        while True:
            time.sleep(0.5)
            with self._cond:
                if self._stopping:
                    return
                busy = bool(self._jobs) or self._api_busy > 0
                idle_for = time.monotonic() - self._last_activity
                if busy or idle_for < self.idle_timeout:
                    continue
            log.info("idle for %.0fs; exiting", idle_for)
            self._send({"op": "bye"})
            self._exit_process()
            return

    # -- JS runtime ------------------------------------------------------------------------

    def _runtime(self) -> JsRuntime | None:
        """Detect the JS runtime the first time a resolve needs one."""
        with self._cond:
            pref = self._runtime_pref
            if pref in self._runtimes:
                return self._runtimes[pref]
        runtime = jsruntime.detect(pref)
        with self._cond:
            first = pref not in self._runtimes
            self._runtimes[pref] = runtime
        if first:
            if runtime is None:
                log.warning("no JavaScript runtime found; signed-in playback will fail")
            self._send(
                {
                    "op": "runtime",
                    "js_runtime": None
                    if runtime is None
                    else {"name": runtime.name, "version": runtime.version},
                }
            )
        return runtime

    # -- resolver threads --------------------------------------------------------------------

    def _next_job(self) -> tuple[_Job, ResolverConfig, int] | None:
        with self._cond:
            while True:
                if self._stopping:
                    return None
                if self._config is not None:
                    while self._heap:
                        priority, _, video_id = heapq.heappop(self._heap)
                        job = self._jobs.get(video_id)
                        if job is not None and not job.started and job.priority == priority:
                            job.started = True
                            return job, self._config, self._config_gen
                self._cond.wait()

    def _resolver(self, config: ResolverConfig, generation: int) -> Resolver:
        current: tuple[int, Resolver] | None = getattr(self._local, "resolver", None)
        if current is None or current[0] != generation:
            current = (generation, Resolver(config, self._runtime))
            self._local.resolver = current
        return current[1]

    def _run(self) -> None:
        while (picked := self._next_job()) is not None:
            job, config, generation = picked
            reply: dict[str, Any]
            try:
                stream = self._resolver(config, generation).resolve(job.video_id, job.format)
                reply = {"op": "result", "video_id": job.video_id, "stream": stream}
            except ResolveError as exc:
                reply = {
                    "op": "error",
                    "video_id": job.video_id,
                    "kind": exc.kind.value,
                    "message": str(exc),
                }
            except Exception as exc:
                log.exception("unexpected resolver failure for %s", job.video_id)
                reply = {
                    "op": "error",
                    "video_id": job.video_id,
                    "kind": ErrorKind.INTERNAL.value,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            with self._cond:
                self._jobs.pop(job.video_id, None)
                request_ids = list(job.request_ids)
            self._touch()
            for request_id in request_ids:
                self._send({**reply, "id": request_id})

    # -- API calls ------------------------------------------------------------------------

    def _run_api(self, message: dict[str, Any]) -> None:
        request_id = int(message.get("id", 0))
        try:
            result = self.service.call(
                str(message["method"]),
                dict(message.get("args") or {}),
                background=bool(message.get("background")),
                headers=message.get("headers"),
            )
            reply: dict[str, Any] = {"op": "api_result", "id": request_id, "result": result}
        except ServiceError as exc:
            reply = {
                "op": "api_error",
                "id": request_id,
                "kind": exc.kind.value,
                "message": str(exc),
            }
        except Exception as exc:
            log.exception("unexpected API failure in %s", message.get("method"))
            reply = {
                "op": "api_error",
                "id": request_id,
                "kind": ApiErrorKind.SERVER.value,
                "message": f"{type(exc).__name__}: {exc}",
            }
        self._send(reply)
        with self._cond:
            self._api_busy -= 1
        self._touch()

    def _send(self, message: dict[str, Any]) -> None:
        data = encode(message)
        with self._out_lock:
            try:
                self._out.write(data)
                self._out.flush()
            except (OSError, ValueError):
                # The app went away; stdin will close too and the loop will end.
                pass
