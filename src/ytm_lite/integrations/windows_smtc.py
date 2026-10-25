"""Windows System Media Transport Controls: the media overlay by the volume flyout, and
the hardware media keys (Windows routes them to the active SMTC session).

SMTC is bound to a hidden helper window of our own (`get_for_window`), so tray mode,
which destroys the main window, doesn't touch it. Nothing WinRT is loaded until the
first track plays. Button events arrive on a WinRT thread and are passed on as Qt
signals, which Qt queues onto the UI thread.

Measured (spike S5): pywinrt costs ~5 MB private once loaded, plus ~6 MB more for
asyncio, ssl and OpenSSL, which it imports only so WinRT calls can be awaited (see
`_import_winrt`). A cover given by URL makes Windows download it inside this process
(+10 MB working set, 4 threads), so covers are handed over as bytes instead.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import time
import types
from datetime import timedelta
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from ytm_lite.app.models import Track
from ytm_lite.app.playback import PlaybackState
from ytm_lite.integrations.base import MediaControls

log = logging.getLogger(__name__)


def _installed() -> bool:
    """Whether the projection is there, without loading it (that starts WinRT). `winrt`
    is a namespace package, so finding a module in it runs no code."""
    try:
        return importlib.util.find_spec("winrt._winrt_windows_media_interop") is not None
    except (ImportError, ValueError):
        return False


class _AsyncioOnDemand(types.ModuleType):
    """Stands in for asyncio inside pywinrt; imports the real one if it's ever used."""

    def __getattr__(self, name: str) -> Any:
        # Dunders are what PySide's import hook and `inspect` probe for: nothing to load.
        if name.startswith("__") or sys.modules.get("asyncio") is self:
            raise AttributeError(name)
        return getattr(importlib.import_module("asyncio"), name)


def _import_winrt() -> None:
    """Import pywinrt's runtime without asyncio. It imports asyncio (and with it ssl and
    OpenSSL, ~6 MB private) only to make WinRT operations awaitable, which this app
    never does; a stand-in takes asyncio's place for the duration of the import."""
    if "asyncio" in sys.modules or "winrt.runtime._internals" in sys.modules:
        return
    stand_in = _AsyncioOnDemand("asyncio")
    sys.modules["asyncio"] = stand_in
    try:
        import winrt.runtime._internals  # noqa: F401
    finally:
        if sys.modules.get("asyncio") is stand_in:
            del sys.modules["asyncio"]


class SmtcControls(MediaControls):
    name = "smtc"

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._helper: QWidget | None = None
        self._smtc: Any = None
        self._track: Track | None = None
        self._cover: bytes | None = None
        self._state = PlaybackState.STOPPED
        if not _installed():
            raise ImportError("pywinrt's Windows.Media projection isn't installed")

    # -- setup -------------------------------------------------------------------------

    def _ensure(self) -> Any:
        if self._smtc is not None:
            return self._smtc
        _import_winrt()
        from winrt.windows.media import MediaPlaybackType
        from winrt.windows.media.interop import get_for_window

        helper = QWidget()
        helper.setWindowFlag(Qt.WindowType.Tool)
        helper.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        smtc = get_for_window(int(helper.winId()))
        smtc.is_enabled = True
        smtc.is_play_enabled = True
        smtc.is_pause_enabled = True
        smtc.is_next_enabled = True
        smtc.is_previous_enabled = True
        smtc.is_stop_enabled = False
        smtc.add_button_pressed(self._on_button)
        smtc.add_playback_position_change_requested(self._on_position)
        smtc.display_updater.type = MediaPlaybackType.MUSIC
        self._helper = helper
        self._smtc = smtc
        log.info("media overlay ready")
        return smtc

    # -- updates from the app ---------------------------------------------------------------

    def update_track(self, track: Track | None) -> None:
        self._track = track
        self._cover = None
        if self._smtc is not None:
            self._push_display()

    def update_cover(self, jpeg: bytes) -> None:
        self._cover = jpeg
        if self._smtc is not None:
            self._push_display()

    def update_state(self, state: PlaybackState) -> None:
        self._state = state
        if self._smtc is None:
            if state is not PlaybackState.PLAYING:
                return  # stay unloaded until something actually plays
            self._ensure()
            self._push_display()
        from winrt.windows.media import MediaPlaybackStatus

        status = {
            PlaybackState.PLAYING: MediaPlaybackStatus.PLAYING,
            PlaybackState.PAUSED: MediaPlaybackStatus.PAUSED,
            PlaybackState.LOADING: MediaPlaybackStatus.CHANGING,
            PlaybackState.STOPPED: MediaPlaybackStatus.STOPPED,
        }[state]
        self._smtc.playback_status = status

    def update_timeline(self, position: float, duration: float) -> None:
        if self._smtc is None:
            return
        from winrt.windows.media import SystemMediaTransportControlsTimelineProperties

        timeline = SystemMediaTransportControlsTimelineProperties()
        timeline.start_time = timedelta(0)
        timeline.min_seek_time = timedelta(0)
        timeline.end_time = timedelta(seconds=duration)
        timeline.max_seek_time = timedelta(seconds=duration)
        timeline.position = timedelta(seconds=max(0.0, min(position, duration)))
        self._smtc.update_timeline_properties(timeline)

    def _push_display(self) -> None:
        updater = self._smtc.display_updater
        track = self._track
        music = updater.music_properties
        music.title = track.title if track is not None else ""
        music.artist = track.artist_text if track is not None else ""
        music.album_title = track.album.name if track is not None and track.album else ""
        updater.thumbnail = self._thumbnail(self._cover) if self._cover else None
        updater.update()

    @staticmethod
    def _thumbnail(jpeg: bytes) -> Any:
        """A stream reference over the cover bytes, written into memory."""
        from winrt.windows.foundation import AsyncStatus
        from winrt.windows.storage.streams import (
            DataWriter,
            InMemoryRandomAccessStream,
            RandomAccessStreamReference,
        )

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(jpeg)
        operation = writer.store_async()
        deadline = time.monotonic() + 1.0
        while operation.status == AsyncStatus.STARTED and time.monotonic() < deadline:
            time.sleep(0.002)  # an in-memory write: done almost at once
        writer.detach_stream()
        stream.seek(0)
        return RandomAccessStreamReference.create_from_stream(stream)

    def close(self) -> None:
        if self._smtc is not None:
            self._smtc.is_enabled = False
            self._smtc = None
        if self._helper is not None:
            self._helper.deleteLater()
            self._helper = None

    # -- events from Windows (on a WinRT thread) ---------------------------------------

    def _on_button(self, _sender: Any, args: Any) -> None:
        from winrt.windows.media import SystemMediaTransportControlsButton as Button

        button = args.button
        if button == Button.PLAY:
            self.play.emit()
        elif button == Button.PAUSE:
            self.pause.emit()
        elif button == Button.NEXT:
            self.next.emit()
        elif button == Button.PREVIOUS:
            self.previous.emit()

    def _on_position(self, _sender: Any, args: Any) -> None:
        self.seek.emit(args.requested_playback_position.total_seconds())
