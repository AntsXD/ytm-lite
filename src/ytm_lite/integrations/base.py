"""The media-controls interface (QtCore only, so the platform modules stay light)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ytm_lite.app.models import Track
from ytm_lite.app.playback import PlaybackState


class MediaControls(QObject):
    """Base class, and the do-nothing implementation."""

    play = Signal()
    pause = Signal()
    play_pause = Signal()
    next = Signal()
    previous = Signal()
    seek = Signal(float)  # seconds
    raise_window = Signal()  # MPRIS "Raise"

    name = "none"

    def update_track(self, track: Track | None) -> None:
        """A new track (or none): title, artist, album. The cover follows separately."""

    def update_cover(self, jpeg: bytes) -> None:
        """The current track's cover, as JPEG bytes."""

    def update_state(self, state: PlaybackState) -> None:
        pass

    def update_timeline(self, position: float, duration: float) -> None:
        pass

    def close(self) -> None:
        pass
