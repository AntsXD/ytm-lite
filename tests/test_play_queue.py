from __future__ import annotations

from ytm_lite.app.models import Track
from ytm_lite.app.play_queue import PlayQueue, RepeatMode


def tracks(*ids: str, unavailable: tuple[str, ...] = ()) -> list[Track]:
    return [Track(i, i.upper(), available=i not in unavailable) for i in ids]


def ids(queue: PlayQueue) -> list[str]:
    return [e.track.video_id for e in queue.items]


def test_set_starts_at_index() -> None:
    q = PlayQueue()
    entry = q.set(tracks("a", "b", "c"), start=1)
    assert entry is not None and entry.track.video_id == "b"
    assert q.cursor == 1
    assert len({e.uid for e in q.items}) == 3


def test_set_empty() -> None:
    q = PlayQueue()
    assert q.set([]) is None
    assert q.next_uid() is None and q.advance() is None


def test_duplicates_get_distinct_uids() -> None:
    q = PlayQueue()
    q.set(tracks("a", "a"))
    first, second = q.items
    assert first.uid != second.uid
    assert q.next_uid() == second.uid


def test_advance_until_end_without_repeat() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b"))
    assert q.advance() is not None
    assert q.next_uid() is None
    assert q.advance() is None
    assert q.cursor == 1


def test_repeat_all_wraps() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b"), start=1)
    q.repeat = RepeatMode.ALL
    assert q.next_uid() == q.items[0].uid
    entry = q.advance()
    assert entry is not None and entry.track.video_id == "a"


def test_repeat_one_does_not_change_next() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b"))
    q.repeat = RepeatMode.ONE  # mpv loops the file; Next still moves on
    assert q.next_uid() == q.items[1].uid


def test_unavailable_tracks_are_skipped() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c", unavailable=("b",)))
    assert q.next_uid() == q.items[2].uid
    q.advance()
    previous = q.previous()
    assert previous is not None and previous.track.video_id == "a"


def test_previous_at_start() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b"))
    assert q.previous() is None
    q.repeat = RepeatMode.ALL
    entry = q.previous()
    assert entry is not None and entry.track.video_id == "b"


def test_move_to_and_version() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c"))
    version = q.version
    uid = q.items[2].uid
    entry = q.move_to(uid)
    assert entry is not None and entry.uid == uid
    assert q.version > version
    assert q.move_to(9999) is None
