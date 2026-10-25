"""Background work on Qt thread pools, with results delivered on the UI thread.

- `api` pool (2 threads): ytmusicapi calls.
- `io` pool (1 thread): disk caches and state files.

Idle threads exit after 30 s. Results come back through one bridge QObject signal, so
callbacks always run on the UI thread. A `Channel` drops results that a newer request
has superseded (a new search, navigating to another page).
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Literal

from PySide6.QtCore import QObject, QThreadPool, Signal

log = logging.getLogger(__name__)

PoolName = Literal["api", "io"]


class Priority(IntEnum):
    """Higher runs first."""

    BACKGROUND = 0
    PAGING = 1
    INTERACTIVE = 2


@dataclass(slots=True)
class _Callbacks:
    on_done: Callable[[Any], None] | None
    on_error: Callable[[Exception], None] | None


class Task:
    def __init__(self, workers: Workers, task_id: int) -> None:
        self._workers = workers
        self.id = task_id

    def cancel(self) -> None:
        """Drop the result. Work that already started still runs to completion."""
        self._workers._callbacks.pop(self.id, None)


class Workers(QObject):
    _finished = Signal(int, object, object)  # task id, result, exception

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pools: dict[str, QThreadPool] = {}
        for name, threads in (("api", 2), ("io", 1)):
            pool = QThreadPool(self)
            pool.setMaxThreadCount(threads)
            pool.setExpiryTimeout(30_000)
            self._pools[name] = pool
        self._ids = itertools.count(1)
        self._callbacks: dict[int, _Callbacks] = {}
        self._finished.connect(self._deliver)

    def submit(
        self,
        fn: Callable[[], Any],
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        pool: PoolName = "api",
        priority: Priority = Priority.INTERACTIVE,
    ) -> Task:
        task_id = next(self._ids)
        self._callbacks[task_id] = _Callbacks(on_done, on_error)

        def run() -> None:
            try:
                result = fn()
            except Exception as exc:
                self._finished.emit(task_id, None, exc)
            else:
                self._finished.emit(task_id, result, None)

        self._pools[pool].start(run, int(priority))
        return Task(self, task_id)

    def _deliver(self, task_id: int, result: Any, error: Any) -> None:
        callbacks = self._callbacks.pop(task_id, None)
        if callbacks is None:
            return
        if error is not None:
            if callbacks.on_error is not None:
                callbacks.on_error(error)
            else:
                log.error("background task failed: %s", error, exc_info=error)
        elif callbacks.on_done is not None:
            callbacks.on_done(result)

    def shutdown(self, msecs: int = 2000) -> None:
        self._callbacks.clear()
        for pool in self._pools.values():
            pool.clear()
            pool.waitForDone(msecs)


class Channel:
    """Latest-wins submissions: results of superseded requests are dropped."""

    def __init__(self, workers: Workers) -> None:
        self._workers = workers
        self._task: Task | None = None

    def submit(
        self,
        fn: Callable[[], Any],
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        pool: PoolName = "api",
        priority: Priority = Priority.INTERACTIVE,
    ) -> Task:
        self.cancel()
        self._task = self._workers.submit(
            fn, on_done=on_done, on_error=on_error, pool=pool, priority=priority
        )
        return self._task

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
