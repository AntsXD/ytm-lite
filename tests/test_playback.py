"""Controller tests against a fake mpv and a fake resolver (no network, no audio)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from ytm_lite.app.models import Track
from ytm_lite.app.play_queue import PlayQueue, RepeatMode
from ytm_lite.app.playback import PlaybackController, PlaybackState
from ytm_lite.app.player import Player
from ytm_lite.app.resolver import ResolverClient, StreamInfo
from ytm_lite.app.worker_client import WorkerProcess
from ytm_worker.protocol import Priority

NOW, PREFETCH = int(Priority.NOW), int(Priority.PREFETCH)
FAST, BEST = True, False


def track(video_id: str) -> Track:
    return Track(video_id, video_id.upper(), duration=100)


class FakePlayer(Player):
    def __init__(self) -> None:
        super().__init__()
        self.is_active = False
        self.entries: list[int] = []
        self.current_id: int | None = None
        self.loads: list[tuple[str, bool, dict[str, str]]] = []
        self.calls: list[str] = []
        self._ids = iter(range(1, 1000))
        self.pos = 0.0
        self.dur = 100.0
        self._paused = False
        self.loop = False

    def active(self) -> bool:
        return self.is_active

    def load(self, url: str, *, append: bool, options: Mapping[str, str]) -> int:
        self.is_active = True
        entry_id = next(self._ids)
        self.entries = [*self.entries, entry_id] if append else [entry_id]
        self.loads.append((url, append, dict(options)))
        return entry_id

    def playlist_next(self) -> None:
        self.calls.append("playlist_next")

    def playlist_clear(self) -> None:
        self.calls.append("playlist_clear")
        self.entries = [e for e in self.entries if e == self.current_id]

    def stop(self) -> None:
        self.calls.append("stop")
        self.entries = []
        self.current_id = None

    def seek(self, seconds: float) -> None:
        self.pos = seconds

    def set_paused(self, paused: bool) -> None:
        if paused != self._paused:
            self._paused = paused
            self.pause_changed.emit(paused)

    def paused(self) -> bool:
        return self._paused

    def position(self) -> float | None:
        return self.pos

    def duration(self) -> float | None:
        return self.dur

    def set_loop_file(self, loop: bool) -> None:
        self.loop = loop

    def release(self) -> None:
        self.is_active = False
        self.entries = []
        self.released.emit()

    # Simulated mpv events
    def start(self, entry_id: int) -> None:
        self.current_id = entry_id
        self.file_started.emit(entry_id)
        self.playback_restarted.emit()

    def end(self, entry_id: int, reason: str = "eof", error: str = "") -> None:
        self.file_ended.emit(entry_id, reason, error)


class FakeResolver(ResolverClient):
    """Resolves only when a test says so. Signed in by default, so that fast (standard)
    and best (Premium) requests differ."""

    def __init__(self, cache_dir: Path) -> None:
        super().__init__(WorkerProcess(cache_dir))  # never started: request() is faked
        self.requests: list[tuple[str, int, bool]] = []
        self.premium_account = True

    @property
    def signed_in(self) -> bool:
        return self.premium_account

    def request(
        self,
        video_id: str,
        priority: Priority = Priority.NOW,
        *,
        fast: bool = False,
        format_override: str | None = None,
    ) -> None:
        fast = fast and self.signed_in
        self.requests.append((video_id, int(priority), fast))
        self._pending[(video_id, fast)] = int(priority)

    def complete(self, video_id: str, *, fast: bool | None = None, wait: float = 0.0) -> StreamInfo:
        """Answer a request: the fast one if it is pending, unless `fast` says otherwise."""
        if fast is None:
            fast = (video_id, True) in self._pending
        stream = StreamInfo(
            video_id=video_id,
            url=f"https://x.googlevideo.com/{video_id}" + ("-fast" if fast else ""),
            format_id="251" if fast else "774",
            codec="opus",
            abr=134.0 if fast else 256.0,
            expires_at=time.time() + 6 * 3600,
            available_at=time.time() + wait if wait else None,
            http_headers={"User-Agent": "UA"},
            premium=not fast,
            fast=fast,
        )
        self._pending.pop((video_id, fast), None)
        self._store(stream)
        self.resolved.emit(video_id, stream)
        return stream

    def fail(
        self,
        video_id: str,
        kind: str = "unavailable",
        message: str = "gone",
        *,
        fast: bool | None = None,
    ) -> None:
        if fast is None:
            fast = (video_id, True) in self._pending
        self._pending.pop((video_id, fast), None)
        self.failed.emit(video_id, kind, message)


@dataclass
class Rig:
    controller: PlaybackController
    player: FakePlayer
    resolver: FakeResolver
    queue: PlayQueue
    current: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    gaps: list[float] = field(default_factory=list)
    streams: list[StreamInfo | None] = field(default_factory=list)
    radio_seeds: list[str] = field(default_factory=list)
    extraction_failing: int = 0

    def play(self, *ids: str, start: int = 0) -> None:
        self.controller.play_tracks([track(i) for i in ids], start)

    def state(self) -> PlaybackState:
        # A method, so mypy doesn't narrow the state across calls that change it.
        return self.controller.state

    def ids(self) -> list[str]:
        return [e.track.video_id for e in self.queue.items]

    @property
    def current_id(self) -> str | None:
        entry = self.queue.current()
        return entry.track.video_id if entry else None


@pytest.fixture
def rig(qapp: QApplication, tmp_path: Path) -> Rig:
    player = FakePlayer()
    resolver = FakeResolver(tmp_path)
    queue = PlayQueue()
    controller = PlaybackController(queue, resolver, player)
    controller.radio = False  # the radio tests turn it on
    rig = Rig(controller, player, resolver, queue)

    def on_current(entry: Any) -> None:
        rig.current.append(entry.track.video_id if entry else "")

    def on_failing() -> None:
        rig.extraction_failing += 1

    controller.current_changed.connect(on_current)
    controller.notice.connect(rig.notices.append)
    controller.transition_measured.connect(rig.gaps.append)
    controller.stream_changed.connect(rig.streams.append)
    controller.radio_wanted.connect(rig.radio_seeds.append)
    controller.extraction_failing.connect(on_failing)
    return rig


def start_playing(rig: Rig, *ids: str) -> None:
    """Play `ids`, resolve and start the first, and resolve + arm the second."""
    rig.play(*ids)
    rig.resolver.complete(ids[0])
    rig.player.start(rig.player.entries[0])
    if len(ids) > 1:
        rig.resolver.complete(ids[1])


# -- quality: the clicked track fast, everything after it Premium ---------------------


def test_play_resolves_fast_then_prefetches_premium(rig: Rig) -> None:
    rig.play("a", "b", "c")
    assert rig.resolver.requests == [("a", NOW, FAST)]
    assert rig.state() is PlaybackState.LOADING
    assert rig.current == ["a"]

    rig.resolver.complete("a")
    assert rig.player.loads[-1] == ("https://x.googlevideo.com/a-fast", False, {"user-agent": "UA"})

    rig.player.start(1)
    assert rig.state() is PlaybackState.PLAYING
    stream = rig.streams[-1]
    assert stream is not None and stream.fast
    assert rig.resolver.requests[-1] == ("b", PREFETCH, BEST)

    rig.resolver.complete("b")
    assert rig.player.loads[-1][:2] == ("https://x.googlevideo.com/b", True)


def test_signed_out_never_asks_for_fast(rig: Rig) -> None:
    rig.resolver.premium_account = False
    rig.play("a", "b")
    assert rig.resolver.requests == [("a", NOW, BEST)]


def test_a_fast_stream_is_never_armed(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.complete("b", fast=True)  # e.g. clicked earlier: cached in standard quality
    rig.resolver.complete("a")
    rig.player.start(1)
    assert rig.resolver.requests[-1] == ("b", PREFETCH, BEST)
    assert len(rig.player.loads) == 1
    rig.resolver.complete("b", fast=False)
    assert rig.player.loads[-1][:2] == ("https://x.googlevideo.com/b", True)


def test_track_ending_before_its_prefetch_waits_for_premium(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.complete("a")
    rig.player.start(1)
    rig.player.end(1, "eof")
    assert rig.current_id == "b"
    assert rig.resolver.requests[-1] == ("b", NOW, BEST)
    rig.resolver.complete("b")
    assert rig.player.loads[-1][:2] == ("https://x.googlevideo.com/b", False)


def test_failure_of_one_kind_waits_for_the_other(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.request("a", Priority.PREFETCH)  # a Premium resolve is out too
    rig.resolver.fail("a", "extraction", "bot check", fast=True)
    assert rig.state() is PlaybackState.LOADING and rig.current_id == "a"
    assert not rig.notices
    rig.resolver.complete("a", fast=False)
    assert rig.player.loads[-1][0] == "https://x.googlevideo.com/a"


def test_error_retry_keeps_the_quality(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.player.end(1, "eof")
    rig.player.start(2)  # b, armed in Premium
    rig.player.pos = 10.0
    rig.controller.position()
    rig.player.end(2, "error", "HTTP error 403")
    assert rig.resolver.requests[-1] == ("b", NOW, BEST)


# -- moving through the queue ---------------------------------------------------------


def test_gapless_transition_follows_start_file(rig: Rig) -> None:
    start_playing(rig, "a", "b", "c")
    rig.player.end(1, "eof")
    rig.player.start(2)
    assert rig.current_id == "b"
    assert rig.current == ["a", "b"]
    assert rig.resolver.requests[-1] == ("c", PREFETCH, BEST)
    assert len(rig.gaps) == 1 and rig.gaps[0] >= 0
    assert "playlist_clear" in rig.player.calls
    stream = rig.streams[-1]
    assert stream is not None and stream.premium


def test_next_uses_armed_entry(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.controller.next()
    assert rig.player.calls[-1] == "playlist_next"
    assert rig.current_id == "b"  # the UI moves at once
    assert len(rig.player.loads) == 2  # nothing reloaded


def test_next_while_paused_plays(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.controller.toggle_pause()
    assert rig.player.paused()
    rig.controller.next()
    assert not rig.player.paused()


def test_next_without_armed_entry_resolves_fast(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.complete("a")
    rig.player.start(1)
    rig.controller.next()
    assert "stop" in rig.player.calls
    assert rig.resolver.requests[-1] == ("b", NOW, FAST)
    assert rig.state() is PlaybackState.LOADING


def test_end_of_queue_stops_and_play_starts_over(rig: Rig) -> None:
    start_playing(rig, "a")
    rig.player.pos = 99.0
    rig.controller.position()
    rig.player.end(1, "eof")
    assert rig.state() is PlaybackState.STOPPED
    rig.controller.toggle_pause()
    assert rig.current_id == "a"
    assert "start" not in rig.player.loads[-1][2]  # from the beginning


def test_play_after_the_end_continues_with_added_songs(rig: Rig) -> None:
    start_playing(rig, "a")
    rig.player.end(1, "eof")
    rig.controller.add_to_queue([track("x")])
    rig.controller.toggle_pause()
    assert rig.current_id == "x"
    assert rig.resolver.requests[-1] == ("x", NOW, FAST)


def test_unavailable_track_is_skipped(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.fail("a", "unavailable")
    assert rig.notices and "A" in rig.notices[0]
    assert rig.resolver.requests[-1] == ("b", NOW, FAST)  # still someone waiting
    assert rig.current_id == "b"


def test_repeated_extraction_failures_stop_playback(rig: Rig) -> None:
    rig.play("a", "b", "c", "d")
    for video_id in ("a", "b", "c"):
        rig.resolver.fail(video_id, "extraction", "broken")
    assert rig.extraction_failing == 1
    assert rig.state() is PlaybackState.STOPPED
    assert not any(r[0] == "d" for r in rig.resolver.requests)


def test_playback_error_retries_once_at_position_then_skips(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.complete("a")
    rig.player.start(1)
    rig.player.pos = 42.0
    rig.controller.position()
    rig.player.end(1, "error", "HTTP error 403")
    assert rig.resolver.cached("a") is None
    assert rig.resolver.requests[-1] == ("a", NOW, FAST)

    rig.resolver.complete("a")
    assert rig.player.loads[-1][2]["start"] == "42.000"
    entry_id = rig.player.entries[0]
    rig.player.start(entry_id)
    rig.player.end(entry_id, "error", "HTTP error 403")
    assert rig.resolver.requests[-1] == ("b", NOW, FAST)
    assert any("playback failed" in n for n in rig.notices)


def test_armed_track_failing_after_the_current_ended_is_retried_now(rig: Rig) -> None:
    start_playing(rig, "a", "b", "c")  # "b" is armed as entry 2
    rig.player.end(1, "eof")
    rig.player.end(2, "error", "HTTP error 403")
    assert rig.current_id == "b"
    assert rig.state() is PlaybackState.LOADING
    assert rig.resolver.requests[-1] == ("b", NOW, BEST)


def test_stream_error_turns_eof_into_error(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.complete("a")
    rig.player.start(1)
    rig.player.stream_error.emit("ffmpeg: HTTP error 403 Forbidden")
    rig.player.end(1, "eof")
    assert rig.resolver.requests[-1] == ("a", NOW, FAST)


def test_available_at_delays_the_load(rig: Rig) -> None:
    rig.play("a")
    rig.resolver.complete("a", wait=5)
    assert rig.player.loads == []
    assert rig.controller._start_timer.isActive()


def test_previous_restarts_track_after_threshold(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.controller.next()
    rig.player.start(2)
    rig.player.pos = 10.0
    rig.controller.previous()
    assert rig.player.pos == 0.0
    assert rig.current_id == "b"


def test_repeat_change_drops_stale_armed_entry(rig: Rig) -> None:
    rig.play("a", "b", start=1)
    rig.resolver.complete("b")
    rig.player.start(1)
    rig.controller.set_repeat(RepeatMode.ALL)
    assert rig.resolver.requests[-1] == ("a", PREFETCH, BEST)
    rig.resolver.complete("a")
    assert rig.player.loads[-1][1] is True  # "a" armed after "b"
    rig.controller.set_repeat(RepeatMode.OFF)
    assert rig.player.calls[-1] == "playlist_clear"


def test_pause_then_release_resumes_from_position(rig: Rig) -> None:
    start_playing(rig, "a")
    rig.player.pos = 30.0
    rig.controller.toggle_pause()
    assert rig.state() is PlaybackState.PAUSED
    rig.controller._release_player()
    assert not rig.player.active()
    assert rig.state() is PlaybackState.PAUSED
    rig.controller.toggle_pause()
    assert rig.player.loads[-1][2]["start"] == "30.000"


def test_double_click_while_paused_plays(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.controller.toggle_pause()
    rig.play("c")
    rig.resolver.complete("c")
    assert not rig.player.paused()


# -- editing the queue ------------------------------------------------------------------


def test_play_next_replaces_the_armed_entry(rig: Rig) -> None:
    start_playing(rig, "a", "b", "c")
    rig.controller.play_next([track("x")])
    assert rig.ids() == ["a", "x", "b", "c"]
    assert rig.player.calls[-1] == "playlist_clear"
    assert rig.resolver.requests[-1] == ("x", PREFETCH, BEST)
    assert any("will play next" in n for n in rig.notices)
    rig.resolver.complete("x")
    assert rig.player.loads[-1][:2] == ("https://x.googlevideo.com/x", True)


def test_removing_the_armed_entry_arms_the_following_one(rig: Rig) -> None:
    start_playing(rig, "a", "b", "c")
    rig.controller.remove([rig.queue.items[1].uid])
    assert rig.ids() == ["a", "c"]
    assert rig.player.calls[-1] == "playlist_clear"
    assert rig.resolver.requests[-1] == ("c", PREFETCH, BEST)


def test_removing_the_current_entry_plays_the_next(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.controller.remove([rig.queue.items[0].uid])
    assert rig.current_id == "b"
    assert rig.player.loads[-1][:2] == ("https://x.googlevideo.com/b", False)  # cached


def test_removing_the_current_entry_while_paused_stays_stopped(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.controller.toggle_pause()
    loads = len(rig.player.loads)
    rig.controller.remove([rig.queue.items[0].uid])
    assert rig.current_id == "b" and rig.current[-1] == "b"
    assert rig.state() is PlaybackState.STOPPED
    assert len(rig.player.loads) == loads


def test_move_changes_what_is_armed(rig: Rig) -> None:
    start_playing(rig, "a", "b", "c")
    rig.controller.move(rig.queue.items[2].uid, 1)
    assert rig.ids() == ["a", "c", "b"]
    assert rig.resolver.requests[-1] == ("c", PREFETCH, BEST)


def test_move_next(rig: Rig) -> None:
    start_playing(rig, "a", "b", "c", "d")
    rig.controller.move_next(rig.queue.items[3].uid)
    assert rig.ids() == ["a", "d", "b", "c"]


def test_adding_to_an_empty_queue_starts_playing(rig: Rig) -> None:
    rig.controller.add_to_queue([track("x"), Track("u", "U", available=False)])
    assert rig.ids() == ["x"]
    assert rig.current_id == "x"
    assert rig.resolver.requests == [("x", NOW, FAST)]
    assert not rig.notices


def test_shuffle_keeps_the_current_track_and_rearms(rig: Rig) -> None:
    start_playing(rig, "a", "b", "c", "d", "e", "f")
    rig.controller.set_shuffle(True)
    assert rig.current_id == "a" and rig.queue.cursor == 0
    armed = rig.controller._armed
    assert armed is None or armed[0] == rig.queue.next_uid()
    rig.controller.set_shuffle(False)
    assert rig.ids() == ["a", "b", "c", "d", "e", "f"]


def test_clear_queue_keeps_the_current_track(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.controller.clear_queue()
    assert rig.ids() == ["a"]
    assert rig.player.calls[-1] == "playlist_clear"
    assert rig.controller._armed is None


# -- radio ----------------------------------------------------------------------------------


def test_radio_tops_up_when_the_queue_runs_low(rig: Rig) -> None:
    rig.controller.radio = True
    rig.play("a")
    assert rig.radio_seeds == ["a"]
    suggestions = [track(i) for i in ("a", "r1", "r2", "r3", "r4")]
    rig.controller.add_radio("a", suggestions)
    assert rig.ids() == ["a", "r1", "r2", "r3", "r4"]  # the seed itself is left out
    assert all(e.origin == "radio" for e in rig.queue.items[1:])
    assert rig.radio_seeds == ["a"]  # four left: enough for now


def test_radio_off_fetches_nothing(rig: Rig) -> None:
    rig.play("a")
    assert rig.radio_seeds == []


def test_start_radio_works_with_autoplay_off(rig: Rig) -> None:
    rig.controller.start_radio(track("a"))
    assert rig.radio_seeds == ["a"]
    assert rig.resolver.requests == [("a", NOW, FAST)]


def test_radio_answer_for_a_replaced_queue_is_dropped(rig: Rig) -> None:
    rig.controller.radio = True
    rig.play("a")
    rig.play("b")
    assert rig.radio_seeds == ["a", "b"]
    rig.controller.add_radio("a", [track("r1")])
    assert rig.ids() == ["b"]


def test_radio_seed_that_brings_nothing_new_is_not_retried(rig: Rig) -> None:
    rig.controller.radio = True
    rig.play("a")
    rig.controller.add_radio("a", [track("a")])
    rig.controller.set_radio(True)  # would fetch again if the seed weren't spent
    assert rig.radio_seeds == ["a"]


def test_radio_failure_frees_the_slot_for_another_seed(rig: Rig) -> None:
    rig.controller.radio = True
    rig.play("a")
    rig.controller.radio_failed("a")
    rig.controller.add_to_queue([track("b")])
    assert rig.radio_seeds == ["a", "b"]


def test_radio_resumes_playback_after_the_queue_ran_out(rig: Rig) -> None:
    rig.controller.radio = True
    start_playing(rig, "a")
    rig.player.end(1, "eof")
    assert rig.state() is PlaybackState.STOPPED
    rig.controller.add_radio("a", [track("r1"), track("r2"), track("r3")])
    assert rig.current_id == "r1"
    assert rig.resolver.requests[-1] == ("r1", NOW, BEST)


# -- restoring a saved session ------------------------------------------------------------


def test_restore_is_paused_and_play_resumes_where_it_was(rig: Rig) -> None:
    entries = [(track("a"), "context"), (track("b"), "context")]
    rig.controller.restore(entries, 1, None, False, RepeatMode.ALL, 42.0)
    assert rig.state() is PlaybackState.PAUSED and rig.current_id == "b"
    assert rig.controller.position() == 42.0
    assert not rig.player.active()

    rig.controller.preload()
    assert rig.resolver.requests[-1] == ("b", PREFETCH, BEST)
    rig.controller.toggle_pause()
    assert rig.resolver.requests[-1] == ("b", NOW, FAST)
    rig.resolver.complete("b", fast=False)  # the Premium preload wins
    assert rig.player.loads[-1][0] == "https://x.googlevideo.com/b"
    assert rig.player.loads[-1][2]["start"] == "42.000"


def test_shuffle_play_starts_with_the_chosen_track(rig: Rig) -> None:
    modes: list[bool] = []
    rig.controller.modes_changed.connect(lambda: modes.append(rig.queue.shuffled))
    rig.controller.play_tracks([track(i) for i in "abcdef"], 3, shuffle=True)
    assert rig.current_id == "d" and rig.queue.cursor == 0 and rig.queue.shuffled
    assert sorted(rig.ids()) == list("abcdef")
    assert modes == [True]
    assert rig.resolver.requests == [("d", NOW, FAST)]
