from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any, BinaryIO, ClassVar, cast

import pytest

from ytm_resolver import jsruntime
from ytm_resolver import worker as worker_mod
from ytm_resolver.protocol import Priority, StreamDict
from ytm_resolver.resolve import ResolverConfig


class Sink:
    """Collects the worker's stdout messages."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._cond = threading.Condition()

    def write(self, data: bytes) -> int:
        with self._cond:
            for line in data.decode().splitlines():
                self.messages.append(json.loads(line))
            self._cond.notify_all()
        return len(data)

    def flush(self) -> None:
        pass

    def wait_for(self, count: int, timeout: float = 5) -> list[dict[str, Any]]:
        with self._cond:
            assert self._cond.wait_for(lambda: len(self.messages) >= count, timeout)
            return list(self.messages)


class FakeResolver:
    calls: ClassVar[list[str]] = []
    gate: ClassVar[threading.Event] = threading.Event()

    def __init__(self, config: ResolverConfig) -> None:
        self.config = config

    def resolve(self, video_id: str, format_override: str | None = None) -> StreamDict:
        FakeResolver.calls.append(video_id)
        if video_id == "slow":
            FakeResolver.gate.wait(5)
        return StreamDict(
            video_id=video_id,
            url=f"https://x.googlevideo.com/{video_id}",
            format_id="251",
            codec="opus",
            abr=134.0,
            expires_at=2e9,
            available_at=None,
            http_headers={},
            duration=10.0,
            premium=False,
        )


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> Iterator[Sink]:
    monkeypatch.setattr(worker_mod, "Resolver", FakeResolver)
    monkeypatch.setattr(jsruntime, "detect", lambda preferred="auto": None)
    FakeResolver.calls = []
    FakeResolver.gate = threading.Event()
    yield Sink()
    FakeResolver.gate.set()


def _resolve(video_id: str, request_id: int, priority: Priority = Priority.NOW) -> dict[str, Any]:
    return {"op": "resolve", "id": request_id, "video_id": video_id, "priority": int(priority)}


def test_configure_reports_ready_then_resolves(sink: Sink) -> None:
    w = worker_mod.Worker(cast(BinaryIO, sink), threads=1)
    w.handle({"op": "configure", "cachedir": "c", "cookies": None})
    w.handle(_resolve("abc", 7))
    ready, result = sink.wait_for(2)
    w.stop()
    assert ready["op"] == "ready" and ready["js_runtime"] is None and not ready["signed_in"]
    assert result == {**result, "op": "result", "id": 7, "video_id": "abc"}
    assert result["stream"]["url"].endswith("/abc")


def test_requests_wait_for_configure(sink: Sink) -> None:
    w = worker_mod.Worker(cast(BinaryIO, sink), threads=1)
    w.handle(_resolve("abc", 1))
    w.handle({"op": "configure", "cachedir": "c"})
    messages = sink.wait_for(2)
    w.stop()
    assert [m["op"] for m in messages] == ["ready", "result"]


def test_duplicate_requests_are_merged(sink: Sink) -> None:
    w = worker_mod.Worker(cast(BinaryIO, sink), threads=1)
    w.handle(_resolve("slow", 1))  # occupies the only thread
    w.handle(_resolve("dup", 2, Priority.PREFETCH))
    w.handle(_resolve("dup", 3, Priority.NOW))
    w.handle({"op": "configure", "cachedir": "c"})
    FakeResolver.gate.set()
    messages = sink.wait_for(4)
    w.stop()
    assert FakeResolver.calls.count("dup") == 1
    assert sorted(m["id"] for m in messages if m.get("video_id") == "dup") == [2, 3]


def test_now_overtakes_queued_prefetch(sink: Sink) -> None:
    w = worker_mod.Worker(cast(BinaryIO, sink), threads=1)
    w.handle({"op": "configure", "cachedir": "c"})
    sink.wait_for(1)
    w.handle(_resolve("slow", 1))
    w.handle(_resolve("later", 2, Priority.PREFETCH))
    w.handle(_resolve("urgent", 3, Priority.NOW))
    FakeResolver.gate.set()
    sink.wait_for(4)
    w.stop()
    assert FakeResolver.calls == ["slow", "urgent", "later"]


def test_shutdown_op_ends_serving(sink: Sink) -> None:
    w = worker_mod.Worker(cast(BinaryIO, sink), threads=1)
    assert w.handle({"op": "shutdown"}) is False
    w.stop()
