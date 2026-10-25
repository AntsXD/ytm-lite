"""Convert ytmusicapi's dicts into the app's models.

Everything here tolerates missing keys: YouTube changes its layouts, and one odd item
should cost one row, not the whole list.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from ytm_lite.app.models import (
    Account,
    Album,
    AlbumRef,
    AlbumSummary,
    Artist,
    ArtistRef,
    ArtistSummary,
    Playlist,
    PlaylistSummary,
    SearchItem,
    SearchResults,
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
    """A playlist/album track, or a watch-playlist (radio) track, which names its
    duration "length" and its thumbnails "thumbnail"."""
    video_id = _str(raw.get("videoId"))
    if video_id is None:
        return None
    return Track(
        video_id=video_id,
        title=_str(raw.get("title")) or "Untitled",
        artists=artists(raw.get("artists")),
        album=album_ref(raw.get("album")),
        duration=to_int(raw.get("duration_seconds"))
        or parse_duration(raw.get("duration") or raw.get("length")),
        thumbnail=thumbnail(raw.get("thumbnails") or raw.get("thumbnail")),
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
    """A library playlist, or a search result (which has a "VL" browseId instead of a
    playlistId, a plain-text author and an itemCount)."""
    playlist_id = _str(raw.get("playlistId"))
    if playlist_id is None and (browse_id := _str(raw.get("browseId"))):
        playlist_id = browse_id.removeprefix("VL")
    if playlist_id is None:
        return None
    author = raw.get("author")
    author_text = author if isinstance(author, str) else ", ".join(a.name for a in artists(author))
    return PlaylistSummary(
        id=playlist_id,
        title=_str(raw.get("title")) or "Untitled playlist",
        count=to_int(raw.get("count")) or to_int(raw.get("itemCount")),
        author=author_text or None,
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
    refs = artists(raw.get("artists"))
    if not refs and (name := _str(raw.get("artist"))):  # default search: plain text
        refs = (ArtistRef(name),)
    return AlbumSummary(
        id=browse_id,
        title=_str(raw.get("title")) or "Untitled",
        artists=refs,
        year=_str(raw.get("year")),
        thumbnail=thumbnail(raw.get("thumbnails")),
        kind=_str(raw.get("type")) or "Album",
    )


def artist_summary(raw: Raw) -> ArtistSummary | None:
    browse_id = _str(raw.get("browseId"))
    name = _str(raw.get("artist")) or _str(raw.get("title")) or _str(raw.get("name"))
    refs = artists(raw.get("artists"))
    if refs and refs[0].id:  # a search's top-result card names its artist this way
        browse_id = browse_id or refs[0].id
        name = name or refs[0].name
    if browse_id is None or name is None:
        return None
    return ArtistSummary(
        id=browse_id,
        name=name,
        subscribers=_str(raw.get("subscribers")),
        thumbnail=thumbnail(raw.get("thumbnails")),
    )


def album(raw: Raw, browse_id: str) -> Album:
    """An album page. Its tracks name the album as plain text and usually have no
    thumbnail of their own: both come from the album."""
    title = _str(raw.get("title")) or "Untitled"
    cover = thumbnail(raw.get("thumbnails"))
    ref = AlbumRef(title, browse_id)
    refs = artists(raw.get("artists"))
    album_tracks = tuple(
        dataclasses.replace(t, album=ref, thumbnail=t.thumbnail or cover, artists=t.artists or refs)
        for t in tracks(raw.get("tracks"))
    )
    return Album(
        id=browse_id,
        title=title,
        tracks=album_tracks,
        artists=refs,
        year=_str(raw.get("year")),
        kind=_str(raw.get("type")) or "Album",
        thumbnail=cover,
        playlist_id=_str(raw.get("audioPlaylistId")),
    )


def _shelf(raw: Raw, key: str) -> list[Any]:
    shelf = raw.get(key)
    if isinstance(shelf, dict) and isinstance(shelf.get("results"), list):
        return list(shelf["results"])
    return []


def artist(raw: Raw, channel_id: str) -> Artist:
    """An artist page: top songs, albums and singles. `channel_id` is the id the page
    was opened with (the response's own channelId is a different one)."""
    name = _str(raw.get("name")) or "Unknown artist"
    me = (ArtistRef(name, channel_id),)

    def releases(key: str, kind: str) -> tuple[AlbumSummary, ...]:
        found = []
        for item in _shelf(raw, key):
            summary = album_summary(item) if isinstance(item, dict) else None
            if summary is not None:
                found.append(
                    dataclasses.replace(
                        summary,
                        artists=summary.artists or me,
                        kind=_str(item.get("type")) or kind,
                    )
                )
        return tuple(found)

    songs = raw.get("songs")
    songs_id = _str(songs.get("browseId")) if isinstance(songs, dict) else None
    return Artist(
        id=channel_id,
        name=name,
        thumbnail=thumbnail(raw.get("thumbnails")),
        subscribers=_str(raw.get("subscribers")),
        top_songs=tracks(_shelf(raw, "songs")),
        albums=releases("albums", "Album"),
        singles=releases("singles", "Single"),
        songs_playlist_id=songs_id.removeprefix("VL") if songs_id else None,
        audience=_str(raw.get("monthlyListeners")),
    )


def search_item(raw: Raw) -> SearchItem | None:
    kind = raw.get("resultType")
    if kind in ("song", "video"):
        return track(raw)
    if kind == "album":
        return album_summary(raw)
    if kind == "artist":
        return artist_summary(raw)
    if kind == "playlist":
        return playlist_summary(raw)
    return None  # podcasts, episodes, profiles: not shown


def search_results(raw: Any, query: str, filter_: str | None) -> SearchResults:
    """Default ("All") search mixes kinds, with a "Top result" first; a filtered search
    returns one kind."""
    top: SearchItem | None = None
    songs: list[Track] = []
    videos: list[Track] = []
    albums: list[AlbumSummary] = []
    found_artists: list[ArtistSummary] = []
    playlists: list[PlaylistSummary] = []
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue
        parsed = search_item(item)
        if parsed is None:
            continue
        if top is None and item.get("category") == "Top result":
            if isinstance(parsed, ArtistSummary):
                # ytmusicapi files the card's number as "subscribers", but YouTube labels
                # it "monthly audience" (ytmusicapi keeps only the number).
                parsed = dataclasses.replace(parsed, subscribers=None, audience=parsed.subscribers)
            top = parsed
            continue
        if isinstance(parsed, Track):
            if not parsed.artists and isinstance(top, ArtistSummary):
                # Songs on the top result's card leave out the artist: it's the card's.
                parsed = dataclasses.replace(parsed, artists=(ArtistRef(top.name, top.id),))
            (videos if item.get("resultType") == "video" else songs).append(parsed)
        elif isinstance(parsed, AlbumSummary):
            albums.append(parsed)
        elif isinstance(parsed, ArtistSummary):
            found_artists.append(parsed)
        else:
            playlists.append(parsed)
    return SearchResults(
        query=query,
        filter=filter_,
        top=top,
        songs=tuple(songs + videos),  # music videos play like songs
        albums=tuple(albums),
        artists=tuple(found_artists),
        playlists=tuple(playlists),
    )
