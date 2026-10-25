"""YouTube Music API calls (ytmusicapi), run inside the worker process.

ytmusicapi's raw responses are large; parsing a few thousand tracks inflates a Python
heap by tens of MB that is never handed back to the OS. Here that happens in a process
that exits when idle, and only the slim, compact-JSON results travel to the app.

YTMusic objects aren't safe to share between threads, and creating one isn't free (a
signed-out instance downloads the music.youtube.com homepage for a visitor id), so a
few are kept in a pool and reused.
"""

from __future__ import annotations

import json
import locale
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ytm_lite.app import codec, parse
from ytm_worker.imports import IMPORT_LOCK
from ytm_worker.protocol import ApiErrorKind

log = logging.getLogger("ytm_worker")

REQUEST_TIMEOUT = 15
BACKGROUND_INTERVAL = 0.1  # spec: >= 100 ms between paging/prefetch calls
LIBRARY_LIMIT = 5000  # "everything" for library lists
RADIO_LIMIT = 25  # tracks per radio top-up (one request)
SEARCH_LIMIT = 20  # results per search (ytmusicapi's default page)
SUGGESTIONS = 8
POOL_SIZE = 2


class ServiceError(Exception):
    def __init__(self, kind: ApiErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def classify(exc: BaseException) -> ServiceError:
    import requests
    from ytmusicapi.exceptions import YTMusicServerError, YTMusicUserError

    # ytmusicapi's lookup errors include the whole response; keep messages short.
    message = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
    if isinstance(exc, YTMusicServerError):
        status = re.search(r"HTTP (\d{3})", message)
        if status and status.group(1) in ("401", "403"):
            return ServiceError(ApiErrorKind.AUTH, message)
        return ServiceError(ApiErrorKind.SERVER, message)
    if isinstance(exc, YTMusicUserError):
        low = message.lower()
        kind = ApiErrorKind.AUTH if "auth" in low or "cookie" in low else ApiErrorKind.SERVER
        return ServiceError(kind, message)
    if isinstance(exc, requests.RequestException):
        return ServiceError(ApiErrorKind.NETWORK, message)
    if isinstance(exc, json.JSONDecodeError):
        return ServiceError(ApiErrorKind.SERVER, "YouTube sent an unreadable response")
    if isinstance(exc, (KeyError, IndexError, TypeError, ValueError)):
        return ServiceError(ApiErrorKind.PARSE, message)
    return ServiceError(ApiErrorKind.SERVER, message)


class Gate:
    """Spaces out requests made by background work (paging, prefetch)."""

    def __init__(self, interval: float = BACKGROUND_INTERVAL) -> None:
        self.interval = interval
        self.local = threading.local()
        self._lock = threading.Lock()
        self._next = 0.0

    def pass_through(self) -> None:
        if not getattr(self.local, "background", False):
            return
        with self._lock:  # holding the lock while sleeping serializes background requests
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next = time.monotonic() + self.interval


def _session(gate: Gate) -> Any:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    class Session(requests.Session):
        def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            kwargs.setdefault("timeout", REQUEST_TIMEOUT)
            gate.pass_through()
            return super().request(method, url, *args, **kwargs)

    session = Session()
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=None,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class _LocaleShim:
    """Stands in for the `locale` module inside ytmusicapi.ytmusic.

    YTMusic() calls setlocale(LC_ALL, language). The locale is process-wide; numbers
    must stay "C" (libmpv refuses to start otherwise, should this code ever run in the
    player). ytmusicapi copes: its number parsing falls back to stripping separators.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(locale, name)

    @staticmethod
    def setlocale(category: int, value: str | None = None) -> str:
        return locale.setlocale(category)  # query only


class YtmService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle: list[tuple[int, Any]] = []
        self._generation = 0
        self._headers: dict[str, str] | None = None
        self.visitor_id: str | None = None
        self.on_visitor: Callable[[str], None] | None = None
        self.gate = Gate()

    def configure(self, headers: dict[str, str] | None, visitor_id: str | None) -> None:
        with self._lock:
            if headers != self._headers:
                self._headers = headers
                self._generation += 1
                self._idle.clear()
            if visitor_id and not self.visitor_id:
                self.visitor_id = visitor_id

    # -- client pool ----------------------------------------------------------------------

    def _create(self, headers: dict[str, str] | None) -> Any:
        with IMPORT_LOCK:
            import ytmusicapi.ytmusic as ytmusic_module
            from ytmusicapi import YTMusic
            from ytmusicapi.helpers import initialize_headers

            if not isinstance(getattr(ytmusic_module, "locale", None), _LocaleShim):
                setattr(ytmusic_module, "locale", _LocaleShim())  # noqa: B010
        if headers is not None:
            headers = dict(headers)
            if self.visitor_id and "x-goog-visitor-id" not in headers:
                headers["x-goog-visitor-id"] = self.visitor_id
        client = YTMusic(auth=headers, requests_session=_session(self.gate), language="en")
        if headers is None and self.visitor_id:
            # Skip the homepage download a fresh signed-out instance would make.
            base = initialize_headers()
            base["X-Goog-Visitor-Id"] = self.visitor_id
            client.__dict__["base_headers"] = base
        return client

    def _remember_visitor(self, client: Any) -> None:
        if self.visitor_id is None:
            base = client.__dict__.get("base_headers")
            if base is not None and base.get("X-Goog-Visitor-Id"):
                self.visitor_id = str(base["X-Goog-Visitor-Id"])
                if self.on_visitor is not None:
                    self.on_visitor(self.visitor_id)

    @contextmanager
    def _client(self, headers_override: dict[str, str] | None) -> Iterator[Any]:
        if headers_override is not None:  # one-off, e.g. checking new credentials
            yield self._create(headers_override)
            return
        client = None
        with self._lock:
            generation = self._generation
            while self._idle:
                gen, candidate = self._idle.pop()
                if gen == generation:
                    client = candidate
                    break
            headers = self._headers
        if client is None:
            client = self._create(headers)
        try:
            yield client
        finally:
            self._remember_visitor(client)
            with self._lock:
                if generation == self._generation and len(self._idle) < POOL_SIZE:
                    self._idle.append((generation, client))

    # -- operations -----------------------------------------------------------------------

    def call(
        self,
        method: str,
        args: dict[str, Any],
        *,
        background: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Run one API operation; returns compact JSON. Raises ServiceError."""
        operation = _OPERATIONS.get(method)
        if operation is None:
            raise ServiceError(ApiErrorKind.SERVER, f"unknown API method {method!r}")
        self.gate.local.background = background
        try:
            with self._client(headers) as client:
                return operation(client, args)
        except ServiceError:
            raise
        except Exception as exc:
            raise classify(exc) from exc
        finally:
            self.gate.local.background = False


def _account_info(c: Any, _args: dict[str, Any]) -> Any:
    return codec.account_to_json(parse.account(c.get_account_info()))


def _library_playlists(c: Any, _args: dict[str, Any]) -> Any:
    items = (parse.playlist_summary(r) for r in c.get_library_playlists(limit=None))
    return [codec.playlist_summary_to_json(p) for p in items if p is not None]


def _library_albums(c: Any, _args: dict[str, Any]) -> Any:
    items = (parse.album_summary(r) for r in c.get_library_albums(limit=LIBRARY_LIMIT))
    return [codec.album_summary_to_json(a) for a in items if a is not None]


def _library_artists(c: Any, _args: dict[str, Any]) -> Any:
    items = (parse.artist_summary(r) for r in c.get_library_subscriptions(limit=LIBRARY_LIMIT))
    return [codec.artist_summary_to_json(a) for a in items if a is not None]


def _playlist(c: Any, args: dict[str, Any]) -> Any:
    playlist_id = str(args["playlist_id"])
    limit = args.get("limit")
    raw = c.get_playlist(playlist_id, limit=limit)
    return codec.playlist_to_json(parse.playlist(raw, playlist_id, limit))


def _rate_song(c: Any, args: dict[str, Any]) -> Any:
    from ytmusicapi.models.content.enums import LikeStatus

    rating = LikeStatus.LIKE if args.get("liked") else LikeStatus.INDIFFERENT
    c.rate_song(str(args["video_id"]), rating)
    return None


def _add_history(c: Any, args: dict[str, Any]) -> Any:
    """Add a play to the account's history: the ping YouTube Music's own player sends
    when a song starts. The URL comes from the song's player response and must be called
    by the same client that fetched it."""
    response = c.add_history_item(c.get_song(str(args["video_id"])))
    if response.status_code != 204:
        raise ServiceError(ApiErrorKind.SERVER, f"history: HTTP {response.status_code}")
    return None


def _radio(c: Any, args: dict[str, Any]) -> Any:
    raw = c.get_watch_playlist(
        videoId=str(args["video_id"]), radio=True, limit=int(args.get("limit") or RADIO_LIMIT)
    )
    return [codec.track_to_json(t) for t in parse.tracks(raw.get("tracks"))]


def _search(c: Any, args: dict[str, Any]) -> Any:
    query = str(args["query"])
    filter_ = args.get("filter") or None
    raw = c.search(query, filter=filter_, limit=int(args.get("limit") or SEARCH_LIMIT))
    return codec.search_results_to_json(parse.search_results(raw, query, filter_))


def _search_suggestions(c: Any, args: dict[str, Any]) -> Any:
    found = c.get_search_suggestions(str(args["query"]))
    return [s for s in found if isinstance(s, str)][:SUGGESTIONS]


def _album(c: Any, args: dict[str, Any]) -> Any:
    browse_id = str(args["browse_id"])
    return codec.album_to_json(parse.album(c.get_album(browse_id), browse_id))


def _artist(c: Any, args: dict[str, Any]) -> Any:
    channel_id = str(args["channel_id"])
    return codec.artist_to_json(parse.artist(c.get_artist(channel_id), channel_id))


_OPERATIONS: dict[str, Callable[[Any, dict[str, Any]], Any]] = {
    "account_info": _account_info,
    "library_playlists": _library_playlists,
    "library_albums": _library_albums,
    "library_artists": _library_artists,
    "playlist": _playlist,
    "rate_song": _rate_song,
    "add_history": _add_history,
    "radio": _radio,
    "search": _search,
    "search_suggestions": _search_suggestions,
    "album": _album,
    "artist": _artist,
}
