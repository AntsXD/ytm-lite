"""Slim, immutable data models. Raw API JSON is converted into these and then dropped."""

from __future__ import annotations

from dataclasses import dataclass

LIKED_SONGS_ID = "LM"


@dataclass(frozen=True, slots=True)
class ArtistRef:
    name: str
    id: str | None = None


@dataclass(frozen=True, slots=True)
class AlbumRef:
    name: str
    id: str | None = None


@dataclass(frozen=True, slots=True)
class Track:
    video_id: str
    title: str
    artists: tuple[ArtistRef, ...] = ()
    album: AlbumRef | None = None
    duration: int | None = None  # seconds
    thumbnail: str | None = None  # largest available; resized on request
    available: bool = True
    explicit: bool = False
    set_video_id: str | None = None  # this entry's id within a playlist

    @property
    def artist_text(self) -> str:
        return ", ".join(a.name for a in self.artists)

    @property
    def link(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}"


@dataclass(frozen=True, slots=True)
class Account:
    name: str
    handle: str | None = None
    photo: str | None = None


@dataclass(frozen=True, slots=True)
class PlaylistSummary:
    id: str
    title: str
    count: int | None = None
    author: str | None = None
    thumbnail: str | None = None
    owned: bool = False


@dataclass(frozen=True, slots=True)
class Playlist:
    id: str
    title: str
    tracks: tuple[Track, ...]
    complete: bool  # False while only the first page is loaded
    author: str | None = None
    description: str | None = None
    track_count: int | None = None
    thumbnail: str | None = None

    @property
    def summary(self) -> PlaylistSummary:
        return PlaylistSummary(self.id, self.title, self.track_count, self.author, self.thumbnail)


@dataclass(frozen=True, slots=True)
class AlbumSummary:
    id: str  # browseId (MPRE...)
    title: str
    artists: tuple[ArtistRef, ...] = ()
    year: str | None = None
    thumbnail: str | None = None
    kind: str = "Album"

    @property
    def artist_text(self) -> str:
        return ", ".join(a.name for a in self.artists)


@dataclass(frozen=True, slots=True)
class ArtistSummary:
    id: str  # channel/browse id
    name: str
    subscribers: str | None = None
    thumbnail: str | None = None
    audience: str | None = None  # monthly audience (a search's top-result card)


@dataclass(frozen=True, slots=True)
class Album:
    id: str  # browseId (MPRE...)
    title: str
    tracks: tuple[Track, ...]
    artists: tuple[ArtistRef, ...] = ()
    year: str | None = None
    kind: str = "Album"  # "Album", "Single", "EP"
    thumbnail: str | None = None
    playlist_id: str | None = None  # the album as a playlist (OLAK5uy_...)

    @property
    def artist_text(self) -> str:
        return ", ".join(a.name for a in self.artists)


@dataclass(frozen=True, slots=True)
class Artist:
    id: str  # channel id
    name: str
    thumbnail: str | None = None
    subscribers: str | None = None
    top_songs: tuple[Track, ...] = ()
    albums: tuple[AlbumSummary, ...] = ()
    singles: tuple[AlbumSummary, ...] = ()
    songs_playlist_id: str | None = None  # all of the artist's songs, as a playlist
    audience: str | None = None  # monthly audience


SearchItem = Track | AlbumSummary | ArtistSummary | PlaylistSummary


@dataclass(frozen=True, slots=True)
class SearchResults:
    query: str
    filter: str | None = None  # None: everything ("All"); else songs/albums/artists/playlists
    top: SearchItem | None = None
    songs: tuple[Track, ...] = ()
    albums: tuple[AlbumSummary, ...] = ()
    artists: tuple[ArtistSummary, ...] = ()
    playlists: tuple[PlaylistSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class HomeShelf:
    title: str
    items: tuple[SearchItem, ...]


@dataclass(frozen=True, slots=True)
class LyricLine:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class Lyrics:
    """Synced (`lines`, with times) when YouTube has them, otherwise plain `text`."""

    text: str = ""
    lines: tuple[LyricLine, ...] = ()
    source: str | None = None  # "Source: LyricFind"

    @property
    def synced(self) -> bool:
        return bool(self.lines)


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "–:––"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
