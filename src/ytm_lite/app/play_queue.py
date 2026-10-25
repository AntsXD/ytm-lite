"""The play queue: pure Python, no Qt, so its edge cases are easy to test.

Each entry gets a unique uid, so the same track can appear more than once. The queue
is the single source of truth for what plays next; mpv's playlist only mirrors it.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ytm_lite.app.models import Track


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
    def __init__(self) -> None:
        self._items: list[QueueEntry] = []
        self._cursor = -1
        self._uids = itertools.count(1)
        self.repeat = RepeatMode.OFF
        self.version = 0  # bumped on every change

    def __len__(self) -> int:
        return len(self._items)

    @property
    def items(self) -> Sequence[QueueEntry]:
        return tuple(self._items)

    @property
    def cursor(self) -> int:
        return self._cursor

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

    def _touch(self) -> None:
        self.version += 1

    def set(
        self, tracks: Iterable[Track], start: int = 0, origin: str = "context"
    ) -> QueueEntry | None:
        """Replace the queue with `tracks` and make `start` current."""
        self._items = [QueueEntry(next(self._uids), track, origin) for track in tracks]
        self._cursor = min(max(start, 0), len(self._items) - 1) if self._items else -1
        self._touch()
        return self.current()

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
        self._touch()
        return self.current()

    def previous(self) -> QueueEntry | None:
        """Move to the previous available entry; returns it, or None if there isn't one."""
        for index in range(self._cursor - 1, -1, -1):
            if self._items[index].track.available:
                self._cursor = index
                self._touch()
                return self.current()
        if self.repeat is RepeatMode.ALL:
            for index in range(len(self._items) - 1, self._cursor, -1):
                if self._items[index].track.available:
                    self._cursor = index
                    self._touch()
                    return self.current()
        return None

    def move_to(self, uid: int) -> QueueEntry | None:
        index = self.index_of(uid)
        if index is None:
            return None
        if index != self._cursor:
            self._cursor = index
            self._touch()
        return self.current()
