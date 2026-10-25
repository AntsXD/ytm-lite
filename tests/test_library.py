"""Library flows against a fake API (no network) and real worker threads."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ytm_lite.app.api import Api, ApiError, ApiErrorKind
from ytm_lite.app.library import FIRST_PAGE, Library
from ytm_lite.app.library_cache import LibraryCache
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import LIKED_SONGS_ID, Account, Playlist, PlaylistSummary, Track
from ytm_lite.app.session import Session
from ytm_lite.app.workers import Workers


class FakeApi:
    signed_in = True

    def __init__(self) -> None:
        self.playlists: list[PlaylistSummary] = [PlaylistSummary("PL1", "Mine")]
        self.calls: list[tuple[str, int | None]] = []
        self.fail_account: ApiError | None = None

    def account_info(self) -> Account:
        if self.fail_account:
            raise self.fail_account
        return Account("Tester")

    def library_playlists(self) -> list[PlaylistSummary]:
        return self.playlists

    def library_albums(self) -> list[Any]:
        return []

    def library_artists(self) -> list[Any]:
        return []

    def playlist(
        self, playlist_id: str, limit: int | None, *, background: bool = False
    ) -> Playlist:
        self.calls.append((playlist_id, limit))
        count = 250
        n = count if limit is None else min(limit, count)
        tracks = tuple(Track(f"{playlist_id}-{i}", f"T{i}") for i in range(n))
        return Playlist(playlist_id, "Title", tracks, n >= count, track_count=count)


class FakeSession(QObject):
    changed = Signal()
    refreshed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.signed_in = True
        self.auth_failures = 0
        self.account: Account | None = None

    def handle_auth_failure(self) -> None:
        self.auth_failures += 1

    def set_account(self, account: Account) -> None:
        self.account = account


@pytest.fixture
def rig(qapp: QApplication, tmp_path: Path) -> tuple[Library, FakeApi, FakeSession, LibraryCache]:
    api = FakeApi()
    session = FakeSession()
    workers = Workers()
    cache = LibraryCache(tmp_path)
    liked = LikedIndex(cast(Api, api), workers)
    library = Library(cast(Api, api), workers, cache, liked, cast(Session, session))
    return library, api, session, cache


def test_playlist_loads_first_page_then_everything(qtbot: QtBot, rig: Any, tmp_path: Path) -> None:
    library, api, _session, cache = rig
    updates: list[Playlist] = []
    library.playlist_updated.connect(updates.append)
    assert library.open_playlist("PL1") is None
    qtbot.waitUntil(lambda: any(p.complete for p in updates), timeout=5000)
    assert [len(p.tracks) for p in updates] == [FIRST_PAGE, 250]
    assert api.calls == [("PL1", FIRST_PAGE), ("PL1", None)]
    qtbot.waitUntil(lambda: (tmp_path / "playlist-PL1.json").exists(), timeout=3000)
    assert cache.load_playlist("PL1") is not None


def test_cached_playlist_shows_first_then_refreshes(qtbot: QtBot, rig: Any) -> None:
    library, api, _session, cache = rig
    cache.save_playlist(Playlist("PL1", "Old", (Track("x", "X"),), True))
    updates: list[Playlist] = []
    library.playlist_updated.connect(updates.append)
    library.open_playlist("PL1")
    qtbot.waitUntil(lambda: len(updates) == 2, timeout=5000)
    assert updates[0].title == "Old"
    assert len(updates[1].tracks) == 250
    assert api.calls == [("PL1", None)]  # no first-page request needed


def test_refresh_fills_liked_index(qtbot: QtBot, rig: Any) -> None:
    library, _api, session, _cache = rig
    library.refresh()
    qtbot.waitUntil(lambda: library._liked.is_liked(f"{LIKED_SONGS_ID}-249"), timeout=5000)
    assert session.account == Account("Tester")


def test_rejected_session_is_reported(qtbot: QtBot, rig: Any) -> None:
    library, api, session, _cache = rig
    api.fail_account = ApiError(ApiErrorKind.PARSE, "no account menu")
    library.refresh()
    qtbot.waitUntil(lambda: session.auth_failures == 1, timeout=3000)


def test_suddenly_empty_library_counts_as_expired(qtbot: QtBot, rig: Any) -> None:
    library, api, session, _cache = rig
    library.playlists = [PlaylistSummary("PL1", "Mine")]
    api.playlists = []
    library.refresh()
    qtbot.waitUntil(lambda: session.auth_failures == 1, timeout=3000)
    assert library.playlists == [PlaylistSummary("PL1", "Mine")]
