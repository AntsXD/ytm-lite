"""WorkerProcess against a tiny stand-in worker script (a real subprocess, no network)."""

from __future__ import annotations

import sys
import textwrap
import threading
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ytm_lite.app.worker_client import WorkerError, WorkerProcess

STUB = textwrap.dedent(
    """
    import json, os, sys
    marker = sys.argv[1]
    starts = int(open(marker).read()) + 1 if os.path.exists(marker) else 1
    open(marker, "w").write(str(starts))
    def send(msg):
        sys.stdout.write(json.dumps(msg) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        op = msg["op"]
        if op == "configure":
            send({"op": "ready", "pid": os.getpid(), "starts": starts})
        elif op == "api":
            method = msg["method"]
            if method == "crash_once" and starts == 1:
                os._exit(3)
            if method == "fail":
                send({"op": "api_error", "id": msg["id"], "kind": "auth", "message": "nope"})
            elif method == "idle_exit":
                send({"op": "api_result", "id": msg["id"], "result": "ok"})
                send({"op": "bye"})
                os._exit(0)
            else:
                send({"op": "api_result", "id": msg["id"], "result": [method, msg["args"], starts]})
        elif op == "resolve":
            send({"op": "error", "id": msg["id"], "video_id": msg["video_id"],
                  "kind": "unavailable", "message": "stub"})
        elif op == "shutdown":
            break
    """
)


@pytest.fixture
def worker(qapp: QApplication, tmp_path: Path) -> Any:
    process = WorkerProcess(tmp_path)
    process.command = [sys.executable, "-c", STUB, str(tmp_path / "starts")]
    yield process
    process.shutdown()


def call_in_thread(worker: WorkerProcess, method: str, results: list[Any]) -> threading.Thread:
    def run() -> None:
        try:
            results.append(worker.call_api(method, {"x": 1}))
        except WorkerError as exc:
            results.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_api_round_trip_and_errors(worker: WorkerProcess) -> None:
    results: list[Any] = []
    call_in_thread(worker, "echo", results).join(10)
    call_in_thread(worker, "fail", results).join(10)
    assert results[0] == ["echo", {"x": 1}, 1]
    assert isinstance(results[1], WorkerError) and results[1].kind == "auth"
    assert worker.is_running()
    assert worker.info.get("starts") == 1


def test_crash_restarts_and_resends(worker: WorkerProcess, qtbot: QtBot) -> None:
    results: list[Any] = []
    with qtbot.waitSignal(worker.exited, timeout=10000):
        thread = call_in_thread(worker, "crash_once", results)
    thread.join(10)
    assert results == [["crash_once", {"x": 1}, 2]]  # answered by the restarted worker


def test_idle_exit_is_not_a_crash(worker: WorkerProcess, qtbot: QtBot) -> None:
    results: list[Any] = []
    with qtbot.waitSignal(worker.exited, timeout=10000) as blocker:
        call_in_thread(worker, "idle_exit", results).join(10)
    assert blocker.args == [True]
    assert results == ["ok"]
    assert not worker.is_running()
    call_in_thread(worker, "echo", results).join(10)  # starts again on demand
    assert results[-1] == ["echo", {"x": 1}, 2]


def test_resolver_messages_arrive_on_the_ui_thread(worker: WorkerProcess, qtbot: QtBot) -> None:
    seen: list[tuple[str, bool]] = []
    main = threading.get_ident()
    worker.message.connect(lambda m: seen.append((m["op"], threading.get_ident() == main)))
    worker.send({"op": "resolve", "id": 1, "video_id": "v", "priority": 0})
    qtbot.waitUntil(lambda: ("error", True) in seen, timeout=10000)
    assert ("ready", True) in seen
