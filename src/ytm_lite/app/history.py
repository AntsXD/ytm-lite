"""When a song counts as played, for the account's YouTube Music history.

YouTube Music's own player reports a song once it starts; plays reported here feed the
history page, recommendations and the yearly Recap. A song counts after 10 s of actual
playback (pauses and buffering don't count), once per queue entry: playing the same song
again later is another play, a repeat-one loop isn't.
"""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal

from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackController, PlaybackState

PLAYED_MS = 10_000


class HistoryReporter(QObject):
    played = Signal(str)  # video id

    def __init__(
        self,
        controller: PlaybackController,
        parent: QObject | None = None,
        *,
        played_ms: int = PLAYED_MS,
    ) -> None:
        super().__init__(parent)
        self._played_ms = played_ms
        self._uid: int | None = None
        self._video_id: str | None = None
        self._remaining = played_ms
        self._reported = False
        self._playing = False
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._counted)
        controller.current_changed.connect(self._current_changed)
        controller.state_changed.connect(self._state_changed)

    def _current_changed(self, entry: QueueEntry | None) -> None:
        uid = entry.uid if entry is not None else None
        if uid == self._uid:
            return  # the same entry, e.g. after a queue edit
        self._stop_clock()
        self._uid = uid
        self._video_id = entry.track.video_id if entry is not None else None
        self._remaining = self._played_ms
        self._reported = False
        if self._playing:
            self._start_clock()

    def _state_changed(self, state: str) -> None:
        playing = state == PlaybackState.PLAYING.value
        if playing == self._playing:
            return
        self._playing = playing
        if playing:
            self._start_clock()
        else:
            self._stop_clock()

    def _start_clock(self) -> None:
        if self._video_id is None or self._reported:
            return
        self._clock.start()
        self._timer.start(self._remaining)

    def _stop_clock(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._remaining = max(0, self._remaining - int(self._clock.elapsed()))

    def _counted(self) -> None:
        self._reported = True
        if self._video_id is not None:
            self.played.emit(self._video_id)
