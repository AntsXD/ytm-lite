from __future__ import annotations

import threading
import time

from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ytm_lite.app.workers import Channel, Workers


def test_results_arrive_on_the_ui_thread(qtbot: QtBot, qapp: QApplication) -> None:
    workers = Workers()
    seen: list[tuple[int, bool]] = []
    main = threading.get_ident()

    def work() -> int:
        assert threading.get_ident() != main
        return 42

    workers.submit(work, on_done=lambda v: seen.append((v, threading.get_ident() == main)))
    qtbot.waitUntil(lambda: bool(seen), timeout=3000)
    assert seen == [(42, True)]
    workers.shutdown()


def test_errors_go_to_on_error(qtbot: QtBot, qapp: QApplication) -> None:
    workers = Workers()
    errors: list[Exception] = []

    def fail() -> None:
        raise ValueError("nope")

    workers.submit(fail, on_done=lambda _v: errors.append(RuntimeError()), on_error=errors.append)
    qtbot.waitUntil(lambda: bool(errors), timeout=3000)
    assert isinstance(errors[0], ValueError)
    workers.shutdown()


def test_cancelled_and_superseded_results_are_dropped(qtbot: QtBot, qapp: QApplication) -> None:
    workers = Workers()
    channel = Channel(workers)
    results: list[str] = []

    def slow(name: str, delay: float) -> str:
        time.sleep(delay)
        return name

    channel.submit(lambda: slow("old", 0.2), on_done=results.append)
    channel.submit(lambda: slow("new", 0.05), on_done=results.append)
    task = workers.submit(lambda: slow("cancelled", 0.05), on_done=results.append)
    task.cancel()
    qtbot.waitUntil(lambda: "new" in results, timeout=3000)
    qtbot.wait(300)  # give the old and cancelled tasks time to finish
    assert results == ["new"]
    workers.shutdown()
