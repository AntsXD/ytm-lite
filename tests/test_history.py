"""When a play counts for the account's history: after enough actual playback, once per
queue entry. The controller's signals are emitted directly, with a short threshold."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from test_playback import FakePlayer, FakeResolver
from ytm_lite.app.history import HistoryReporter
from ytm_lite.app.models import Track
from ytm_lite.app.play_queue import PlayQueue, QueueEntry
from ytm_lite.app.playback import PlaybackController

PLAYED_MS = 300  # with room for a slow machine around each wait
Setup = tuple[PlaybackController, HistoryReporter, list[str]]


@pytest.fixture
def setup(qapp: QApplication, tmp_path: Path) -> Setup:
    controller = PlaybackController(PlayQueue(), FakeResolver(tmp_path), FakePlayer())
    reporter = HistoryReporter(controller, played_ms=PLAYED_MS)
    played: list[str] = []
    reporter.played.connect(played.append)
    return controller, reporter, played


def entry(uid: int, video_id: str) -> QueueEntry:
    return QueueEntry(uid, Track(video_id, video_id.upper()))


def test_a_song_counts_after_enough_playback(setup: Setup, qtbot: QtBot) -> None:
    controller, _, played = setup
    controller.current_changed.emit(entry(1, "a"))
    controller.state_changed.emit("playing")
    qtbot.waitUntil(lambda: played == ["a"], timeout=2000)
    qtbot.wait(PLAYED_MS * 2)
    assert played == ["a"]  # once
    controller.current_changed.emit(entry(1, "a"))  # the same entry again (a queue edit)
    qtbot.wait(PLAYED_MS * 2)
    assert played == ["a"]


def test_pauses_and_buffering_dont_count(setup: Setup, qtbot: QtBot) -> None:
    controller, _, played = setup
    controller.current_changed.emit(entry(1, "a"))
    controller.state_changed.emit("playing")
    qtbot.wait(PLAYED_MS // 2)
    controller.state_changed.emit("paused")
    qtbot.wait(PLAYED_MS * 2)
    assert played == []
    controller.state_changed.emit("loading")
    qtbot.wait(PLAYED_MS)
    assert played == []
    controller.state_changed.emit("playing")  # the rest of the time
    qtbot.waitUntil(lambda: played == ["a"], timeout=2000)


def test_skipped_songs_dont_count_and_each_entry_is_a_play(setup: Setup, qtbot: QtBot) -> None:
    controller, _, played = setup
    controller.state_changed.emit("playing")
    controller.current_changed.emit(entry(1, "a"))
    qtbot.wait(PLAYED_MS // 3)
    controller.current_changed.emit(entry(2, "b"))  # skipped "a" (gapless: still playing)
    qtbot.waitUntil(lambda: played == ["b"], timeout=2000)
    controller.current_changed.emit(entry(3, "b"))  # "b" again, later in the queue
    qtbot.waitUntil(lambda: played == ["b", "b"], timeout=2000)


def test_a_restored_paused_song_isnt_a_play(setup: Setup, qtbot: QtBot) -> None:
    controller, _, played = setup
    controller.current_changed.emit(entry(1, "a"))
    controller.state_changed.emit("paused")
    qtbot.wait(PLAYED_MS * 2)
    controller.current_changed.emit(None)
    controller.state_changed.emit("playing")
    qtbot.wait(PLAYED_MS * 2)
    assert played == []
