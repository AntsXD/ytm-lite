"""Disk cache of library lists (stale-while-revalidate): shown at startup, then refreshed.

Methods here do file I/O, so call them from the io pool.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from ytm_lite.app import codec
from ytm_lite.app.config import atomic_write_text, read_json
from ytm_lite.app.models import (
    LIKED_SONGS_ID,
    AlbumSummary,
    ArtistSummary,
    Playlist,
    PlaylistSummary,
)

log = logging.getLogger(__name__)
T = TypeVar("T")

FORMAT_VERSION = 1
MAX_PLAYLISTS = 30  # recently opened playlists kept on disk


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)


class LibraryCache:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def _read(self, name: str) -> Any:
        data = read_json(self.folder / f"{name}.json")
        if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
            return None
        return data.get("data")

    def _write(self, name: str, data: Any) -> None:
        payload = json.dumps({"version": FORMAT_VERSION, "data": data}, separators=(",", ":"))
        atomic_write_text(self.folder / f"{name}.json", payload)

    def _read_list(self, name: str, decode: Callable[[dict[str, Any]], T]) -> list[T] | None:
        raw = self._read(name)
        if not isinstance(raw, list):
            return None
        try:
            return [decode(item) for item in raw]
        except (KeyError, TypeError, IndexError):
            log.warning("cached %s is damaged; ignoring it", name)
            return None

    # -- lists ------------------------------------------------------------------------

    def load_playlists(self) -> list[PlaylistSummary] | None:
        return self._read_list("playlists", codec.playlist_summary_from_json)

    def save_playlists(self, items: list[PlaylistSummary]) -> None:
        self._write("playlists", [codec.playlist_summary_to_json(p) for p in items])

    def load_albums(self) -> list[AlbumSummary] | None:
        return self._read_list("albums", codec.album_summary_from_json)

    def save_albums(self, items: list[AlbumSummary]) -> None:
        self._write("albums", [codec.album_summary_to_json(a) for a in items])

    def load_artists(self) -> list[ArtistSummary] | None:
        return self._read_list("artists", codec.artist_summary_from_json)

    def save_artists(self, items: list[ArtistSummary]) -> None:
        self._write("artists", [codec.artist_summary_to_json(a) for a in items])

    # -- playlists ---------------------------------------------------------------------

    def load_playlist(self, playlist_id: str) -> Playlist | None:
        raw = self._read(f"playlist-{_safe(playlist_id)}")
        if raw is None:
            return None
        try:
            return codec.playlist_from_json(raw)
        except (KeyError, TypeError, IndexError):
            log.warning("cached playlist %s is damaged; ignoring it", playlist_id)
            return None

    def save_playlist(self, playlist: Playlist) -> None:
        """Only complete playlists are cached."""
        if not playlist.complete:
            return
        self._write(f"playlist-{_safe(playlist.id)}", codec.playlist_to_json(playlist))
        self._prune()

    def _prune(self) -> None:
        keep = f"playlist-{LIKED_SONGS_ID}.json"
        files = sorted(
            (p for p in self.folder.glob("playlist-*.json") if p.name != keep),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[MAX_PLAYLISTS:]:
            with contextlib.suppress(OSError):
                old.unlink()

    def clear(self) -> None:
        for path in self.folder.glob("*.json"):
            with contextlib.suppress(OSError):
                path.unlink()
