"""Saving and restoring the playback session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from ytm_lite.app.config import Paths
from ytm_lite.app.models import ArtistRef, Track
from ytm_lite.app.play_queue import PlayQueue, RepeatMode
from ytm_lite.app.playback import PlaybackController, PlaybackState
from ytm_lite.app.player import Player
from ytm_lite.app.resolver import ResolverClient
from ytm_lite.app.state import (
    SavedState,
    StateKeeper,
    queue_from_json,
    queue_to_json,
    state_from_json,
    state_to_json,
)
from ytm_lite.app.worker_client import WorkerProcess
from ytm_lite.app.workers import Workers


def track(video_id: str) -> Track:
    return Track(video_id, video_id.upper(), (ArtistRef("Artist", "UC1"),), duration=200)


@pytest.fixture
def keeper(qapp: QApplication, tmp_path: Path) -> StateKeeper:
    paths = Paths(config=tmp_path / "config", cache=tmp_path / "cache", logs=tmp_path / "logs")
    paths.config.mkdir()
    # Nothing here starts mpv or the worker: restore() leaves playback paused.
    controller = PlaybackController(
        PlayQueue(), ResolverClient(WorkerProcess(paths.cache)), Player()
    )
    return StateKeeper(paths, controller, Workers())


def test_queue_json_roundtrip_keeps_origins_and_original_order() -> None:
    queue = PlayQueue()
    queue.set([track(i) for i in "abcde"])
    queue.add([track("x")], origin="user")
    queue.extend_radio([track("r")])
    queue.set_shuffle(True)
    data = json.loads(json.dumps(queue_to_json(queue.items, queue.original_order, True)))
    saved = queue_from_json(data)
    assert saved is not None
    assert [t.video_id for t, _ in saved.entries] == [e.track.video_id for e in queue.items]
    assert [o for _, o in saved.entries] == [e.origin for e in queue.items]
    assert saved.entries[0][0] == queue.items[0].track

    restored = PlayQueue()
    restored.restore(saved.entries, 0, saved.original, True, RepeatMode.OFF)
    restored.set_shuffle(False)
    assert [e.track.video_id for e in restored.items] == list("abcdexr")


def test_unshuffled_queue_saves_no_original_order() -> None:
    queue = PlayQueue()
    queue.set([track("a")])
    assert queue_to_json(queue.items, queue.original_order, False)["original"] is None


def test_damaged_files_are_ignored() -> None:
    assert queue_from_json({"v": 1, "tracks": [{"t": "no id"}]}) is None
    assert queue_from_json({"v": 99}) is None
    assert queue_from_json(None) is None
    assert state_from_json({"v": 1, "repeat": "sideways"}).repeat is RepeatMode.OFF
    assert state_from_json("garbage") == SavedState()


def test_state_json_roundtrip() -> None:
    state = SavedState(3, "abc", 61.25, True, RepeatMode.ONE, 55.0, {"queue": True})
    assert state_from_json(json.loads(json.dumps(state_to_json(state)))) == SavedState(
        3, "abc", 61.2, True, RepeatMode.ONE, 55.0, {"queue": True}
    )


def test_save_and_restore(keeper: StateKeeper, tmp_path: Path) -> None:
    controller = keeper._controller
    entries = [(track(i), "context") for i in "abc"]
    controller.restore(entries, 1, None, False, RepeatMode.ALL, 42.0)
    controller.set_shuffle(True)
    keeper.window_state = lambda: {"queue": True}
    keeper.save_now()
    assert (tmp_path / "config" / "queue.json").exists()

    controller.queue.set([track("z")])  # something else entirely
    state = keeper.load_state()
    assert state.window == {"queue": True} and state.shuffle
    assert keeper.restore(state, keeper.load_queue())
    current = controller.queue.current()
    assert current is not None and current.track.video_id == "b"
    assert controller.position() == 42.0
    assert controller.state is PlaybackState.PAUSED
    assert controller.queue.repeat is RepeatMode.ALL and controller.queue.shuffled


def test_queue_file_is_only_rewritten_when_the_queue_changes(
    keeper: StateKeeper, tmp_path: Path
) -> None:
    controller = keeper._controller
    controller.restore([(track(i), "context") for i in "abc"], 0, None, False, RepeatMode.OFF, 0)
    keeper._queue_version = None
    _state, queue_data = keeper.snapshot()
    assert queue_data is not None
    _state, queue_data = keeper.snapshot()
    assert queue_data is None  # unchanged since
    controller.queue.advance()
    assert keeper.snapshot()[1] is None  # the cursor lives in state.json
    controller.add_to_queue([track("d")])
    assert keeper.snapshot()[1] is not None


def test_restore_finds_the_track_when_the_files_disagree(keeper: StateKeeper) -> None:
    saved_queue = queue_from_json(
        {"v": 1, "tracks": [{"v": i, "t": i} for i in "abc"], "original": None}
    )
    state = SavedState(cursor=0, video_id="c", position=99.0)
    assert keeper.restore(state, saved_queue)
    current = keeper._controller.queue.current()
    assert current is not None and current.track.video_id == "c"
    assert keeper._controller.position() == 0.0


def test_nothing_to_restore(keeper: StateKeeper) -> None:
    assert not keeper.restore(SavedState(), None)
    assert not keeper.restore(SavedState(), queue_from_json({"v": 1, "tracks": []}))
    assert keeper._controller.state is PlaybackState.STOPPED


def test_read_only_keeper_writes_nothing(keeper: StateKeeper, tmp_path: Path) -> None:
    keeper._controller.restore([(track("a"), "context")], 0, None, False, RepeatMode.OFF, 5.0)
    keeper.read_only = True
    keeper.save()
    keeper.save_now()
    assert not (tmp_path / "config" / "state.json").exists()
    assert not (tmp_path / "config" / "queue.json").exists()
