"""YouTube Music API, from the app's side.

The calls run in the worker process (ytm_worker.ytm), so ytmusicapi, requests and the
heap churn of big JSON responses stay out of the player. Methods block until the worker
answers, so call them from the api pool, never on the UI thread.
"""

from __future__ import annotations

from typing import Any

from ytm_lite.app import codec
from ytm_lite.app.auth import Credentials
from ytm_lite.app.models import (
    LIKED_SONGS_ID,
    Account,
    Album,
    AlbumSummary,
    Artist,
    ArtistSummary,
    Playlist,
    PlaylistSummary,
    SearchResults,
    Track,
)
from ytm_lite.app.worker_client import WorkerError, WorkerProcess
from ytm_worker.protocol import ApiErrorKind

__all__ = ["Api", "ApiError", "ApiErrorKind"]


class ApiError(Exception):
    def __init__(self, kind: ApiErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class Api:
    def __init__(self, worker: WorkerProcess) -> None:
        self._worker = worker
        self._headers: dict[str, str] | None = None

    @property
    def signed_in(self) -> bool:
        return self._headers is not None

    def set_credentials(self, credentials: Credentials | None) -> None:
        self._headers = credentials.ytmusic_headers() if credentials else None
        self._worker.configure(api_headers=self._headers)

    def _call(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        background: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            return self._worker.call_api(method, args, background=background, headers=headers)
        except WorkerError as exc:
            raise ApiError(ApiErrorKind(exc.kind), str(exc)) from exc

    def _require_auth(self) -> None:
        if not self.signed_in:
            raise ApiError(ApiErrorKind.AUTH, "Not signed in")

    # -- operations ---------------------------------------------------------------------

    def account_info(self) -> Account:
        self._require_auth()
        return codec.account_from_json(self._call("account_info"))

    def account_info_for(self, credentials: Credentials) -> Account:
        """Check credentials that aren't in use yet (the sign-in dialog)."""
        return codec.account_from_json(
            self._call("account_info", headers=credentials.ytmusic_headers())
        )

    def library_playlists(self) -> list[PlaylistSummary]:
        self._require_auth()
        return [codec.playlist_summary_from_json(p) for p in self._call("library_playlists")]

    def library_albums(self) -> list[AlbumSummary]:
        self._require_auth()
        return [codec.album_summary_from_json(a) for a in self._call("library_albums")]

    def library_artists(self) -> list[ArtistSummary]:
        self._require_auth()
        return [codec.artist_summary_from_json(a) for a in self._call("library_artists")]

    def playlist(
        self, playlist_id: str, limit: int | None, *, background: bool = False
    ) -> Playlist:
        """`limit=None` fetches every track (one request per 100)."""
        if playlist_id == LIKED_SONGS_ID:
            self._require_auth()
        raw = self._call(
            "playlist", {"playlist_id": playlist_id, "limit": limit}, background=background
        )
        return codec.playlist_from_json(raw)

    def rate_song(self, video_id: str, liked: bool) -> None:
        self._require_auth()
        self._call("rate_song", {"video_id": video_id, "liked": liked})

    def radio(self, video_id: str) -> list[Track]:
        """Radio suggestions seeded by a track (works signed out too); the seed itself
        usually comes back first."""
        return [codec.track_from_json(t) for t in self._call("radio", {"video_id": video_id})]

    # These work signed out too.

    def search(self, query: str, filter_: str | None = None) -> SearchResults:
        """`filter_`: None for everything, or songs/albums/artists/playlists."""
        raw = self._call("search", {"query": query, "filter": filter_})
        return codec.search_results_from_json(raw)

    def search_suggestions(self, query: str) -> list[str]:
        return [str(s) for s in self._call("search_suggestions", {"query": query})]

    def album(self, browse_id: str) -> Album:
        return codec.album_from_json(self._call("album", {"browse_id": browse_id}))

    def artist(self, channel_id: str) -> Artist:
        return codec.artist_from_json(self._call("artist", {"channel_id": channel_id}))
