"""Slim, immutable data models. Raw API JSON is converted into these and then dropped."""

from __future__ import annotations

from dataclasses import dataclass


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
    thumbnail: str | None = None
    available: bool = True

    @property
    def artist_text(self) -> str:
        return ", ".join(a.name for a in self.artists)

    @property
    def link(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "–:––"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
