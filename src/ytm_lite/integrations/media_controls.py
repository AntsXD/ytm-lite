"""OS media controls: what's playing in the OS overlay, and media keys / overlay buttons
routed to the playback controller.

- Windows: System Media Transport Controls (the volume flyout), set up the first time
  something plays, so an idle app in the tray doesn't pay for the WinRT runtime. If SMTC
  can't start, the media keys are registered as hotkeys instead (no overlay).
- Linux: MPRIS on the session bus.
- Elsewhere, or when both fail: nothing.

The timeline is updated on events (play, pause, seek, a new track), never on a timer.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject
from PySide6.QtGui import QPixmap

from ytm_lite.app.images import ImageLoader, image_key
from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackController, PlaybackState
from ytm_lite.integrations.base import MediaControls

__all__ = ["MediaBridge", "MediaControls", "create_media_controls", "jpeg_bytes"]

log = logging.getLogger(__name__)

COVER_PX = 300


def create_media_controls(parent: QObject | None = None) -> MediaControls:
    if sys.platform == "win32":
        try:
            from ytm_lite.integrations.windows_smtc import SmtcControls

            return SmtcControls(parent)
        except Exception:
            log.warning("media overlay unavailable; using media-key hotkeys", exc_info=True)
        try:
            from ytm_lite.integrations.windows_hotkeys import HotkeyControls

            return HotkeyControls(parent)
        except Exception:
            log.warning("media keys unavailable", exc_info=True)
    elif sys.platform.startswith("linux"):
        try:
            from ytm_lite.integrations.linux_mpris import MprisControls

            controls = MprisControls(parent)
            if controls.registered:
                return controls
        except Exception:
            log.warning("MPRIS unavailable", exc_info=True)
    return MediaControls(parent)


def jpeg_bytes(pixmap: QPixmap, quality: int = 85) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "JPG", quality)
    buffer.close()
    return bytes(data.data())


class MediaBridge(QObject):
    """Keeps the OS controls in step with the controller, and the other way round."""

    def __init__(
        self,
        controls: MediaControls,
        controller: PlaybackController,
        images: ImageLoader,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controls = controls
        self._controller = controller
        self._images = images
        self._cover_url: str | None = None
        controls.play.connect(controller.play)
        controls.pause.connect(controller.pause)
        controls.play_pause.connect(controller.toggle_pause)
        controls.next.connect(controller.next)
        controls.previous.connect(controller.previous)
        controls.seek.connect(self._seek)
        controller.current_changed.connect(self._current_changed)
        controller.state_changed.connect(self._state_changed)
        # A new track or a seek: a gapless change leaves the state at "playing".
        controller.player.playback_restarted.connect(self._send_timeline)
        images.loaded.connect(self._image_loaded)

    def _current_changed(self, entry: QueueEntry | None) -> None:
        track = entry.track if entry is not None else None
        self._controls.update_track(track)
        self._cover_url = track.thumbnail if track is not None else None
        self._send_cover()

    def _state_changed(self, state: str) -> None:
        self._controls.update_state(PlaybackState(state))
        self._send_timeline()

    def _seek(self, seconds: float) -> None:
        self._controller.seek(seconds)
        self._send_timeline()

    def _send_timeline(self) -> None:
        position = self._controller.position()
        duration = self._controller.duration()
        if position is not None and duration:
            self._controls.update_timeline(position, duration)

    def _send_cover(self) -> None:
        pixmap = self._images.get(self._cover_url, COVER_PX)
        if pixmap is not None:
            self._controls.update_cover(jpeg_bytes(pixmap))

    def _image_loaded(self, key: str) -> None:
        if self._cover_url and key == image_key(self._cover_url, COVER_PX):
            self._send_cover()
