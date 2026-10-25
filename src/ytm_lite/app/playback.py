"""Playback controller: keeps mpv's playlist in line with the play queue.

mpv's playlist is a two-slot window, [current, armed], where "armed" is the next queue
entry already handed to mpv so the change is gapless. `reconcile()` is idempotent and
runs after every relevant event and queue edit: if the armed entry is no longer what
the queue says comes next, it is dropped, and the right one is resolved and armed.

`start-file` events are the source of truth for what is actually playing; each mpv
playlist entry id is mapped back to the queue entry and stream it was created for.

Stream quality when signed in: a track you start yourself (double-click, Next or
Previous with nothing armed, Play after mpv was released) is resolved the fast way, at
standard quality, so it starts in ~1.5 s instead of ~4 s. Everything after it is
prefetched while the current track plays, and that is always Premium.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import NamedTuple

from PySide6.QtCore import QObject, QTimer, Signal

from ytm_lite.app.models import Track
from ytm_lite.app.play_queue import PlayQueue, QueueEntry, RepeatMode
from ytm_lite.app.player import Player
from ytm_lite.app.resolver import ResolverClient, StreamInfo
from ytm_worker.protocol import ErrorKind, Priority

log = logging.getLogger(__name__)


class PlaybackState(StrEnum):
    STOPPED = "stopped"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"


class _Loaded(NamedTuple):
    uid: int  # queue entry
    stream: StreamInfo


def _queued_text(tracks: Sequence[Track], next_up: bool) -> str:
    what = f"“{tracks[0].title}”" if len(tracks) == 1 else f"{len(tracks)} songs"
    return f"{what} will play next" if next_up else f"Added {what} to the queue"


class PlaybackController(QObject):
    current_changed = Signal(object)  # QueueEntry | None
    queue_changed = Signal()  # entries were added, removed or reordered
    modes_changed = Signal()  # shuffle, repeat or radio
    state_changed = Signal(str)  # PlaybackState value
    stream_changed = Signal(object)  # StreamInfo | None for the current entry
    radio_wanted = Signal(str)  # seed video id; answer with add_radio() or radio_failed()
    notice = Signal(str)  # short message for the user
    extraction_failing = Signal()  # repeated extraction errors: yt-dlp likely needs an update
    transition_measured = Signal(float)  # ms from one track ending to the next playing

    RELEASE_AFTER_MS = 10 * 60 * 1000  # destroy mpv after this long stopped or paused
    MAX_EXTRACTION_FAILURES = 3
    RESTART_THRESHOLD = 3.0  # Previous restarts the track when past this many seconds
    RADIO_THRESHOLD = 2  # fetch radio suggestions when this few tracks are left

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
        self.radio = True  # autoplay: continue with radio suggestions when the queue runs out
        self._state = PlaybackState.STOPPED
        self._loaded: dict[int, _Loaded] = {}  # mpv playlist entry id -> what it plays
        self._playing_uid: int | None = None  # what mpv reported in its last start-file
        self._armed: tuple[int, int] | None = None  # (uid, entry id) of the prefetched next
        self._waiting_uid: int | None = None  # being resolved to play now...
        self._waiting_fast = False  # ...the fast way (then any quality will do)
        self._start_paused = False
        self._resume_at: float | None = None
        self._retried: set[int] = set()  # uids already re-resolved after a playback error
        self._prefetch_failed: set[int] = set()
        self._stream_error_uid: int | None = None
        self._extraction_failures = 0
        self._last_pos = 0.0
        self._ended_at: float | None = None
        self._finished = False  # the queue ran out
        self._radio_queue = False  # "Start radio": suggestions keep coming with autoplay off
        self._radio_seed: str | None = None  # suggestions being fetched for this seed
        self._radio_spent: set[str] = set()  # seeds that brought nothing new

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

    def play_tracks(
        self,
        tracks: Sequence[Track],
        start: int = 0,
        *,
        radio: bool = False,
        shuffle: bool | None = None,
    ) -> None:
        """Replace the queue with `tracks` and play from `start`. With `radio`, radio
        suggestions follow even when autoplay is off ("Start radio"); `shuffle` turns
        shuffle on or off first ("Shuffle play": `start` first, the rest shuffled)."""
        if shuffle is not None and shuffle != self.queue.shuffled:
            self.queue.set_shuffle(shuffle)  # the old queue is replaced right below
            self.modes_changed.emit()
        entry = self.queue.set(tracks, start)
        self._radio_queue = radio
        self._radio_seed = None
        self._radio_spent.clear()
        self._retried.clear()
        self._prefetch_failed.clear()
        self.queue_changed.emit()
        self._play_entry(entry, fast=True)
        self._maybe_top_up()

    def start_radio(self, track: Track) -> None:
        self.play_tracks([track], radio=True)

    def play_entry(self, uid: int) -> None:
        """Jump to an entry that is already in the queue."""
        entry = self.queue.move_to(uid)
        if entry is not None:
            self._play_entry(entry, fast=True)

    def toggle_pause(self) -> None:
        if self._state is PlaybackState.PLAYING:
            self.player.set_paused(True)
        elif self._state is PlaybackState.PAUSED and self.player.active():
            self.player.set_paused(False)
        elif self._state in (PlaybackState.PAUSED, PlaybackState.STOPPED):
            entry = self.queue.current()
            if self._finished and self.queue.next_index() is not None:
                entry = self.queue.advance()  # songs were added after the queue ran out
            if entry is not None:
                self._play_entry(entry, resume_at=self._last_pos or None, fast=True)

    def next(self) -> None:
        if self._armed is not None and self.player.active():
            uid, _ = self._armed
            entry = self.queue.move_to(uid)
            self.current_changed.emit(entry)
            self.player.set_paused(False)
            self.player.playlist_next()
            return
        entry = self.queue.advance()
        if entry is None:
            self.stop()
        else:
            self._play_entry(entry, fast=True)

    def previous(self) -> None:
        if (self.position() or 0.0) > self.RESTART_THRESHOLD:
            self.seek(0.0)
            return
        entry = self.queue.previous()
        if entry is None:
            self.seek(0.0)
        else:
            self._play_entry(entry, fast=True)

    def seek(self, seconds: float) -> None:
        self._last_pos = seconds
        if self.player.active():
            self.player.seek(seconds)

    def set_volume(self, volume: float) -> None:
        self.player.set_volume(volume)

    def position(self) -> float | None:
        """Current position; also remembered for resuming after errors or a release."""
        pos = self.player.position() if self.player.active() else None
        if pos is not None:
            self._last_pos = pos
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

    def shutdown(self) -> None:
        for timer in (self._release_timer, self._start_timer, self._arm_timer):
            timer.stop()
        self.player.release()

    # -- editing the queue ----------------------------------------------------------------

    def play_next(self, tracks: Sequence[Track]) -> None:
        """Insert `tracks` right after the current one."""
        self._enqueue(tracks, next_up=True)

    def add_to_queue(self, tracks: Sequence[Track]) -> None:
        self._enqueue(tracks, next_up=False)

    def _enqueue(self, tracks: Sequence[Track], *, next_up: bool) -> None:
        playable = [t for t in tracks if t.available]
        if not playable:
            return
        idle = self.queue.current() is None
        if next_up:
            self.queue.play_next(playable)
        else:
            self.queue.add(playable)
        self.queue_changed.emit()
        if idle:  # nothing was queued: start playing
            self._play_entry(self.queue.current(), fast=True)
        else:
            self.notice.emit(_queued_text(playable, next_up))
            self.reconcile()
        self._maybe_top_up()

    def move(self, uid: int, index: int) -> None:
        """Move an entry to `index` in play order (a drag in the queue panel)."""
        if self.queue.move(uid, index):
            self._queue_edited()

    def move_next(self, uid: int) -> None:
        """Move an entry to right after the current one."""
        index = self.queue.index_of(uid)
        cursor = self.queue.cursor
        if index is not None and index != cursor:
            self.move(uid, cursor + 1 if index > cursor else cursor)

    def remove(self, uids: Iterable[int]) -> None:
        active = self._state in (PlaybackState.PLAYING, PlaybackState.LOADING)
        if self.queue.remove(uids):  # the current entry went too; the next one takes over
            entry = self.queue.current()
            if active and entry is not None:
                self._play_entry(entry, fast=True)
            else:
                self.stop()
                self._last_pos = 0.0
                self.current_changed.emit(entry)
        self._queue_edited()

    def clear_queue(self) -> None:
        """Remove everything but the current entry."""
        self.queue.clear()
        self._queue_edited()

    def set_shuffle(self, on: bool) -> None:
        if on != self.queue.shuffled:
            self.queue.set_shuffle(on)
            self.modes_changed.emit()
            self._queue_edited()

    def set_repeat(self, mode: RepeatMode) -> None:
        self.queue.repeat = mode
        self.player.set_loop_file(mode is RepeatMode.ONE)
        self.modes_changed.emit()
        self.reconcile()
        self._maybe_top_up()

    def set_radio(self, on: bool) -> None:
        self.radio = on
        self.modes_changed.emit()
        self._maybe_top_up()

    def restore(
        self,
        entries: Sequence[tuple[Track, str]],
        cursor: int,
        original: Sequence[int] | None,
        shuffled: bool,
        repeat: RepeatMode,
        position: float,
    ) -> None:
        """Bring back a saved queue, paused at `position`. mpv isn't started until Play."""
        self.queue.restore(entries, cursor, original, shuffled, repeat)
        self._last_pos = max(0.0, position)
        entry = self.queue.current()
        self._set_state(PlaybackState.PAUSED if entry is not None else PlaybackState.STOPPED)
        self.queue_changed.emit()
        self.modes_changed.emit()
        self.current_changed.emit(entry)

    def preload(self) -> None:
        """Resolve the restored track ahead of Play, so it starts at once (in Premium)."""
        entry = self.queue.current()
        if entry is not None and not self.player.active() and self._waiting_uid is None:
            self.resolver.request(entry.track.video_id, Priority.PREFETCH)

    def _queue_edited(self) -> None:
        self.queue_changed.emit()
        self.reconcile()
        self._maybe_top_up()

    # -- radio ------------------------------------------------------------------------------

    def _maybe_top_up(self) -> None:
        """Ask for radio suggestions when the queue is about to run out."""
        if not (self.radio or self._radio_queue) or self._radio_seed is not None:
            return
        if self.queue.repeat is RepeatMode.ALL or self.queue.current() is None:
            return
        last = self.queue.last()
        if last is None or self.queue.remaining() > self.RADIO_THRESHOLD:
            return
        seed = last.track.video_id
        if seed in self._radio_spent:
            return
        self._radio_seed = seed
        log.info("fetching radio suggestions after %s", seed)
        self.radio_wanted.emit(seed)

    def add_radio(self, seed: str, tracks: Sequence[Track]) -> None:
        """Suggestions fetched for `radio_wanted(seed)`."""
        if seed != self._radio_seed:
            return  # the queue was replaced meanwhile
        self._radio_seed = None
        if not self.queue.extend_radio(tracks, seed):
            self._radio_spent.add(seed)
            return
        self._queue_edited()
        if self._finished and self._state is PlaybackState.STOPPED:
            self._play_entry(self.queue.advance(), fast=False)

    def radio_failed(self, seed: str) -> None:
        if seed == self._radio_seed:
            self._radio_seed = None
            self._radio_spent.add(seed)

    # -- starting playback --------------------------------------------------------------

    def _play_entry(
        self,
        entry: QueueEntry | None,
        *,
        fast: bool,
        resume_at: float | None = None,
        paused: bool = False,
    ) -> None:
        """Stop what plays and start `entry`. `fast`: someone is waiting, so take the
        quick standard-quality resolve (signed in) unless a stream is cached already."""
        self._start_timer.stop()
        self._arm_timer.stop()
        self._armed = None
        self._ended_at = None
        self._finished = False
        if entry is None:
            self.stop()
            return
        if self.player.active():
            self.player.stop()  # stop the old track at once while the new one resolves
        self._loaded.clear()
        self._playing_uid = None
        self._waiting_uid = entry.uid
        self._waiting_fast = fast
        self._start_paused = paused
        self._resume_at = resume_at
        self._last_pos = resume_at or 0.0
        self._set_state(PlaybackState.LOADING)
        self.current_changed.emit(entry)
        self.stream_changed.emit(None)
        video_id = entry.track.video_id
        duration = entry.track.duration or 0
        if self.resolver.cached(video_id, valid_for=duration, best=not fast) is not None:
            self._start_waiting()
        else:
            self.resolver.request(video_id, Priority.NOW, fast=fast)

    def _start_waiting(self) -> None:
        """Load the entry we're waiting on, once its stream is resolved and usable."""
        entry = self.queue.entry(self._waiting_uid) if self._waiting_uid is not None else None
        if entry is None:
            return
        video_id = entry.track.video_id
        stream = self.resolver.cached(video_id, best=not self._waiting_fast)
        if stream is None:
            self.resolver.request(video_id, Priority.NOW, fast=self._waiting_fast)
            return
        wait = stream.wait_seconds()
        if wait > 0:
            log.info("stream for %s usable in %.1fs; waiting", video_id, wait)
            self._start_timer.start(int(wait * 1000) + 100)
            return
        entry_id = self.player.load(
            stream.url, append=False, options=self._file_options(stream, self._resume_at)
        )
        self.player.set_paused(self._start_paused)
        self.player.set_loop_file(self.queue.repeat is RepeatMode.ONE)
        self._loaded = {entry_id: _Loaded(entry.uid, stream)}
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
        """Make mpv's armed entry match the queue's next entry (idempotent).

        Only Premium streams are armed (when signed in): nobody waits for a prefetch.
        """
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
            entry.track.video_id, valid_for=remaining + (entry.track.duration or 0), best=True
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
        self._loaded[entry_id] = _Loaded(want, stream)
        log.info("armed next track %s (%s)", entry.track.video_id, stream.quality_text)

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
        if self.resolver.is_pending(video_id):
            return  # the other kind of resolve (fast or Premium) may still work
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
        self._skip_after_failure(fast=self._waiting_fast)

    def _skip_after_failure(self, *, fast: bool) -> None:
        entry = self.queue.advance()
        if entry is None:
            self.stop()
        else:
            self._play_entry(entry, fast=fast)

    # -- player events -----------------------------------------------------------------

    def _on_file_started(self, entry_id: int) -> None:
        loaded = self._loaded.get(entry_id)
        if loaded is None:
            return
        entry = self.queue.entry(loaded.uid)
        if entry is None:  # removed from the queue while mpv was opening it
            current = self.queue.current()
            if current is not None and current.uid == self._playing_uid:
                current = self.queue.advance()
            self._play_entry(current, fast=False)
            return
        self._playing_uid = entry.uid
        self._stream_error_uid = None
        self._prefetch_failed.discard(entry.uid)
        if self._armed is not None and self._armed[1] == entry_id:
            self._armed = None
        current = self.queue.current()
        if current is None or current.uid != entry.uid:
            self.queue.move_to(entry.uid)
            self.current_changed.emit(entry)
        self.player.playlist_clear()  # drop the finished entry; keeps this one
        self._loaded = {entry_id: loaded}
        self.stream_changed.emit(loaded.stream)
        self.reconcile()
        self._maybe_top_up()

    def _on_file_ended(self, entry_id: int, reason: str, error: str) -> None:
        loaded = self._loaded.get(entry_id)
        if loaded is None:
            return
        uid = loaded.uid
        if reason == "eof" and self._stream_error_uid == uid:
            reason, error = "error", "stream interrupted"
        if reason == "error":
            self._loaded.pop(entry_id, None)
            self._handle_playback_error(uid, loaded.stream, error)
            return
        if reason != "eof" or uid != self._playing_uid:
            return
        self._ended_at = time.monotonic()
        if self._armed is None:
            # Nothing armed in time (or the queue ended): continue the slow way.
            self._playing_uid = None
            next_entry = self.queue.advance()
            if next_entry is None:
                self._finished = True
                self._last_pos = 0.0
                self._set_state(PlaybackState.STOPPED)
            else:
                # Premium, unless its prefetch failed: the fast way has a fallback.
                self._play_entry(next_entry, fast=next_entry.uid in self._prefetch_failed)

    def _handle_playback_error(self, uid: int, stream: StreamInfo, error: str) -> None:
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
            if self._ended_at is not None:
                # The current track has already ended, so nothing plays: continue now.
                self._playing_uid = None
                next_entry = self.queue.advance()
                self._play_entry(next_entry, fast=uid in self._prefetch_failed)
                return
            self.reconcile()
            return
        log.warning("playback error on %s: %s", entry.track.video_id, error)
        if uid not in self._retried:
            self._retried.add(uid)
            # Same quality as before, so the sound doesn't change in the middle of a track.
            self._play_entry(
                entry,
                fast=stream.fast,
                resume_at=self._last_pos or None,
                paused=self._state is PlaybackState.PAUSED,
            )
            return
        self.notice.emit(f"Skipped “{entry.track.title}”: playback failed")
        self._skip_after_failure(fast=stream.fast)

    def _on_playback_restarted(self) -> None:
        if self._ended_at is not None:
            gap_ms = (time.monotonic() - self._ended_at) * 1000
            self._ended_at = None
            log.info("track transition: %.0f ms", gap_ms)
            self.transition_measured.emit(gap_ms)
        if self._playing_uid is not None:
            self._set_state(PlaybackState.PAUSED if self.player.paused() else PlaybackState.PLAYING)

    def _on_pause_changed(self, paused: bool) -> None:
        if self._playing_uid is not None:
            self._set_state(PlaybackState.PAUSED if paused else PlaybackState.PLAYING)

    def _on_idle_changed(self, idle: bool) -> None:
        if idle and self._waiting_uid is None and self._playing_uid is None:
            self._set_state(PlaybackState.STOPPED)

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
        # mpv is destroyed after a while stopped or paused, and kept while anything plays.
        if state in (PlaybackState.PAUSED, PlaybackState.STOPPED):
            if not self._release_timer.isActive():
                self._release_timer.start()
        else:
            self._release_timer.stop()
        if state is not self._state:
            self._state = state
            self.state_changed.emit(state.value)
