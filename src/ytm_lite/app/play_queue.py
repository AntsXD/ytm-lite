"""The play queue: pure Python, no Qt, so its edge cases are easy to test.

Each entry gets a unique uid, so the same track can appear more than once. The queue
is the single source of truth for what plays next; mpv's playlist only mirrors it.

Two orders are kept: the play order (`items`) and the original order. Shuffling only
reorders what comes after the current entry; turning shuffle off restores the original
order around the current entry.
"""

from __future__ import annotations

import itertools
import random
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ytm_lite.app.models import Track

RECENT = 50  # radio skips tracks among the last this-many played


class RepeatMode(StrEnum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass(frozen=True, slots=True)
class QueueEntry:
    uid: int
    track: Track
    origin: str = "context"  # "context" (the list it was played from), "user", or "radio"


class PlayQueue:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._items: list[QueueEntry] = []  # play order
        self._original: list[int] = []  # uids in unshuffled order
        self._cursor = -1
        self._uids = itertools.count(1)
        self._rng = rng or random.Random()
        self._shuffle = False
        self._recent: deque[str] = deque(maxlen=RECENT)
        self.repeat = RepeatMode.OFF
        self.version = 0  # bumped when entries or their order change (not the cursor)

    # -- reading ---------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    @property
    def items(self) -> Sequence[QueueEntry]:
        return tuple(self._items)

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def shuffled(self) -> bool:
        return self._shuffle

    @property
    def original_order(self) -> Sequence[int]:
        return tuple(self._original)

    def current(self) -> QueueEntry | None:
        if 0 <= self._cursor < len(self._items):
            return self._items[self._cursor]
        return None

    def entry(self, uid: int) -> QueueEntry | None:
        index = self.index_of(uid)
        return None if index is None else self._items[index]

    def index_of(self, uid: int) -> int | None:
        for index, item in enumerate(self._items):
            if item.uid == uid:
                return index
        return None

    def last(self) -> QueueEntry | None:
        return self._items[-1] if self._items else None

    def upcoming(self) -> Sequence[QueueEntry]:
        return tuple(self._items[self._cursor + 1 :])

    def remaining(self) -> int:
        return max(0, len(self._items) - self._cursor - 1)

    # -- helpers -----------------------------------------------------------------------

    def _touch(self) -> None:
        self.version += 1

    def _make(self, tracks: Iterable[Track], origin: str) -> list[QueueEntry]:
        return [QueueEntry(next(self._uids), track, origin) for track in tracks]

    def _became_current(self) -> None:
        entry = self.current()
        if entry is not None:
            self._recent.append(entry.track.video_id)

    def _shuffle_upcoming(self) -> None:
        upcoming = self._items[self._cursor + 1 :]
        self._rng.shuffle(upcoming)
        self._items[self._cursor + 1 :] = upcoming

    # -- replacing -----------------------------------------------------------------------

    def set(
        self, tracks: Iterable[Track], start: int = 0, origin: str = "context"
    ) -> QueueEntry | None:
        """Replace the queue with `tracks` and make `start` current (shuffling the rest
        if shuffle is on)."""
        self._items = self._make(tracks, origin)
        self._original = [e.uid for e in self._items]
        self._cursor = min(max(start, 0), len(self._items) - 1) if self._items else -1
        if self._shuffle and self._items:
            current = self._items.pop(self._cursor)
            self._items.insert(0, current)
            self._cursor = 0
            self._shuffle_upcoming()
        self._became_current()
        self._touch()
        return self.current()

    def restore(
        self,
        entries: Sequence[tuple[Track, str]],
        cursor: int,
        original: Sequence[int] | None,
        shuffled: bool,
        repeat: RepeatMode,
    ) -> None:
        """Rebuild a saved queue. `original` lists play-order indexes in unshuffled order."""
        self._items = [QueueEntry(next(self._uids), track, origin) for track, origin in entries]
        order = list(original) if original is not None else list(range(len(self._items)))
        if sorted(order) != list(range(len(self._items))):
            order = list(range(len(self._items)))
        self._original = [self._items[i].uid for i in order]
        self._cursor = cursor if 0 <= cursor < len(self._items) else (0 if self._items else -1)
        self._shuffle = shuffled
        self.repeat = repeat
        self._touch()

    # -- moving through it ---------------------------------------------------------------

    def next_index(self) -> int | None:
        """Index that plays after the current one, skipping unavailable tracks."""
        count = len(self._items)
        if count == 0:
            return None
        for step in range(1, count + 1):
            index = self._cursor + step
            if index >= count:
                if self.repeat is not RepeatMode.ALL:
                    return None
                index %= count
            if self._items[index].track.available:
                return index
        return None

    def next_uid(self) -> int | None:
        index = self.next_index()
        return None if index is None else self._items[index].uid

    def advance(self) -> QueueEntry | None:
        """Move to the next entry; returns it, or None at the end of the queue."""
        index = self.next_index()
        if index is None:
            return None
        self._cursor = index
        self._became_current()
        return self.current()

    def previous(self) -> QueueEntry | None:
        """Move to the previous available entry; returns it, or None if there isn't one."""
        candidates = list(range(self._cursor - 1, -1, -1))
        if self.repeat is RepeatMode.ALL:
            candidates += list(range(len(self._items) - 1, self._cursor, -1))
        for index in candidates:
            if self._items[index].track.available:
                self._cursor = index
                self._became_current()
                return self.current()
        return None

    def move_to(self, uid: int) -> QueueEntry | None:
        index = self.index_of(uid)
        if index is None:
            return None
        if index != self._cursor:
            self._cursor = index
            self._became_current()
        return self.current()

    # -- editing ---------------------------------------------------------------------------

    def play_next(self, tracks: Iterable[Track], origin: str = "user") -> list[QueueEntry]:
        """Insert `tracks` right after the current entry (in both orders)."""
        entries = self._make(tracks, origin)
        if not entries:
            return []
        self._items[self._cursor + 1 : self._cursor + 1] = entries
        current = self.current()
        at = self._original.index(current.uid) + 1 if current is not None else 0
        self._original[at:at] = [e.uid for e in entries]
        if self._cursor < 0:
            self._cursor = 0
            self._became_current()
        self._touch()
        return entries

    def add(self, tracks: Iterable[Track], origin: str = "user") -> list[QueueEntry]:
        """Append `tracks` to the end of the queue."""
        entries = self._make(tracks, origin)
        if not entries:
            return []
        self._items.extend(entries)
        self._original.extend(e.uid for e in entries)
        if self._cursor < 0:
            self._cursor = 0
            self._became_current()
        self._touch()
        return entries

    def move(self, uid: int, index: int) -> bool:
        """Move an entry to `index` in the play order (a drag in the queue panel)."""
        source = self.index_of(uid)
        if source is None:
            return False
        index = max(0, min(index, len(self._items) - 1))
        if index == source:
            return False
        current = self.current()
        entry = self._items.pop(source)
        self._items.insert(index, entry)
        if current is not None:
            self._cursor = self._items.index(current)
        if not self._shuffle:
            self._original = [e.uid for e in self._items]
        self._touch()
        return True

    def remove(self, uids: Iterable[int]) -> bool:
        """Remove entries. Returns True if the current entry was among them; the entry
        that followed it (if any) is then current."""
        gone = set(uids)
        if not gone:
            return False
        current = self.current()
        removed_current = current is not None and current.uid in gone
        kept_before = sum(1 for e in self._items[: self._cursor] if e.uid not in gone)
        self._items = [e for e in self._items if e.uid not in gone]
        self._original = [uid for uid in self._original if uid not in gone]
        if not self._items:
            self._cursor = -1
        elif removed_current:
            self._cursor = min(kept_before, len(self._items) - 1)
            self._became_current()
        else:
            self._cursor = kept_before
        self._touch()
        return removed_current

    def clear(self) -> None:
        """Remove everything except the current entry."""
        current = self.current()
        self._items = [current] if current is not None else []
        self._original = [current.uid] if current is not None else []
        self._cursor = 0 if current is not None else -1
        self._touch()

    def set_shuffle(self, on: bool) -> None:
        if on == self._shuffle:
            return
        self._shuffle = on
        current = self.current()
        if on:
            self._shuffle_upcoming()
        else:
            by_uid = {e.uid: e for e in self._items}
            self._items = [by_uid[uid] for uid in self._original if uid in by_uid]
            if current is not None:
                self._cursor = self._items.index(current)
        self._touch()

    def extend_radio(
        self, tracks: Iterable[Track], seed_video_id: str | None = None
    ) -> list[QueueEntry]:
        """Append radio suggestions, leaving out the seed, recently played tracks and
        anything already coming up."""
        skip = set(self._recent) | {e.track.video_id for e in self.upcoming()}
        if seed_video_id:
            skip.add(seed_video_id)
        fresh: list[Track] = []
        for track in tracks:
            if track.video_id not in skip and track.available:
                skip.add(track.video_id)
                fresh.append(track)
        return self.add(fresh, origin="radio")
