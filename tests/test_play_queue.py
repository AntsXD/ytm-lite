from __future__ import annotations

import random

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


def test_move_to() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c"))
    uid = q.items[2].uid
    entry = q.move_to(uid)
    assert entry is not None and entry.uid == uid
    assert q.move_to(9999) is None


def test_version_tracks_structure_not_the_cursor() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c"))
    version = q.version
    q.advance()
    q.previous()
    q.move_to(q.items[2].uid)
    assert q.version == version
    q.add(tracks("d"))
    assert q.version > version


def shuffled(seed: int = 7) -> PlayQueue:
    return PlayQueue(random.Random(seed))


def test_set_with_shuffle_starts_with_the_chosen_track() -> None:
    q = shuffled()
    q.set_shuffle(True)
    q.set(tracks(*"abcdefgh"), start=3)
    assert q.cursor == 0 and ids(q)[0] == "d"
    assert sorted(ids(q)) == list("abcdefgh")
    q.set_shuffle(False)
    assert ids(q) == list("abcdefgh")
    assert q.cursor == 3  # still on "d", in its original place


def test_shuffle_only_reorders_what_is_ahead() -> None:
    q = shuffled()
    q.set(tracks(*"abcdefgh"), start=2)
    q.set_shuffle(True)
    assert ids(q)[:3] == ["a", "b", "c"]
    assert q.cursor == 2
    assert sorted(ids(q)[3:]) == list("defgh")
    q.advance()
    current = q.current()
    assert current is not None
    q.set_shuffle(False)
    assert ids(q) == list("abcdefgh")
    assert q.current() == current


def test_play_next_under_shuffle_survives_unshuffling() -> None:
    q = shuffled()
    q.set(tracks(*"abcdef"))
    q.set_shuffle(True)
    q.play_next(tracks("x", "y"))
    assert ids(q)[1:3] == ["x", "y"]
    q.set_shuffle(False)
    assert ids(q)[:3] == ["a", "x", "y"]  # still right after the current track


def test_add_appends_in_both_orders() -> None:
    q = shuffled()
    q.set(tracks(*"abc"))
    q.set_shuffle(True)
    q.add(tracks("z"))
    assert ids(q)[-1] == "z"
    q.set_shuffle(False)
    assert ids(q) == ["a", "b", "c", "z"]


def test_adding_to_an_empty_queue_makes_the_first_current() -> None:
    q = PlayQueue()
    q.add(tracks("a", "b"))
    current = q.current()
    assert current is not None and current.track.video_id == "a"
    q = PlayQueue()
    q.play_next(tracks("x"))
    current = q.current()
    assert current is not None and current.track.video_id == "x"


def test_remove_current_makes_the_following_entry_current() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c"), start=1)
    assert q.remove([q.items[1].uid]) is True
    current = q.current()
    assert current is not None and current.track.video_id == "c"
    assert ids(q) == ["a", "c"]


def test_remove_last_current_falls_back_to_the_one_before() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b"), start=1)
    assert q.remove([q.items[1].uid]) is True
    current = q.current()
    assert current is not None and current.track.video_id == "a"


def test_remove_before_the_cursor_keeps_the_current_entry() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c", "d"), start=2)
    assert q.remove([q.items[0].uid, q.items[3].uid]) is False
    current = q.current()
    assert current is not None and current.track.video_id == "c"
    assert q.cursor == 1


def test_remove_everything() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b"))
    assert q.remove([e.uid for e in q.items]) is True
    assert q.current() is None and q.cursor == -1 and len(q) == 0


def test_move_keeps_the_cursor_on_the_current_entry() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c", "d"), start=1)
    assert q.move(q.items[3].uid, 0)
    assert ids(q) == ["d", "a", "b", "c"]
    current = q.current()
    assert current is not None and current.track.video_id == "b" and q.cursor == 2
    assert not q.move(q.items[0].uid, 0)  # already there
    assert not q.move(9999, 1)
    q.set_shuffle(True)
    q.set_shuffle(False)
    assert ids(q) == ["d", "a", "b", "c"]  # unshuffled, a move is the new original order


def test_clear_keeps_only_the_current_entry() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c"), start=1)
    q.clear()
    assert ids(q) == ["b"] and q.cursor == 0
    assert q.original_order == (q.items[0].uid,)


def test_repeat_all_previous_wraps_and_repeat_one_keeps_next() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c"))
    q.repeat = RepeatMode.ALL
    previous = q.previous()
    assert previous is not None and previous.track.video_id == "c"
    q.repeat = RepeatMode.ONE
    assert q.next_index() is None  # at the end; repeat-one doesn't wrap Next


def test_extend_radio_leaves_out_the_seed_recent_upcoming_and_duplicates() -> None:
    q = PlayQueue()
    q.set(tracks("a", "b", "c"))
    q.advance()  # a and b were played
    added = q.extend_radio(
        tracks("c", "a", "b", "r1", "r1", "r2", "u", unavailable=("u",)), seed_video_id="c"
    )
    assert [e.track.video_id for e in added] == ["r1", "r2"]
    assert all(e.origin == "radio" for e in added)
    assert ids(q) == ["a", "b", "c", "r1", "r2"]


def test_last_and_remaining() -> None:
    q = PlayQueue()
    assert q.last() is None and q.remaining() == 0
    q.set(tracks("a", "b", "c"))
    last = q.last()
    assert last is not None and last.track.video_id == "c"
    assert q.remaining() == 2


def test_restore_validates_its_input() -> None:
    q = PlayQueue()
    entries = [(t, "context") for t in tracks("a", "b", "c")]
    q.restore(entries, cursor=1, original=[2, 0, 1], shuffled=True, repeat=RepeatMode.ALL)
    assert q.shuffled and q.repeat is RepeatMode.ALL
    current = q.current()
    assert current is not None and current.track.video_id == "b"
    q.set_shuffle(False)
    assert ids(q) == ["c", "a", "b"]  # the saved original order

    q.restore(entries, cursor=7, original=[0, 0, 1], shuffled=False, repeat=RepeatMode.OFF)
    assert q.cursor == 0 and q.original_order == tuple(e.uid for e in q.items)
