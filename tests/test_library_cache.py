from __future__ import annotations

import os
from pathlib import Path

from ytm_lite.app.library_cache import MAX_PLAYLISTS, LibraryCache
from ytm_lite.app.models import (
    AlbumRef,
    AlbumSummary,
    ArtistRef,
    ArtistSummary,
    Playlist,
    PlaylistSummary,
    Track,
)

TRACKS = (
    Track(
        "a",
        "A",
        (ArtistRef("X", "UCx"),),
        AlbumRef("Alb", "MPRE"),
        100,
        "https://t/a",
        True,
        True,
        "s1",
    ),
    Track("b", "B", available=False),
)


def test_lists_roundtrip(tmp_path: Path) -> None:
    cache = LibraryCache(tmp_path)
    assert cache.load_playlists() is None
    playlists = [PlaylistSummary("PL1", "Mine", 5, "me", "https://t/p", True)]
    albums = [AlbumSummary("MPRE1", "Alb", (ArtistRef("X", "UCx"),), "2020", "https://t/al", "EP")]
    artists = [ArtistSummary("UC1", "Artist", "1K", "https://t/ar")]
    cache.save_playlists(playlists)
    cache.save_albums(albums)
    cache.save_artists(artists)
    assert cache.load_playlists() == playlists
    assert cache.load_albums() == albums
    assert cache.load_artists() == artists


def test_playlist_roundtrip_and_partial_skipped(tmp_path: Path) -> None:
    cache = LibraryCache(tmp_path)
    playlist = Playlist("PL/1", "Mix", TRACKS, True, "me", "desc", 2, "https://t/p")
    cache.save_playlist(playlist)
    assert cache.load_playlist("PL/1") == playlist
    cache.save_playlist(Playlist("PL2", "Partial", TRACKS, False))
    assert cache.load_playlist("PL2") is None


def test_prune_keeps_recent_playlists_and_liked_songs(tmp_path: Path) -> None:
    cache = LibraryCache(tmp_path)
    cache.save_playlist(Playlist("LM", "Liked", TRACKS, True))
    for i in range(MAX_PLAYLISTS + 3):
        cache.save_playlist(Playlist(f"PL{i}", "x", TRACKS, True))
        path = tmp_path / f"playlist-PL{i}.json"
        os.utime(path, (1000 + i, 1000 + i))
    cache.save_playlist(Playlist("PLlast", "x", TRACKS, True))
    remaining = {p.name for p in tmp_path.glob("playlist-*.json")}
    assert "playlist-LM.json" in remaining
    assert "playlist-PL0.json" not in remaining
    assert len(remaining) == MAX_PLAYLISTS + 1


def test_version_mismatch_and_clear(tmp_path: Path) -> None:
    cache = LibraryCache(tmp_path)
    (tmp_path / "playlists.json").write_text('{"version": 999, "data": []}', "utf-8")
    assert cache.load_playlists() is None
    cache.save_artists([])
    cache.clear()
    assert list(tmp_path.glob("*.json")) == []
