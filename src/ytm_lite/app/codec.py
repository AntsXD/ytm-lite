"""Compact JSON for the models, used for the disk cache and for worker -> app messages.

Short keys keep a 5,000-track list around 1 MB. Pure stdlib: the worker process
imports this too.
"""

from __future__ import annotations

from typing import Any

from ytm_lite.app.models import (
    Account,
    Album,
    AlbumRef,
    AlbumSummary,
    Artist,
    ArtistRef,
    ArtistSummary,
    HomeShelf,
    LyricLine,
    Lyrics,
    Playlist,
    PlaylistSummary,
    SearchItem,
    SearchResults,
    Track,
)

Json = dict[str, Any]


def _refs(refs: tuple[ArtistRef, ...]) -> list[list[str | None]]:
    return [[r.name, r.id] for r in refs]


def _artists(raw: Any) -> tuple[ArtistRef, ...]:
    return tuple(ArtistRef(str(r[0]), r[1]) for r in raw or ())


def track_to_json(t: Track) -> Json:
    out: Json = {"v": t.video_id, "t": t.title}
    if t.artists:
        out["a"] = _refs(t.artists)
    if t.album:
        out["al"] = [t.album.name, t.album.id]
    if t.duration is not None:
        out["d"] = t.duration
    if t.thumbnail:
        out["th"] = t.thumbnail
    if not t.available:
        out["u"] = 1
    if t.explicit:
        out["e"] = 1
    if t.set_video_id:
        out["s"] = t.set_video_id
    return out


def track_from_json(d: Json) -> Track:
    album = d.get("al")
    return Track(
        video_id=d["v"],
        title=d["t"],
        artists=_artists(d.get("a")),
        album=AlbumRef(album[0], album[1]) if album else None,
        duration=d.get("d"),
        thumbnail=d.get("th"),
        available=not d.get("u"),
        explicit=bool(d.get("e")),
        set_video_id=d.get("s"),
    )


def playlist_to_json(p: Playlist) -> Json:
    return {
        "id": p.id,
        "t": p.title,
        "a": p.author,
        "de": p.description,
        "c": p.track_count,
        "th": p.thumbnail,
        "x": int(p.complete),
        "tr": [track_to_json(t) for t in p.tracks],
    }


def playlist_from_json(d: Json) -> Playlist:
    return Playlist(
        id=d["id"],
        title=d["t"],
        tracks=tuple(track_from_json(t) for t in d["tr"]),
        complete=bool(d.get("x", 1)),
        author=d.get("a"),
        description=d.get("de"),
        track_count=d.get("c"),
        thumbnail=d.get("th"),
    )


def playlist_summary_to_json(p: PlaylistSummary) -> Json:
    return {
        "id": p.id,
        "t": p.title,
        "c": p.count,
        "a": p.author,
        "th": p.thumbnail,
        "o": int(p.owned),
    }


def playlist_summary_from_json(d: Json) -> PlaylistSummary:
    return PlaylistSummary(d["id"], d["t"], d.get("c"), d.get("a"), d.get("th"), bool(d.get("o")))


def album_summary_to_json(a: AlbumSummary) -> Json:
    return {
        "id": a.id,
        "t": a.title,
        "a": _refs(a.artists),
        "y": a.year,
        "th": a.thumbnail,
        "k": a.kind,
    }


def album_summary_from_json(d: Json) -> AlbumSummary:
    return AlbumSummary(
        d["id"], d["t"], _artists(d.get("a")), d.get("y"), d.get("th"), d.get("k", "Album")
    )


def artist_summary_to_json(a: ArtistSummary) -> Json:
    return {"id": a.id, "n": a.name, "s": a.subscribers, "th": a.thumbnail, "au": a.audience}


def artist_summary_from_json(d: Json) -> ArtistSummary:
    return ArtistSummary(d["id"], d["n"], d.get("s"), d.get("th"), d.get("au"))


def account_to_json(a: Account) -> Json:
    return {"n": a.name, "h": a.handle, "p": a.photo}


def account_from_json(d: Json) -> Account:
    return Account(d["n"], d.get("h"), d.get("p"))


def album_to_json(a: Album) -> Json:
    return {
        "id": a.id,
        "t": a.title,
        "a": _refs(a.artists),
        "y": a.year,
        "k": a.kind,
        "th": a.thumbnail,
        "p": a.playlist_id,
        "tr": [track_to_json(t) for t in a.tracks],
    }


def album_from_json(d: Json) -> Album:
    return Album(
        id=d["id"],
        title=d["t"],
        tracks=tuple(track_from_json(t) for t in d["tr"]),
        artists=_artists(d.get("a")),
        year=d.get("y"),
        kind=d.get("k", "Album"),
        thumbnail=d.get("th"),
        playlist_id=d.get("p"),
    )


def artist_to_json(a: Artist) -> Json:
    return {
        "id": a.id,
        "n": a.name,
        "th": a.thumbnail,
        "s": a.subscribers,
        "top": [track_to_json(t) for t in a.top_songs],
        "al": [album_summary_to_json(x) for x in a.albums],
        "si": [album_summary_to_json(x) for x in a.singles],
        "p": a.songs_playlist_id,
        "au": a.audience,
    }


def artist_from_json(d: Json) -> Artist:
    return Artist(
        id=d["id"],
        name=d["n"],
        thumbnail=d.get("th"),
        subscribers=d.get("s"),
        top_songs=tuple(track_from_json(t) for t in d.get("top") or ()),
        albums=tuple(album_summary_from_json(x) for x in d.get("al") or ()),
        singles=tuple(album_summary_from_json(x) for x in d.get("si") or ()),
        songs_playlist_id=d.get("p"),
        audience=d.get("au"),
    )


def _item_to_json(item: SearchItem) -> Json:
    if isinstance(item, Track):
        return {"k": "song", "d": track_to_json(item)}
    if isinstance(item, AlbumSummary):
        return {"k": "album", "d": album_summary_to_json(item)}
    if isinstance(item, ArtistSummary):
        return {"k": "artist", "d": artist_summary_to_json(item)}
    return {"k": "playlist", "d": playlist_summary_to_json(item)}


def _item_from_json(d: Json) -> SearchItem:
    kind, data = d["k"], d["d"]
    if kind == "song":
        return track_from_json(data)
    if kind == "album":
        return album_summary_from_json(data)
    if kind == "artist":
        return artist_summary_from_json(data)
    return playlist_summary_from_json(data)


def home_to_json(shelves: tuple[HomeShelf, ...]) -> list[Json]:
    return [{"t": s.title, "i": [_item_to_json(i) for i in s.items]} for s in shelves]


def home_from_json(d: list[Json] | None) -> tuple[HomeShelf, ...]:
    return tuple(HomeShelf(s["t"], tuple(_item_from_json(i) for i in s["i"])) for s in d or ())


def lyrics_to_json(lyrics: Lyrics | None) -> Json | None:
    if lyrics is None:
        return None
    return {
        "t": lyrics.text,
        "l": [[line.start_ms, line.end_ms, line.text] for line in lyrics.lines],
        "s": lyrics.source,
    }


def lyrics_from_json(d: Json | None) -> Lyrics | None:
    if not d:
        return None
    lines = tuple(LyricLine(int(a), int(b), str(text)) for a, b, text in d.get("l") or ())
    return Lyrics(text=d.get("t") or "", lines=lines, source=d.get("s"))


def search_results_to_json(r: SearchResults) -> Json:
    return {
        "q": r.query,
        "f": r.filter,
        "top": _item_to_json(r.top) if r.top is not None else None,
        "so": [track_to_json(t) for t in r.songs],
        "al": [album_summary_to_json(a) for a in r.albums],
        "ar": [artist_summary_to_json(a) for a in r.artists],
        "pl": [playlist_summary_to_json(p) for p in r.playlists],
    }


def search_results_from_json(d: Json) -> SearchResults:
    top = d.get("top")
    return SearchResults(
        query=d["q"],
        filter=d.get("f"),
        top=_item_from_json(top) if top else None,
        songs=tuple(track_from_json(t) for t in d.get("so") or ()),
        albums=tuple(album_summary_from_json(a) for a in d.get("al") or ()),
        artists=tuple(artist_summary_from_json(a) for a in d.get("ar") or ()),
        playlists=tuple(playlist_summary_from_json(p) for p in d.get("pl") or ()),
    )
