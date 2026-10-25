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
    def __init__(self, cache_dir: Path) -> None:
        super().__init__(WorkerProcess(cache_dir))  # never started: request() is faked
        self.requests: list[tuple[str, int]] = []

    def request(
        self,
        video_id: str,
        priority: Priority = Priority.NOW,
        *,
        format_override: str | None = None,
    ) -> None:
        self.requests.append((video_id, int(priority)))
        self._pending[video_id] = int(priority)

    def complete(self, video_id: str, *, wait: float = 0.0) -> StreamInfo:
        stream = StreamInfo(
            video_id=video_id,
            url=f"https://x.googlevideo.com/{video_id}",
            format_id="251",
            codec="opus",
            abr=134.0,
            expires_at=time.time() + 6 * 3600,
            available_at=time.time() + wait if wait else None,
            http_headers={"User-Agent": "UA"},
        )
        self._cache[video_id] = stream
        self._pending.pop(video_id, None)
        self.resolved.emit(video_id, stream)
        return stream

    def fail(self, video_id: str, kind: str = "unavailable", message: str = "gone") -> None:
        self._pending.pop(video_id, None)
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
    extraction_failing: int = 0

    def play(self, *ids: str, start: int = 0) -> None:
        self.controller.play_tracks([Track(i, i.upper(), duration=100) for i in ids], start)

    def state(self) -> PlaybackState:
        # A method, so mypy doesn't narrow the state across calls that change it.
        return self.controller.state

    @property
    def current_id(self) -> str | None:
        entry = self.queue.current()
        return entry.track.video_id if entry else None


@pytest.fixture
def rig(qapp: QApplication, tmp_path: Path) -> Rig:
    player = FakePlayer()
    resolver = FakeResolver(tmp_path)
    queue = PlayQueue()
    rig = Rig(PlaybackController(queue, resolver, player), player, resolver, queue)

    def on_current(entry: Any) -> None:
        rig.current.append(entry.track.video_id if entry else "")

    def on_failing() -> None:
        rig.extraction_failing += 1

    rig.controller.current_changed.connect(on_current)
    rig.controller.notice.connect(rig.notices.append)
    rig.controller.transition_measured.connect(rig.gaps.append)
    rig.controller.extraction_failing.connect(on_failing)
    return rig


def start_playing(rig: Rig, *ids: str) -> None:
    """Play `ids`, resolve and start the first, and resolve + arm the second."""
    rig.play(*ids)
    rig.resolver.complete(ids[0])
    rig.player.start(rig.player.entries[0])
    if len(ids) > 1:
        rig.resolver.complete(ids[1])


def test_play_resolves_loads_and_arms_next(rig: Rig) -> None:
    rig.play("a", "b", "c")
    assert rig.resolver.requests == [("a", NOW)]
    assert rig.state() is PlaybackState.LOADING
    assert rig.current == ["a"]

    rig.resolver.complete("a")
    assert rig.player.loads[-1] == ("https://x.googlevideo.com/a", False, {"user-agent": "UA"})

    rig.player.start(1)
    assert rig.state() is PlaybackState.PLAYING
    assert rig.resolver.requests[-1] == ("b", PREFETCH)

    rig.resolver.complete("b")
    assert rig.player.loads[-1][:2] == ("https://x.googlevideo.com/b", True)


def test_gapless_transition_follows_start_file(rig: Rig) -> None:
    start_playing(rig, "a", "b", "c")
    rig.player.end(1, "eof")
    rig.player.start(2)
    assert rig.current_id == "b"
    assert rig.current == ["a", "b"]
    assert rig.resolver.requests[-1] == ("c", PREFETCH)
    assert len(rig.gaps) == 1 and rig.gaps[0] >= 0
    assert "playlist_clear" in rig.player.calls


def test_next_uses_armed_entry(rig: Rig) -> None:
    start_playing(rig, "a", "b")
    rig.controller.next()
    assert rig.player.calls[-1] == "playlist_next"
    assert rig.current_id == "b"  # the UI moves at once
    assert len(rig.player.loads) == 2  # nothing reloaded


def test_next_without_armed_entry_resolves_now(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.complete("a")
    rig.player.start(1)
    rig.controller.next()
    assert "stop" in rig.player.calls
    assert rig.resolver.requests[-1] == ("b", NOW)
    assert rig.state() is PlaybackState.LOADING


def test_end_of_queue_stops(rig: Rig) -> None:
    start_playing(rig, "a")
    rig.player.end(1, "eof")
    assert rig.state() is PlaybackState.STOPPED


def test_unavailable_track_is_skipped(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.fail("a", "unavailable")
    assert rig.notices and "A" in rig.notices[0]
    assert rig.resolver.requests[-1] == ("b", NOW)
    assert rig.current_id == "b"


def test_repeated_extraction_failures_stop_playback(rig: Rig) -> None:
    rig.play("a", "b", "c", "d")
    for video_id in ("a", "b", "c"):
        rig.resolver.fail(video_id, "extraction", "broken")
    assert rig.extraction_failing == 1
    assert rig.state() is PlaybackState.STOPPED
    assert ("d", NOW) not in rig.resolver.requests


def test_playback_error_retries_once_at_position_then_skips(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.complete("a")
    rig.player.start(1)
    rig.player.pos = 42.0
    rig.controller.position()
    rig.player.end(1, "error", "HTTP error 403")
    assert rig.resolver.cached("a") is None
    assert rig.resolver.requests[-1] == ("a", NOW)

    rig.resolver.complete("a")
    assert rig.player.loads[-1][2]["start"] == "42.000"
    entry_id = rig.player.entries[0]
    rig.player.start(entry_id)
    rig.player.end(entry_id, "error", "HTTP error 403")
    assert rig.resolver.requests[-1] == ("b", NOW)
    assert any("playback failed" in n for n in rig.notices)


def test_stream_error_turns_eof_into_error(rig: Rig) -> None:
    rig.play("a", "b")
    rig.resolver.complete("a")
    rig.player.start(1)
    rig.player.stream_error.emit("ffmpeg: HTTP error 403 Forbidden")
    rig.player.end(1, "eof")
    assert rig.resolver.requests[-1] == ("a", NOW)


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
    assert rig.resolver.requests[-1] == ("a", PREFETCH)
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
