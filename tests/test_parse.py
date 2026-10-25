"""Parsers against ytmusicapi-shaped dicts (shapes from ytmusicapi 1.12 docstrings)."""

from __future__ import annotations

from typing import Any

from ytm_lite.app import parse
from ytm_lite.app.models import AlbumRef, ArtistRef

THUMBS = [
    {"url": "https://lh3.googleusercontent.com/x=w60-h60-l90-rj", "width": 60, "height": 60},
    {"url": "https://lh3.googleusercontent.com/x=w544-h544-l90-rj", "width": 544, "height": 544},
]

TRACK: dict[str, Any] = {
    "videoId": "bjGppZKiuFE",
    "title": "Lost",
    "artists": [{"name": "Guest Who", "id": "UCk"}, {"name": "Kate Wild", "id": None}],
    "album": {"name": "Lost", "id": "MPREb_Px"},
    "duration": "2:58",
    "duration_seconds": 178,
    "setVideoId": "748EE8",
    "likeStatus": "INDIFFERENT",
    "thumbnails": THUMBS,
    "isAvailable": True,
    "isExplicit": False,
}


def test_track() -> None:
    track = parse.track(TRACK)
    assert track is not None
    assert track.video_id == "bjGppZKiuFE"
    assert track.artists == (ArtistRef("Guest Who", "UCk"), ArtistRef("Kate Wild", None))
    assert track.album == AlbumRef("Lost", "MPREb_Px")
    assert track.duration == 178
    assert track.thumbnail == THUMBS[-1]["url"]
    assert track.set_video_id == "748EE8"
    assert track.available


def test_track_tolerates_missing_and_odd_fields() -> None:
    assert parse.track({"title": "no id"}) is None
    track = parse.track(
        {"videoId": "v", "duration": "1:02:03", "isAvailable": False, "album": None}
    )
    assert track is not None
    assert track.title == "Untitled"
    assert track.duration == 3723
    assert not track.available
    assert track.album is None and track.artists == ()


def test_tracks_skips_garbage() -> None:
    assert len(parse.tracks([TRACK, None, "x", {"title": "no id"}, TRACK])) == 2
    assert parse.tracks(None) == ()


def test_playlist_completeness() -> None:
    raw = {"id": "PL1", "title": "Mix", "trackCount": 237, "tracks": [TRACK] * 100}
    first_page = parse.playlist(raw, "PL1", requested=100)
    assert not first_page.complete and first_page.track_count == 237
    small = parse.playlist({"title": "Small", "tracks": [TRACK] * 5}, "PL2", requested=100)
    assert small.complete and small.id == "PL2"
    full = parse.playlist(raw, "PL1", requested=None)
    assert full.complete


def test_library_items() -> None:
    playlist = parse.playlist_summary(
        {
            "playlistId": "PL1",
            "title": "Mine",
            "count": "1,234",
            "owned": True,
            "thumbnails": THUMBS,
        }
    )
    assert playlist is not None and playlist.count == 1234 and playlist.owned
    album = parse.album_summary(
        {
            "browseId": "MPRE1",
            "title": "Beautiful",
            "type": "Album",
            "year": "2015",
            "artists": [{"name": "Project 46", "id": "UCX"}],
        }
    )
    assert album is not None and album.artist_text == "Project 46" and album.year == "2015"
    artist = parse.artist_summary(
        {"browseId": "UC1", "artist": "WildVibes", "subscribers": "2.91K"}
    )
    assert artist is not None and artist.subscribers == "2.91K"
    assert parse.artist_summary({"browseId": "UC1"}) is None
    account = parse.account(
        {"accountName": "Sample", "channelHandle": "@s", "accountPhotoUrl": None}
    )
    assert account.name == "Sample" and account.photo is None


def test_numbers() -> None:
    assert parse.to_int("5 songs") == 5
    assert parse.to_int(None) is None
    assert parse.parse_duration("3:05") == 185
    assert parse.parse_duration("live") is None
