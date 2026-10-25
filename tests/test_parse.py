"""Parsers against ytmusicapi-shaped dicts (shapes from ytmusicapi 1.12 docstrings)."""

from __future__ import annotations

from typing import Any

from ytm_lite.app import parse
from ytm_lite.app.models import AlbumRef, ArtistRef, ArtistSummary, Track

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


def test_watch_playlist_track() -> None:
    """Radio tracks name their duration "length" and thumbnails "thumbnail"."""
    raw = {
        "videoId": "9mWr4c_ig54",
        "title": "Foolish Of Me",
        "length": "3:07",
        "thumbnail": THUMBS,
        "artists": [{"name": "Seven Lions", "id": "UCY"}],
        "album": {"name": "Foolish Of Me", "id": "MPREb_C8"},
        "videoType": "MUSIC_VIDEO_TYPE_ATV",
    }
    track = parse.track(raw)
    assert track is not None
    assert track.duration == 187
    assert track.thumbnail == THUMBS[-1]["url"]
    assert track.artist_text == "Seven Lions"


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


SEARCH_ALL: list[dict[str, Any]] = [
    {
        "category": "Top result",
        "resultType": "video",
        "videoId": "vU05Eksc_iM",
        "title": "Wonderwall",
        "artists": [{"name": "Oasis", "id": "UCm"}],
        "duration": "4:38",
        "duration_seconds": 278,
    },
    {
        "category": "Songs",
        "resultType": "song",
        "videoId": "ZrOKjDZOtkA",
        "title": "Wonderwall",
        "artists": [{"name": "Oasis", "id": "UCm"}],
        "album": {"name": "Morning Glory", "id": "MPREb_9n"},
        "duration_seconds": 259,
        "thumbnails": THUMBS,
    },
    {"category": "Videos", "resultType": "video", "videoId": "bx1", "title": "Wonderwall (MV)"},
    {
        "category": "Albums",
        "resultType": "album",
        "browseId": "MPREb_IIn",
        "title": "Morning Glory",
        "type": "Album",
        "artist": "Oasis",
        "year": "1995",
    },
    {
        "category": "Community playlists",
        "resultType": "playlist",
        "browseId": "VLPLK1",
        "title": "Wonderwall - Oasis",
        "author": "Tate",
        "itemCount": "174",
    },
    {"category": "Artists", "resultType": "artist", "browseId": "UCm", "artist": "Oasis"},
    {"category": "Profiles", "resultType": "profile", "title": "Fan", "browseId": "UCx"},
]


def test_search_results_sort_kinds_and_keep_the_top_result() -> None:
    results = parse.search_results(SEARCH_ALL, "wonderwall", None)
    assert isinstance(results.top, Track) and results.top.video_id == "vU05Eksc_iM"
    assert [t.video_id for t in results.songs] == ["ZrOKjDZOtkA", "bx1"]  # videos last
    assert results.albums[0].artists == (ArtistRef("Oasis"),)
    assert results.playlists[0].id == "PLK1" and results.playlists[0].author == "Tate"
    assert results.playlists[0].count == 174
    assert results.artists[0].name == "Oasis"


def test_album_tracks_take_the_album_and_its_cover() -> None:
    raw = {
        "title": "Revival",
        "type": "Album",
        "thumbnails": THUMBS,
        "artists": [{"name": "Eminem", "id": "UCe"}],
        "year": "2017",
        "audioPlaylistId": "OLAK5uy_n",
        "tracks": [
            {"videoId": "a", "title": "One", "album": "Revival", "thumbnails": None, "artists": []},
            {"videoId": "b", "title": "Two", "duration": "3:00", "isAvailable": False},
        ],
    }
    album = parse.album(raw, "MPREb_x")
    assert [t.title for t in album.tracks] == ["One", "Two"]
    assert all(t.album == AlbumRef("Revival", "MPREb_x") for t in album.tracks)
    assert all(t.thumbnail == THUMBS[-1]["url"] for t in album.tracks)
    assert album.tracks[0].artists == (ArtistRef("Eminem", "UCe"),)
    assert not album.tracks[1].available
    assert (album.year, album.kind, album.playlist_id) == ("2017", "Album", "OLAK5uy_n")


def test_artist_page() -> None:
    raw = {
        "name": "Oasis",
        "channelId": "UCdifferent",
        "subscribers": "3.86M",
        "monthlyListeners": "21.4M",
        "thumbnails": THUMBS,
        "songs": {
            "browseId": "VLPLMp",
            "results": [{"videoId": "z", "title": "Wonderwall", "artists": [{"name": "Oasis"}]}],
        },
        "albums": {"results": [{"title": "Familiar", "year": "2018", "browseId": "MPREb_A"}]},
        "singles": {"results": [{"title": "Stand By Me", "browseId": "MPREb_7"}]},
    }
    artist = parse.artist(raw, "UCm")
    assert artist.id == "UCm" and artist.songs_playlist_id == "PLMp"
    assert (artist.subscribers, artist.audience) == ("3.86M", "21.4M")
    assert [t.video_id for t in artist.top_songs] == ["z"]
    assert artist.albums[0].artists == (ArtistRef("Oasis", "UCm"),)
    assert artist.albums[0].kind == "Album" and artist.singles[0].kind == "Single"


def test_top_result_card_artist_and_its_songs() -> None:
    raw = [
        {
            "category": "Top result",
            "resultType": "artist",
            "subscribers": "67.8M",
            "artists": [{"name": "Daft Punk", "id": "UCRr"}],
        },
        {"resultType": "song", "videoId": "k1", "title": "Instant Crush", "duration": "5:38"},
        {
            "resultType": "song",
            "videoId": "k2",
            "title": "Crescendolls",
            "artists": [{"name": "X"}],
        },
    ]
    results = parse.search_results(raw, "daft punk", None)
    top = results.top
    assert isinstance(top, ArtistSummary) and top.id == "UCRr"
    # The card's number is the monthly audience, whatever ytmusicapi calls it.
    assert (top.subscribers, top.audience) == (None, "67.8M")
    assert results.songs[0].artists == (ArtistRef("Daft Punk", "UCRr"),)
    assert results.songs[1].artists == (ArtistRef("X"),)
