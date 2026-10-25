"""The saved playback session: the queue, your place in it, and the window layout.

Two files in the config folder, so the big one is rarely rewritten:
- queue.json: the queue's tracks, written only when entries or their order change;
- state.json: cursor, position, modes, volume and window layout; small, written often.

state.json also names the current track, so a queue.json from another moment (a crash
between the two writes) can't put you on the wrong song.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QTimer

from ytm_lite.app import codec
from ytm_lite.app.config import Paths, atomic_write_text, read_json
from ytm_lite.app.models import Track
from ytm_lite.app.play_queue import QueueEntry, RepeatMode
from ytm_lite.app.playback import PlaybackController, PlaybackState
from ytm_lite.app.workers import Workers

log = logging.getLogger(__name__)

VERSION = 1
CONTEXT = "context"

QueueData = tuple[Sequence[QueueEntry], Sequence[int], bool]  # entries, original uids, shuffled


@dataclass(slots=True)
class SavedState:
    cursor: int = -1
    video_id: str | None = None  # the track at `cursor` when this was written
    position: float = 0.0
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF
    volume: float | None = None
    window: dict[str, Any] = field(default_factory=dict)  # opaque, from the main window


@dataclass(slots=True)
class SavedQueue:
    entries: list[tuple[Track, str]]  # (track, origin) in play order
    original: list[int] | None  # play-order indexes in unshuffled order, when shuffled


def state_to_json(state: SavedState) -> dict[str, Any]:
    return {
        "v": VERSION,
        "cursor": state.cursor,
        "video_id": state.video_id,
        "position": round(state.position, 1),
        "shuffle": state.shuffle,
        "repeat": state.repeat.value,
        "volume": state.volume,
        "window": state.window,
    }


def state_from_json(raw: Any) -> SavedState:
    if not isinstance(raw, dict) or raw.get("v") != VERSION:
        return SavedState()
    try:
        repeat = RepeatMode(raw.get("repeat", "off"))
    except ValueError:
        repeat = RepeatMode.OFF
    volume = raw.get("volume")
    window = raw.get("window")
    video_id = raw.get("video_id")
    return SavedState(
        cursor=int(raw.get("cursor", -1)),
        video_id=video_id if isinstance(video_id, str) else None,
        position=max(0.0, float(raw.get("position") or 0.0)),
        shuffle=bool(raw.get("shuffle")),
        repeat=repeat,
        volume=float(volume) if isinstance(volume, int | float) else None,
        window=window if isinstance(window, dict) else {},
    )


def queue_to_json(
    entries: Sequence[QueueEntry], original_uids: Sequence[int], shuffled: bool
) -> dict[str, Any]:
    tracks = []
    for entry in entries:
        item = codec.track_to_json(entry.track)
        if entry.origin != CONTEXT:
            item["o"] = entry.origin
        tracks.append(item)
    original = None
    if shuffled:
        position = {entry.uid: i for i, entry in enumerate(entries)}
        original = [position[uid] for uid in original_uids if uid in position]
    return {"v": VERSION, "tracks": tracks, "original": original}


def queue_from_json(raw: Any) -> SavedQueue | None:
    if not isinstance(raw, dict) or raw.get("v") != VERSION:
        return None
    try:
        entries = [
            (codec.track_from_json(item), str(item.get("o") or CONTEXT))
            for item in raw.get("tracks") or ()
        ]
    except (KeyError, TypeError, IndexError):
        log.warning("queue.json is damaged; starting with an empty queue")
        return None
    original = raw.get("original")
    if not isinstance(original, list) or not all(isinstance(i, int) for i in original):
        original = None
    return SavedQueue(entries, original)


class StateKeeper(QObject):
    """Writes the session after changes (debounced), every 30 s while playing, and on exit."""

    SAVE_DELAY_MS = 2000
    PLAYING_INTERVAL_MS = 30_000

    def __init__(
        self,
        paths: Paths,
        controller: PlaybackController,
        workers: Workers,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state_file = paths.state_file
        self._queue_file = paths.config / "queue.json"
        self._controller = controller
        self._workers = workers
        self._queue_version: int | None = None  # the queue version last written
        self._window: dict[str, Any] = {}  # the main window's layout as last seen
        self.window_state: Callable[[], dict[str, Any]] | None = None  # set by the window
        self.read_only = False  # testing runs mustn't replace your saved session

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.SAVE_DELAY_MS)
        self._debounce.timeout.connect(self.save)
        self._periodic = QTimer(self)
        self._periodic.setInterval(self.PLAYING_INTERVAL_MS)
        self._periodic.timeout.connect(self.save)
        controller.queue_changed.connect(self.schedule)
        controller.current_changed.connect(self._current_changed)
        controller.modes_changed.connect(self.schedule)
        controller.state_changed.connect(self._state_changed)

    # -- loading ------------------------------------------------------------------------

    def load_state(self) -> SavedState:
        """state.json (small; read before the window is shown, for its layout)."""
        state = state_from_json(read_json(self._state_file))
        self._window = dict(state.window)
        return state

    def load_queue(self) -> SavedQueue | None:
        """queue.json; can be large, so read it off the UI thread."""
        return queue_from_json(read_json(self._queue_file))

    def restore(self, state: SavedState, queue: SavedQueue | None) -> bool:
        """Put the saved queue back, paused where it was. False if there was none."""
        if queue is None or not queue.entries:
            return False
        entries, cursor, position = queue.entries, state.cursor, state.position
        in_range = 0 <= cursor < len(entries)
        if state.video_id is not None and (
            not in_range or entries[cursor][0].video_id != state.video_id
        ):
            # The two files are from different moments: find the track, start it over.
            cursor = next(
                (i for i, (t, _) in enumerate(entries) if t.video_id == state.video_id), 0
            )
            position = 0.0
        self._controller.restore(
            entries, cursor, queue.original, state.shuffle, state.repeat, position
        )
        self._queue_version = self._controller.queue.version
        log.info("restored a queue of %d tracks", len(entries))
        return True

    # -- saving -------------------------------------------------------------------------

    def schedule(self) -> None:
        self._debounce.start()

    def _current_changed(self, _entry: object) -> None:
        self.schedule()

    def _state_changed(self, state: str) -> None:
        if state == PlaybackState.PLAYING.value:
            if not self._periodic.isActive():
                self._periodic.start()
        else:
            self._periodic.stop()
            self.schedule()  # remember where a pause left off

    def snapshot(self) -> tuple[SavedState, QueueData | None]:
        """The state to write, plus the queue (as immutable tuples) if it changed."""
        controller = self._controller
        queue = controller.queue
        entry = queue.current()
        if self.window_state is not None:
            self._window = self.window_state()
        state = SavedState(
            cursor=queue.cursor,
            video_id=entry.track.video_id if entry is not None else None,
            position=controller.position() or 0.0,
            shuffle=queue.shuffled,
            repeat=queue.repeat,
            volume=controller.player.volume(),
            window=dict(self._window),
        )
        queue_data = None
        if queue.version != self._queue_version:
            self._queue_version = queue.version
            queue_data = (queue.items, queue.original_order, queue.shuffled)
        return state, queue_data

    def _write(self, state: SavedState, queue_data: QueueData | None) -> None:
        if queue_data is not None:  # first: state.json is checked against it on load
            text = json.dumps(queue_to_json(*queue_data), ensure_ascii=False, separators=(",", ":"))
            atomic_write_text(self._queue_file, text)
        atomic_write_text(self._state_file, json.dumps(state_to_json(state), indent=1))

    def save(self) -> None:
        """Write on the io thread."""
        self._debounce.stop()
        if self.read_only:
            return
        state, queue_data = self.snapshot()
        self._workers.submit(
            lambda: self._write(state, queue_data), pool="io", on_error=self._failed
        )

    def save_now(self, snapshot: tuple[SavedState, QueueData | None] | None = None) -> None:
        """Write on this thread (at exit, once the io pool is done)."""
        self._debounce.stop()
        self._periodic.stop()
        if self.read_only:
            return
        state, queue_data = snapshot if snapshot is not None else self.snapshot()
        try:
            self._write(state, queue_data)
        except OSError as exc:
            self._failed(exc)

    def stop(self) -> tuple[SavedState, QueueData | None]:
        """Stop the timers and take the final snapshot (before mpv goes away)."""
        self._debounce.stop()
        self._periodic.stop()
        return self.snapshot()

    @staticmethod
    def _failed(error: Exception) -> None:
        log.warning("couldn't save the playback session: %s", error)
