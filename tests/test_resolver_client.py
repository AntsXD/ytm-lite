"""The app-side resolver: fast and Premium resolves are requested and cached apart."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ytm_lite.app.resolver import ResolverClient
from ytm_lite.app.worker_client import WorkerProcess
from ytm_worker.protocol import Priority

COOKIES = "# Netscape HTTP Cookie File\n"


class RecordingWorker(WorkerProcess):
    """Never starts a process: records what would be sent."""

    def __init__(self, cache_dir: Path) -> None:
        super().__init__(cache_dir)
        self.sent: list[dict[str, Any]] = []

    def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


def result(video_id: str, *, fast: bool) -> dict[str, Any]:
    format_id = "251" if fast else "774"
    stream = {
        "video_id": video_id,
        "url": f"https://x.googlevideo.com/{video_id}/{format_id}",
        "format_id": format_id,
        "codec": "opus",
        "abr": 134.0 if fast else 256.0,
        "expires_at": time.time() + 6 * 3600,
        "available_at": None,
        "http_headers": {},
        "duration": 200.0,
        "premium": not fast,
    }
    return {"op": "result", "id": 1, "video_id": video_id, "fast": fast, "stream": stream}


@pytest.fixture
def rig(qapp: QApplication, tmp_path: Path) -> tuple[ResolverClient, RecordingWorker]:
    worker = RecordingWorker(tmp_path)
    resolver = ResolverClient(worker)
    resolver.configure(cookies=COOKIES)
    return resolver, worker


def test_fast_requests_only_differ_when_signed_in(
    rig: tuple[ResolverClient, RecordingWorker],
) -> None:
    resolver, worker = rig
    resolver.request("a", fast=True)
    assert worker.sent[-1]["fast"] is True
    resolver.configure(cookies=None)
    resolver.request("b", fast=True)
    assert worker.sent[-1]["fast"] is False


def test_fast_and_premium_are_requested_and_cached_apart(
    rig: tuple[ResolverClient, RecordingWorker],
) -> None:
    resolver, worker = rig
    resolver.request("a", fast=True)
    resolver.request("a", Priority.PREFETCH)
    resolver.request("a", Priority.PREFETCH)  # merged with the one already out
    assert [m["fast"] for m in worker.sent] == [True, False]

    worker.message.emit(result("a", fast=True))
    fast = resolver.cached("a")
    assert fast is not None and fast.fast and not fast.premium
    assert resolver.cached("a", best=True) is None  # never armed as a prefetch
    assert resolver.is_pending("a")  # the Premium resolve is still out

    worker.message.emit(result("a", fast=False))
    best = resolver.cached("a", best=True)
    assert best is not None and best.premium
    assert not resolver.is_pending("a")

    worker.message.emit(result("a", fast=True))  # a late fast answer...
    kept = resolver.cached("a")
    assert kept is not None and kept.premium  # ...doesn't push the Premium URL out


def test_a_cached_premium_stream_answers_a_fast_request(
    rig: tuple[ResolverClient, RecordingWorker], qtbot: QtBot
) -> None:
    resolver, worker = rig
    worker.message.emit(result("a", fast=False))
    sent = len(worker.sent)
    with qtbot.waitSignal(resolver.resolved, timeout=1000) as blocker:
        resolver.request("a", fast=True)
    assert len(worker.sent) == sent
    assert blocker.args is not None and blocker.args[1].premium


def test_a_fast_stream_does_not_answer_a_premium_request(
    rig: tuple[ResolverClient, RecordingWorker],
) -> None:
    resolver, worker = rig
    worker.message.emit(result("a", fast=True))
    resolver.request("a", Priority.PREFETCH)
    assert worker.sent[-1] == {**worker.sent[-1], "video_id": "a", "fast": False}


def test_failures_report_the_video(rig: tuple[ResolverClient, RecordingWorker]) -> None:
    resolver, worker = rig
    failures: list[tuple[str, str, str]] = []
    resolver.failed.connect(lambda *args: failures.append(args))
    resolver.request("a", fast=True)
    worker.message.emit(
        {
            "op": "error",
            "id": 1,
            "video_id": "a",
            "fast": True,
            "kind": "unavailable",
            "message": "x",
        }
    )
    assert failures == [("a", "unavailable", "x")]
    assert not resolver.is_pending("a")
