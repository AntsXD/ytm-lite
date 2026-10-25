"""Playback controller: keeps mpv's playlist in line with the play queue.

mpv's playlist is a two-slot window, [current, armed], where "armed" is the next queue
entry already handed to mpv so the change is gapless. `reconcile()` is idempotent and
runs after every relevant event: if the armed entry is no longer what the queue says
comes next, it is dropped, and the right one is resolved and armed instead.

`start-file` events are the source of truth for what is actually playing; each mpv
playlist entry id is mapped back to the queue entry it was created for.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from ytm_lite.app.models import Track
from ytm_lite.app.play_queue import PlayQueue, QueueEntry, RepeatMode
from ytm_lite.app.player import Player
from ytm_lite.app.resolver import ResolverClient, StreamInfo
from ytm_resolver.protocol import ErrorKind, Priority

log = logging.getLogger(__name__)


class PlaybackState(StrEnum):
    STOPPED = "stopped"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"


class PlaybackController(QObject):
    current_changed = Signal(object)  # QueueEntry | None
    state_changed = Signal(str)  # PlaybackState value
    stream_changed = Signal(object)  # StreamInfo | None for the current entry
    notice = Signal(str)  # short message for the user
    extraction_failing = Signal()  # repeated extraction errors: yt-dlp likely needs an update
    transition_measured = Signal(float)  # ms from one track ending to the next playing

    RELEASE_AFTER_MS = 10 * 60 * 1000  # destroy mpv after this long stopped or paused
    MAX_EXTRACTION_FAILURES = 3
    RESTART_THRESHOLD = 3.0  # Previous restarts the track when past this many seconds

    def __init__(
        self,
        queue: PlayQueue,
        resolver: ResolverClient,
        player: Player,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.queue = queue
        self.resolver = resolver
        self.player = player
        self._state = PlaybackState.STOPPED
        self._loaded: dict[int, int] = {}  # mpv playlist entry id -> queue uid
        self._playing_uid: int | None = None  # what mpv reported in its last start-file
        self._armed: tuple[int, int] | None = None  # (uid, entry id) of the prefetched next
        self._waiting_uid: int | None = None  # being resolved to play now
        self._resume_at: float | None = None
        self._retried: set[int] = set()  # uids already re-resolved after a playback error
        self._prefetch_failed: set[int] = set()
        self._stream_error_uid: int | None = None
        self._extraction_failures = 0
        self._last_pos = 0.0
        self._last_pos_at = 0.0
        self._ended_at: float | None = None

        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.setInterval(self.RELEASE_AFTER_MS)
        self._release_timer.timeout.connect(self._release_player)
        self._start_timer = QTimer(self)  # waits out available_at for the track to play now
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._start_waiting)
        self._arm_timer = QTimer(self)  # waits out available_at for the armed track
        self._arm_timer.setSingleShot(True)
        self._arm_timer.timeout.connect(self.reconcile)

        resolver.resolved.connect(self._on_resolved)
        resolver.failed.connect(self._on_failed)
        player.file_started.connect(self._on_file_started)
        player.file_ended.connect(self._on_file_ended)
        player.playback_restarted.connect(self._on_playback_restarted)
        player.pause_changed.connect(self._on_pause_changed)
        player.idle_changed.connect(self._on_idle_changed)
        player.stream_error.connect(self._on_stream_error)
        player.released.connect(self._on_released)

    # -- public API -------------------------------------------------------------------

    @property
    def state(self) -> PlaybackState:
        return self._state

    def play_tracks(self, tracks: Sequence[Track], start: int = 0) -> None:
        """Replace the queue with `tracks` and play from `start`."""
        self._play_entry(self.queue.set(tracks, start))

    def toggle_pause(self) -> None:
        if self._state is PlaybackState.PLAYING:
            self.player.set_paused(True)
        elif self._state is PlaybackState.PAUSED and self.player.active():
            self.player.set_paused(False)
        elif self._state in (PlaybackState.PAUSED, PlaybackState.STOPPED):
            entry = self.queue.current()
            if entry is not None:
                self._play_entry(entry, resume_at=self._last_pos or None)

    def next(self) -> None:
        if self._armed is not None and self.player.active():
            uid, _ = self._armed
            entry = self.queue.move_to(uid)
            self.current_changed.emit(entry)
            self.player.playlist_next()
            return
        entry = self.queue.advance()
        if entry is None:
            self.stop()
        else:
            self._play_entry(entry)

    def previous(self) -> None:
        if (self.position() or 0.0) > self.RESTART_THRESHOLD:
            self.seek(0.0)
            return
        entry = self.queue.previous()
        if entry is None:
            self.seek(0.0)
        else:
            self._play_entry(entry)

    def seek(self, seconds: float) -> None:
        self._last_pos = seconds
        self._last_pos_at = time.monotonic()
        if self.player.active():
            self.player.seek(seconds)

    def set_volume(self, volume: float) -> None:
        self.player.set_volume(volume)

    def set_repeat(self, mode: RepeatMode) -> None:
        self.queue.repeat = mode
        self.player.set_loop_file(mode is RepeatMode.ONE)
        self.reconcile()

    def position(self) -> float | None:
        """Current position; also remembered for resuming after errors or a release."""
        pos = self.player.position() if self.player.active() else None
        if pos is not None:
            self._last_pos = pos
            self._last_pos_at = time.monotonic()
            return pos
        return self._last_pos if self.queue.current() is not None else None

    def duration(self) -> float | None:
        duration = self.player.duration() if self.player.active() else None
        if duration is None:
            entry = self.queue.current()
            if entry is not None and entry.track.duration:
                return float(entry.track.duration)
        return duration

    def stop(self) -> None:
        self._start_timer.stop()
        self._arm_timer.stop()
        self._waiting_uid = None
        self._armed = None
        self._loaded.clear()
        self._playing_uid = None
        if self.player.active():
            self.player.stop()
        self._set_state(PlaybackState.STOPPED)
        self._release_timer.start()

    def shutdown(self) -> None:
        for timer in (self._release_timer, self._start_timer, self._arm_timer):
            timer.stop()
        self.player.release()

    # -- starting playback --------------------------------------------------------------

    def _play_entry(self, entry: QueueEntry | None, resume_at: float | None = None) -> None:
        self._start_timer.stop()
        self._arm_timer.stop()
        self._armed = None
        self._ended_at = None
        if entry is None:
            self.stop()
            return
        if self.player.active():
            self.player.stop()  # stop the old track at once while the new one resolves
        self._loaded.clear()
        self._playing_uid = None
        self._waiting_uid = entry.uid
        self._resume_at = resume_at
        self._last_pos = resume_at or 0.0
        self._release_timer.stop()
        self._set_state(PlaybackState.LOADING)
        self.current_changed.emit(entry)
        self.stream_changed.emit(None)
        video_id = entry.track.video_id
        if self.resolver.cached(video_id, valid_for=entry.track.duration or 0) is not None:
            self._start_waiting()
        else:
            self.resolver.request(video_id, Priority.NOW)

    def _start_waiting(self) -> None:
        """Load the entry we're waiting on, once its stream is resolved and usable."""
        entry = self.queue.entry(self._waiting_uid) if self._waiting_uid is not None else None
        if entry is None:
            return
        stream = self.resolver.cached(entry.track.video_id)
        if stream is None:
            self.resolver.request(entry.track.video_id, Priority.NOW)
            return
        wait = stream.wait_seconds()
        if wait > 0:
            log.info("stream for %s usable in %.1fs; waiting", entry.track.video_id, wait)
            self._start_timer.start(int(wait * 1000) + 100)
            return
        entry_id = self.player.load(
            stream.url, append=False, options=self._file_options(stream, self._resume_at)
        )
        self.player.set_loop_file(self.queue.repeat is RepeatMode.ONE)
        self._loaded = {entry_id: entry.uid}
        self._waiting_uid = None
        self._resume_at = None
        self.stream_changed.emit(stream)

    @staticmethod
    def _file_options(stream: StreamInfo, start: float | None = None) -> dict[str, str]:
        options: dict[str, str] = {}
        user_agent = stream.http_headers.get("User-Agent")
        if user_agent:
            options["user-agent"] = user_agent
        referer = stream.http_headers.get("Referer")
        if referer:
            options["referrer"] = referer
        if start:
            options["start"] = f"{start:.3f}"
        return options

    # -- keeping the next track armed ------------------------------------------------------

    def reconcile(self) -> None:
        """Make mpv's armed entry match the queue's next entry (idempotent)."""
        if not self.player.active() or self._playing_uid is None or self._waiting_uid is not None:
            return
        want = self.queue.next_uid()
        if self._armed is not None and self._armed[0] != want:
            self.player.playlist_clear()
            self._loaded.pop(self._armed[1], None)
            self._armed = None
        if want is None or self._armed is not None or want in self._prefetch_failed:
            return
        entry = self.queue.entry(want)
        if entry is None:
            return
        remaining = max(0.0, (self.duration() or 0.0) - (self.position() or 0.0))
        stream = self.resolver.cached(
            entry.track.video_id, valid_for=remaining + (entry.track.duration or 0)
        )
        if stream is None:
            self.resolver.request(entry.track.video_id, Priority.PREFETCH)
            return
        wait = stream.wait_seconds()
        if wait > 0:
            self._arm_timer.start(int(wait * 1000) + 100)
            return
        entry_id = self.player.load(stream.url, append=True, options=self._file_options(stream))
        self._armed = (want, entry_id)
        self._loaded[entry_id] = want
        log.info("armed next track %s", entry.track.video_id)

    # -- resolver events ---------------------------------------------------------------

    def _on_resolved(self, video_id: str, _stream: StreamInfo) -> None:
        waiting = self.queue.entry(self._waiting_uid) if self._waiting_uid is not None else None
        if waiting is not None and waiting.track.video_id == video_id:
            self._extraction_failures = 0
            self._start_waiting()
        else:
            self.reconcile()

    def _on_failed(self, video_id: str, kind: str, message: str) -> None:
        waiting = self.queue.entry(self._waiting_uid) if self._waiting_uid is not None else None
        if waiting is None or waiting.track.video_id != video_id:
            # A prefetch failed; try again only when that track is up to play.
            for item in self.queue.items:
                if item.track.video_id == video_id:
                    self._prefetch_failed.add(item.uid)
            return
        self._waiting_uid = None
        if kind == ErrorKind.EXTRACTION.value:
            self._extraction_failures += 1
            if self._extraction_failures >= self.MAX_EXTRACTION_FAILURES:
                self.stop()
                self.extraction_failing.emit()
                return
        if kind == ErrorKind.NO_JS_RUNTIME.value:
            self.stop()
            self.notice.emit("No JavaScript runtime found; install Deno or Node to play this")
            return
        reason = "unavailable" if kind == ErrorKind.UNAVAILABLE.value else "couldn't load it"
        self.notice.emit(f"Skipped “{waiting.track.title}”: {reason}")
        self._skip_after_failure()

    def _skip_after_failure(self) -> None:
        entry = self.queue.advance()
        if entry is None:
            self.stop()
        else:
            self._play_entry(entry)

    # -- player events -----------------------------------------------------------------

    def _on_file_started(self, entry_id: int) -> None:
        uid = self._loaded.get(entry_id)
        if uid is None:
            return
        entry = self.queue.entry(uid)
        if entry is None:  # removed from the queue while it was armed
            self._play_entry(self.queue.current())
            return
        self._playing_uid = uid
        self._stream_error_uid = None
        self._prefetch_failed.discard(uid)
        if self._armed is not None and self._armed[1] == entry_id:
            self._armed = None
        current = self.queue.current()
        if current is None or current.uid != uid:
            self.queue.move_to(uid)
            self.current_changed.emit(entry)
        self.player.playlist_clear()  # drop the finished entry; keeps this one
        self._loaded = {entry_id: uid}
        self.stream_changed.emit(self.resolver.cached(entry.track.video_id))
        self._release_timer.stop()
        self.reconcile()

    def _on_file_ended(self, entry_id: int, reason: str, error: str) -> None:
        uid = self._loaded.get(entry_id)
        if uid is None:
            return
        if reason == "eof" and self._stream_error_uid == uid:
            reason, error = "error", "stream interrupted"
        if reason == "error":
            self._loaded.pop(entry_id, None)
            self._handle_playback_error(uid, error)
            return
        if reason != "eof" or uid != self._playing_uid:
            return
        self._ended_at = time.monotonic()
        if self._armed is None:
            # Nothing armed in time (or the queue ended): continue the slow way.
            self._playing_uid = None
            next_entry = self.queue.advance()
            if next_entry is None:
                self._set_state(PlaybackState.STOPPED)
                self._release_timer.start()
            else:
                self._play_entry(next_entry)

    def _handle_playback_error(self, uid: int, error: str) -> None:
        entry = self.queue.entry(uid)
        if entry is None:
            return
        self.resolver.invalidate(entry.track.video_id)
        if self._armed is not None and self._armed[0] == uid:
            # The prefetched track failed to open; re-resolve it once.
            self._armed = None
            if uid in self._retried:
                self._prefetch_failed.add(uid)
            self._retried.add(uid)
            self.reconcile()
            return
        log.warning("playback error on %s: %s", entry.track.video_id, error)
        if uid not in self._retried:
            self._retried.add(uid)
            self._play_entry(entry, resume_at=self._last_pos or None)
            return
        self.notice.emit(f"Skipped “{entry.track.title}”: playback failed")
        self._skip_after_failure()

    def _on_playback_restarted(self) -> None:
        if self._ended_at is not None:
            gap_ms = (time.monotonic() - self._ended_at) * 1000
            self._ended_at = None
            log.info("track transition: %.0f ms", gap_ms)
            self.transition_measured.emit(gap_ms)
        if self._playing_uid is not None:
            self._set_state(PlaybackState.PAUSED if self.player.paused() else PlaybackState.PLAYING)

    def _on_pause_changed(self, paused: bool) -> None:
        if self._playing_uid is None:
            return
        self._set_state(PlaybackState.PAUSED if paused else PlaybackState.PLAYING)
        if paused:
            self._release_timer.start()
        else:
            self._release_timer.stop()

    def _on_idle_changed(self, idle: bool) -> None:
        if idle and self._waiting_uid is None and self._playing_uid is None:
            self._set_state(PlaybackState.STOPPED)
            self._release_timer.start()

    def _on_stream_error(self, _text: str) -> None:
        self._stream_error_uid = self._playing_uid

    def _release_player(self) -> None:
        if self._state in (PlaybackState.PAUSED, PlaybackState.STOPPED):
            self.position()  # remember where we were
            self.player.release()

    def _on_released(self) -> None:
        self._loaded.clear()
        self._armed = None
        self._playing_uid = None
        if self.queue.current() is not None and self._state is not PlaybackState.STOPPED:
            self._set_state(PlaybackState.PAUSED)

    def _set_state(self, state: PlaybackState) -> None:
        if state is not self._state:
            self._state = state
            self.state_changed.emit(state.value)
