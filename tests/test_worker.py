from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any, BinaryIO, ClassVar, cast

import pytest

from ytm_worker import jsruntime
from ytm_worker import worker as worker_mod
from ytm_worker.protocol import ApiErrorKind, Priority, StreamDict
from ytm_worker.resolve import ResolverConfig
from ytm_worker.ytm import ServiceError, YtmService


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

    def __init__(self, config: ResolverConfig, runtime: Any = None) -> None:
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


class FakeService(YtmService):
    def call(
        self,
        method: str,
        args: dict[str, Any],
        *,
        background: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if method == "boom":
            raise ServiceError(ApiErrorKind.AUTH, "expired")
        return {"method": method, "args": args, "background": background}


def make_worker(sink: Sink, exits: list[bool] | None = None) -> worker_mod.Worker:
    return worker_mod.Worker(
        cast(BinaryIO, sink),
        threads=1,
        service=FakeService(),
        exit_process=lambda: exits.append(True) if exits is not None else None,
    )


def test_configure_reports_ready_then_resolves(sink: Sink) -> None:
    w = make_worker(sink)
    w.handle({"op": "configure", "cachedir": "c", "cookies": None})
    w.handle(_resolve("abc", 7))
    ready, result = sink.wait_for(2)
    w.stop()
    assert ready["op"] == "ready" and not ready["signed_in"]
    assert result == {**result, "op": "result", "id": 7, "video_id": "abc"}
    assert result["stream"]["url"].endswith("/abc")


def test_api_calls(sink: Sink) -> None:
    w = make_worker(sink)
    w.handle({"op": "api", "id": 1, "method": "playlist", "args": {"limit": 5}, "background": True})
    w.handle({"op": "api", "id": 2, "method": "boom"})
    messages = sink.wait_for(2)
    w.stop()
    by_id = {m["id"]: m for m in messages}
    assert by_id[1] == {
        "op": "api_result",
        "id": 1,
        "result": {"method": "playlist", "args": {"limit": 5}, "background": True},
    }
    assert by_id[2] == {"op": "api_error", "id": 2, "kind": "auth", "message": "expired"}


def test_idle_worker_says_bye_and_exits(sink: Sink) -> None:
    exits: list[bool] = []
    w = make_worker(sink, exits)
    w.idle_timeout = 0.2
    sink.wait_for(1, timeout=5)
    assert sink.messages[-1] == {"op": "bye"}
    assert exits == [True]
    w.stop()


def test_js_runtime_is_detected_once_and_reported(sink: Sink) -> None:
    w = make_worker(sink)
    assert w._runtime() is None
    assert w._runtime() is None
    w.stop()
    assert [m for m in sink.messages if m["op"] == "runtime"] == [
        {"op": "runtime", "js_runtime": None}
    ]


def test_requests_wait_for_configure(sink: Sink) -> None:
    w = make_worker(sink)
    w.handle(_resolve("abc", 1))
    w.handle({"op": "configure", "cachedir": "c"})
    messages = sink.wait_for(2)
    w.stop()
    assert [m["op"] for m in messages] == ["ready", "result"]


def test_duplicate_requests_are_merged(sink: Sink) -> None:
    w = make_worker(sink)
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
    w = make_worker(sink)
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
    w = make_worker(sink)
    assert w.handle({"op": "shutdown"}) is False
    w.stop()
