"""Convert ytmusicapi's dicts into the app's models.

Everything here tolerates missing keys: YouTube changes its layouts, and one odd item
should cost one row, not the whole list.
"""

from __future__ import annotations

import re
from typing import Any

from ytm_lite.app.models import (
    Account,
    AlbumRef,
    AlbumSummary,
    ArtistRef,
    ArtistSummary,
    Playlist,
    PlaylistSummary,
    Track,
)

Raw = dict[str, Any]


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def to_int(value: Any) -> int | None:
    """'1,234 songs' -> 1234."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = re.sub(r"\D", "", value)
        return int(digits) if digits else None
    return None


def parse_duration(value: Any) -> int | None:
    """'3:45' or '1:02:03' -> seconds."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d+(:\d{1,2}){1,2}", value.strip()):
        return None
    seconds = 0
    for part in value.strip().split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def thumbnail(raw: Any) -> str | None:
    """URL of the largest thumbnail (ytmusicapi lists them smallest first)."""
    if isinstance(raw, list) and raw:
        last = raw[-1]
        if isinstance(last, dict):
            return _str(last.get("url"))
    return None


def artists(raw: Any) -> tuple[ArtistRef, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        ArtistRef(name, _str(a.get("id")))
        for a in raw
        if isinstance(a, dict) and (name := _str(a.get("name")))
    )


def album_ref(raw: Any) -> AlbumRef | None:
    if isinstance(raw, dict) and (name := _str(raw.get("name"))):
        return AlbumRef(name, _str(raw.get("id")))
    return None


def track(raw: Raw) -> Track | None:
    video_id = _str(raw.get("videoId"))
    if video_id is None:
        return None
    return Track(
        video_id=video_id,
        title=_str(raw.get("title")) or "Untitled",
        artists=artists(raw.get("artists")),
        album=album_ref(raw.get("album")),
        duration=to_int(raw.get("duration_seconds")) or parse_duration(raw.get("duration")),
        thumbnail=thumbnail(raw.get("thumbnails")),
        available=raw.get("isAvailable", True) is not False,
        explicit=bool(raw.get("isExplicit")),
        set_video_id=_str(raw.get("setVideoId")),
    )


def tracks(raw: Any) -> tuple[Track, ...]:
    if not isinstance(raw, list):
        return ()
    parsed = (track(item) for item in raw if isinstance(item, dict))
    return tuple(t for t in parsed if t is not None)


def account(raw: Raw) -> Account:
    return Account(
        name=_str(raw.get("accountName")) or "YouTube Music user",
        handle=_str(raw.get("channelHandle")),
        photo=_str(raw.get("accountPhotoUrl")),
    )


def playlist_summary(raw: Raw) -> PlaylistSummary | None:
    playlist_id = _str(raw.get("playlistId"))
    if playlist_id is None:
        return None
    author = artists(raw.get("author"))
    return PlaylistSummary(
        id=playlist_id,
        title=_str(raw.get("title")) or "Untitled playlist",
        count=to_int(raw.get("count")),
        author=", ".join(a.name for a in author) or None,
        thumbnail=thumbnail(raw.get("thumbnails")),
        owned=bool(raw.get("owned")),
    )


def playlist(raw: Raw, playlist_id: str, requested: int | None) -> Playlist:
    items = tracks(raw.get("tracks"))
    count = to_int(raw.get("trackCount"))
    author = raw.get("author")
    author_name = _str(author.get("name")) if isinstance(author, dict) else None
    if requested is None:
        complete = True
    else:
        complete = len(items) < requested or (count is not None and len(items) >= count)
    return Playlist(
        id=_str(raw.get("id")) or playlist_id,
        title=_str(raw.get("title")) or "Untitled playlist",
        tracks=items,
        complete=complete,
        author=author_name,
        description=_str(raw.get("description")),
        track_count=count,
        thumbnail=thumbnail(raw.get("thumbnails")),
    )


def album_summary(raw: Raw) -> AlbumSummary | None:
    browse_id = _str(raw.get("browseId"))
    if browse_id is None:
        return None
    return AlbumSummary(
        id=browse_id,
        title=_str(raw.get("title")) or "Untitled",
        artists=artists(raw.get("artists")),
        year=_str(raw.get("year")),
        thumbnail=thumbnail(raw.get("thumbnails")),
        kind=_str(raw.get("type")) or "Album",
    )


def artist_summary(raw: Raw) -> ArtistSummary | None:
    browse_id = _str(raw.get("browseId"))
    name = _str(raw.get("artist"))
    if browse_id is None or name is None:
        return None
    return ArtistSummary(
        id=browse_id,
        name=name,
        subscribers=_str(raw.get("subscribers")),
        thumbnail=thumbnail(raw.get("thumbnails")),
    )
