"""The queue panel's model: mirrors the queue, applies drags through the controller."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.models import Track
from ytm_lite.app.play_queue import PlayQueue, RepeatMode
from ytm_lite.app.playback import PlaybackController
from ytm_lite.app.player import Player
from ytm_lite.app.resolver import ResolverClient
from ytm_lite.app.worker_client import WorkerProcess
from ytm_lite.ui.queue_panel import ENTRY_ROLE, MIME_TYPE, QueueModel, QueuePanel


@pytest.fixture
def controller(qapp: QApplication, tmp_path: Path) -> PlaybackController:
    controller = PlaybackController(PlayQueue(), ResolverClient(WorkerProcess(tmp_path)), Player())
    controller.radio = False
    entries = [(Track(i, i.upper(), duration=60), "context") for i in "abcde"]
    controller.restore(entries, 1, None, False, RepeatMode.OFF, 0.0)  # paused on "b"
    return controller


def titles(model: QueueModel) -> list[str]:
    return [model.data(model.index(row)) for row in range(model.rowCount())]


def test_model_mirrors_the_queue(controller: PlaybackController) -> None:
    model = QueueModel(controller)
    assert titles(model) == ["A", "B", "C", "D", "E"]
    assert model.cursor == 1
    entry = model.data(model.index(2), ENTRY_ROLE)
    assert entry is controller.queue.items[2]
    controller.add_to_queue([Track("x", "X")])
    assert titles(model)[-1] == "X"


def test_drag_moves_through_the_controller(controller: PlaybackController) -> None:
    model = QueueModel(controller)
    resets: list[bool] = []
    model.modelReset.connect(lambda: resets.append(True))
    # Drag "E" (row 4) to between "A" and "B" (QListView passes the row before the move).
    assert model.moveRows(QModelIndex(), 4, 1, QModelIndex(), 1)
    assert [e.track.video_id for e in controller.queue.items] == ["a", "e", "b", "c", "d"]
    assert titles(model) == ["A", "E", "B", "C", "D"]
    assert model.cursor == 2  # still on "b"
    assert resets == []  # a move, not a reset: the view keeps selection and scroll
    # Dragging down: "A" to after "C".
    assert model.moveRows(QModelIndex(), 0, 1, QModelIndex(), 4)
    assert titles(model) == ["E", "B", "C", "A", "D"]
    # Dropping a row where it already is does nothing.
    assert not model.moveRows(QModelIndex(), 1, 1, QModelIndex(), 2)


def test_dropping_a_selection_keeps_it_together_in_order(
    controller: PlaybackController,
) -> None:
    model = QueueModel(controller)
    model.move_rows([3, 0], 5)  # "A" and "D" dropped below "E"
    assert titles(model) == ["B", "C", "E", "A", "D"]
    model.move_rows([3, 4], 0)  # and back to the top
    assert titles(model) == ["A", "D", "B", "C", "E"]
    model.move_rows([0, 4], 2)  # "A" and "E" between "D" and "B"
    assert titles(model) == ["D", "A", "E", "B", "C"]
    current = controller.queue.current()
    assert current is not None and current.track.video_id == "b"
    assert model.cursor == 3


def test_current_change_updates_the_cursor(controller: PlaybackController) -> None:
    model = QueueModel(controller)
    changed: list[tuple[int, int]] = []
    model.dataChanged.connect(lambda first, last: changed.append((first.row(), last.row())))
    entry = controller.queue.move_to(controller.queue.items[3].uid)
    controller.current_changed.emit(entry)
    assert model.cursor == 3
    assert changed == [(1, 3)]  # rows that changed between played and upcoming


def test_mime_data_lists_uids(controller: PlaybackController) -> None:
    model = QueueModel(controller)
    data = model.mimeData([model.index(0), model.index(2)])
    uids = json.loads(bytes(data.data(MIME_TYPE).data()))
    assert uids == [controller.queue.items[0].uid, controller.queue.items[2].uid]


def test_panel_summary_and_autoplay(controller: PlaybackController, tmp_path: Path) -> None:
    panel = QueuePanel(controller, ImageLoader(tmp_path))
    assert panel.summary.text() == "3 up next · 3:00"
    assert not panel.autoplay.isChecked()
    panel.autoplay.setChecked(True)
    assert controller.radio
    controller.clear_queue()
    assert panel.summary.text() == "Nothing up next"
