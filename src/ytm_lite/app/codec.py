"""Compact JSON for the models, used for the disk cache and for worker -> app messages.

Short keys keep a 5,000-track list around 1 MB. Pure stdlib: the worker process
imports this too.
"""

from __future__ import annotations

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
    return {"id": a.id, "n": a.name, "s": a.subscribers, "th": a.thumbnail}


def artist_summary_from_json(d: Json) -> ArtistSummary:
    return ArtistSummary(d["id"], d["n"], d.get("s"), d.get("th"))


def account_to_json(a: Account) -> Json:
    return {"n": a.name, "h": a.handle, "p": a.photo}


def account_from_json(d: Json) -> Account:
    return Account(d["n"], d.get("h"), d.get("p"))
