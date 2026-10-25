from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from ytm_lite.app.api import Api
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import Track
from ytm_lite.app.worker_client import WorkerProcess
from ytm_lite.app.workers import Workers
from ytm_lite.ui.track_table import TRACK_ROLE, Column, TrackTableModel


def tracks(*ids: str) -> list[Track]:
    return [Track(i, i.upper(), duration=65) for i in ids]


def make_model() -> tuple[TrackTableModel, LikedIndex]:
    liked = LikedIndex(Api(WorkerProcess(Path("."))), Workers())  # worker never started
    return TrackTableModel(liked), liked


def test_data_roles(qapp: QApplication) -> None:
    model, _ = make_model()
    model.set_tracks([Track("a", "Song", duration=65), Track("b", "Gone", available=False)])
    assert model.rowCount() == 2 and model.columnCount() == len(Column)
    assert model.data(model.index(0, Column.INDEX)) == "1"
    assert model.data(model.index(0, Column.TITLE)) == "Song"
    assert model.data(model.index(0, Column.DURATION)) == "1:05"
    assert model.data(model.index(1, Column.TITLE), TRACK_ROLE).video_id == "b"
    assert model.data(model.index(1, Column.TITLE), Qt.ItemDataRole.ForegroundRole) is not None
    assert model.headerData(Column.TITLE, Qt.Orientation.Horizontal) == "Title"


def test_growing_list_appends_instead_of_resetting(qapp: QApplication) -> None:
    model, _ = make_model()
    events: list[str] = []
    model.modelReset.connect(lambda: events.append("reset"))
    model.rowsInserted.connect(lambda *_: events.append("insert"))
    model.set_tracks(tracks("a", "b"))
    model.set_tracks(tracks("a", "b", "c", "d"))  # first page, then the full playlist
    model.set_tracks(tracks("a", "b", "c", "d"))  # unchanged
    model.set_tracks(tracks("x", "b"))  # different list
    assert events == ["reset", "insert", "reset"]
    assert model.rowCount() == 2


def test_like_changes_update_heart_cells(qapp: QApplication) -> None:
    model, liked = make_model()
    model.set_tracks(tracks("a", "b", "a"))
    changed: list[tuple[int, int]] = []

    def on_changed(top: QModelIndex, bottom: QModelIndex, *_: Any) -> None:
        changed.append((top.row(), top.column()))

    model.dataChanged.connect(on_changed)
    liked._mark("a", True)
    assert changed == [(0, Column.LIKED), (2, Column.LIKED)]
    changed.clear()
    liked.replace(["b"])
    assert changed == [(0, Column.LIKED)]  # one range covering every row
