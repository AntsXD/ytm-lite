"""The user's library: cached lists shown at once, then refreshed from YouTube.

Long playlists load in two steps: the first page (100 tracks) paints quickly, then the
rest is fetched at paging priority and merged in.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Signal

from ytm_lite.app.api import Api, ApiError, ApiErrorKind
from ytm_lite.app.library_cache import LibraryCache
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import (
    LIKED_SONGS_ID,
    Account,
    AlbumSummary,
    ArtistSummary,
    Playlist,
    PlaylistSummary,
)
from ytm_lite.app.session import Session
from ytm_lite.app.workers import Priority, Workers

log = logging.getLogger(__name__)
T = TypeVar("T")

FIRST_PAGE = 100
MEMORY_PLAYLISTS = 4


class Library(QObject):
    playlists_changed = Signal(object)  # list[PlaylistSummary]
    albums_changed = Signal(object)  # list[AlbumSummary]
    artists_changed = Signal(object)  # list[ArtistSummary]
    playlist_updated = Signal(object)  # Playlist
    account_changed = Signal(object)  # Account | None
    message = Signal(str)  # problem worth telling the user about

    def __init__(
        self,
        api: Api,
        workers: Workers,
        cache: LibraryCache,
        liked: LikedIndex,
        session: Session,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._workers = workers
        self._cache = cache
        self._liked = liked
        self._session = session
        self.playlists: list[PlaylistSummary] | None = None
        self.albums: list[AlbumSummary] | None = None
        self.artists: list[ArtistSummary] | None = None
        self._open: OrderedDict[str, Playlist] = OrderedDict()
        self._fetching: set[str] = set()
        self._generation = 0  # bumped on sign-in/out so stale results are dropped
        session.changed.connect(self._on_session_changed)
        session.refreshed.connect(self.refresh)

    # -- helpers --------------------------------------------------------------------

    def _guard(self, fn: Callable[[T], None]) -> Callable[[T], None]:
        """Drop the callback if the session changed since the work was submitted."""
        generation = self._generation

        def wrapped(value: T) -> None:
            if generation == self._generation:
                fn(value)

        return wrapped

    def _failed(self, what: str) -> Callable[[Exception], None]:
        def on_error(exc: Exception) -> None:
            if isinstance(exc, ApiError) and exc.kind is ApiErrorKind.AUTH:
                self._session.handle_auth_failure()
                return
            log.warning("loading %s failed: %s", what, exc)
            self.message.emit(f"Couldn't load {what}: {exc}")

        return self._guard(on_error)

    # -- startup -----------------------------------------------------------------------

    def load_cached(self) -> None:
        """Show whatever is on disk (io pool), before the network answers."""
        if not self._session.signed_in:
            return
        cache = self._cache

        def read() -> tuple[Any, ...]:
            return (
                cache.load_playlists(),
                cache.load_albums(),
                cache.load_artists(),
                cache.load_playlist(LIKED_SONGS_ID),
            )

        def show(result: tuple[Any, ...]) -> None:
            playlists, albums, artists, liked = result
            if playlists is not None and self.playlists is None:
                self._set_playlists(playlists)
            if albums is not None and self.albums is None:
                self.albums = albums
                self.albums_changed.emit(albums)
            if artists is not None and self.artists is None:
                self.artists = artists
                self.artists_changed.emit(artists)
            if liked is not None and LIKED_SONGS_ID not in self._open:
                self._remember(liked)
                self._liked.replace(t.video_id for t in liked.tracks)

        self._workers.submit(read, on_done=self._guard(show), pool="io")

    def refresh(self) -> None:
        """Check the session, then refresh every list from YouTube."""
        if not self._api.signed_in:
            return

        def account_ok(account: Account) -> None:
            self._session.set_account(account)
            self.account_changed.emit(account)
            self._refresh_lists()

        def account_failed(exc: Exception) -> None:
            # An expired session usually answers with a page we can't parse.
            if isinstance(exc, ApiError) and exc.kind in (ApiErrorKind.AUTH, ApiErrorKind.PARSE):
                self._session.handle_auth_failure()
            else:
                log.warning("account check failed: %s", exc)
                self._refresh_lists()

        self._workers.submit(
            self._api.account_info,
            on_done=self._guard(account_ok),
            on_error=self._guard(account_failed),
        )

    def _refresh_lists(self) -> None:
        def got_playlists(items: list[PlaylistSummary]) -> None:
            if not items and self.playlists:
                # An empty library where there used to be playlists means the session
                # quietly stopped working; keep what we have.
                self._session.handle_auth_failure()
                return
            self._set_playlists(items)
            self._workers.submit(lambda: self._cache.save_playlists(items), pool="io")

        def got_albums(items: list[AlbumSummary]) -> None:
            self.albums = items
            self.albums_changed.emit(items)
            self._workers.submit(lambda: self._cache.save_albums(items), pool="io")

        def got_artists(items: list[ArtistSummary]) -> None:
            self.artists = items
            self.artists_changed.emit(items)
            self._workers.submit(lambda: self._cache.save_artists(items), pool="io")

        submit = self._workers.submit
        submit(
            self._api.library_playlists,
            on_done=self._guard(got_playlists),
            on_error=self._failed("your playlists"),
        )
        submit(
            self._api.library_albums,
            on_done=self._guard(got_albums),
            on_error=self._failed("your albums"),
            priority=Priority.BACKGROUND,
        )
        submit(
            self._api.library_artists,
            on_done=self._guard(got_artists),
            on_error=self._failed("your artists"),
            priority=Priority.BACKGROUND,
        )
        # Liked Songs drives every heart, so fetch it even if its page isn't open.
        self._fetch_full(LIKED_SONGS_ID, "Liked Songs")

    def _set_playlists(self, items: list[PlaylistSummary]) -> None:
        self.playlists = items
        self.playlists_changed.emit(items)

    # -- playlists ---------------------------------------------------------------------

    def playlist(self, playlist_id: str) -> Playlist | None:
        """The playlist if it's already in memory."""
        return self._open.get(playlist_id)

    def open_playlist(self, playlist_id: str, title: str = "playlist") -> Playlist | None:
        """Show cached data right away (returned and/or emitted), then refresh it."""
        known = self._open.get(playlist_id)
        if known is not None:
            self._open.move_to_end(playlist_id)
            self._fetch_full(playlist_id, title)
            return known

        def cached(playlist: Playlist | None) -> None:
            if playlist is not None and playlist_id not in self._open:
                self._remember(playlist)
                self.playlist_updated.emit(playlist)
                self._fetch_full(playlist_id, title)
            elif playlist_id not in self._open:
                self._fetch_first_page(playlist_id, title)

        self._workers.submit(
            lambda: self._cache.load_playlist(playlist_id), on_done=self._guard(cached), pool="io"
        )
        return None

    def _fetch_first_page(self, playlist_id: str, title: str) -> None:
        def got(playlist: Playlist) -> None:
            if playlist_id in self._open and self._open[playlist_id].complete:
                return  # the full fetch won the race
            self._remember(playlist)
            self.playlist_updated.emit(playlist)
            if playlist.complete:
                self._save(playlist)
            else:
                self._fetch_full(playlist_id, title)

        self._workers.submit(
            lambda: self._api.playlist(playlist_id, FIRST_PAGE),
            on_done=self._guard(got),
            on_error=self._failed(title),
        )

    def _fetch_full(self, playlist_id: str, title: str) -> None:
        if playlist_id in self._fetching:
            return
        if playlist_id == LIKED_SONGS_ID and not self._api.signed_in:
            return
        self._fetching.add(playlist_id)

        def got(playlist: Playlist) -> None:
            self._fetching.discard(playlist_id)
            log.info("playlist loaded: %s, %d tracks", playlist_id, len(playlist.tracks))
            self._remember(playlist)
            self.playlist_updated.emit(playlist)
            self._save(playlist)

        def failed(exc: Exception) -> None:
            self._fetching.discard(playlist_id)
            self._failed(title)(exc)

        self._workers.submit(
            lambda: self._api.playlist(playlist_id, None, background=True),
            on_done=self._guard(got),
            on_error=failed,
            priority=Priority.PAGING,
        )

    def _remember(self, playlist: Playlist) -> None:
        self._open[playlist.id] = playlist
        self._open.move_to_end(playlist.id)
        while len(self._open) > MEMORY_PLAYLISTS:
            oldest = next(iter(self._open))
            if oldest == LIKED_SONGS_ID and len(self._open) > 1:
                self._open.move_to_end(oldest)  # Liked Songs stays: it backs the hearts
                oldest = next(iter(self._open))
            del self._open[oldest]

    def _save(self, playlist: Playlist) -> None:
        if playlist.id == LIKED_SONGS_ID and playlist.complete:
            self._liked.replace(t.video_id for t in playlist.tracks)
        self._workers.submit(lambda: self._cache.save_playlist(playlist), pool="io")

    # -- session -------------------------------------------------------------------------

    def _on_session_changed(self) -> None:
        self._generation += 1
        self._open.clear()
        self._fetching.clear()
        if self._session.signed_in:
            self.refresh()
            return
        self.playlists = self.albums = self.artists = None
        self.playlists_changed.emit([])
        self.albums_changed.emit([])
        self.artists_changed.emit([])
        self.account_changed.emit(None)
        self._liked.clear()
        self._workers.submit(self._cache.clear, pool="io")
