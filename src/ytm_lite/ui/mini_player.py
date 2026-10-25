"""The mini player: a small always-on-top window with the cover, the song, previous /
play / next and a thin progress line along the bottom.

While it's up the main window is gone (as in tray mode), so it costs next to nothing.
Drag it anywhere; double-click it, or its expand button, to get the full window back;
its ✕ sends the app to the tray.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QHideEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ytm_lite.app.images import ImageLoader, image_key
from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackController, PlaybackState
from ytm_lite.ui import icons
from ytm_lite.ui.now_playing_bar import CoverView, bar_button
from ytm_lite.ui.theme import ACCENT, font
from ytm_lite.ui.widgets import CircleButton, ElidedLabel

COVER = 64
PROGRESS_MS = 500


def round_corners(widget: QWidget) -> None:
    """Windows 11 rounds a frameless window's corners when asked (no transparency)."""
    if sys.platform != "win32":
        return
    preference = ctypes.c_int(2)  # DWMWCP_ROUND
    with contextlib.suppress(AttributeError, OSError):  # older Windows: square corners
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(widget.winId())), 33, ctypes.byref(preference), 4
        )


class MiniPlayer(QWidget):
    expand_requested = Signal()
    close_requested = Signal()

    def __init__(self, controller: PlaybackController, images: ImageLoader) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        super().__init__(None, flags)
        self.setObjectName("MiniPlayer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("ytm-lite")
        self.setFixedSize(QSize(380, 88))
        self._controller = controller
        self._images = images
        self._thumbnail: str | None = None
        self._drag_from: QPoint | None = None

        self.cover = CoverView(COVER)
        self.title = ElidedLabel("Nothing playing")
        self.title.setFont(font(10, QFont.Weight.Medium))
        self.artist = ElidedLabel("")
        self.artist.setObjectName("Dim")
        self.artist.setFont(font(8.5))
        dim, bright = icons.DIM, icons.NORMAL
        previous = bar_button(icons.icon("previous", 20, dim, hover=bright), "Previous")
        self.play = CircleButton("play", 30, "#ffffff", "#000000", "#e6e6e6")
        following = bar_button(icons.icon("next", 20, dim, hover=bright), "Next")
        previous.clicked.connect(controller.previous)
        self.play.clicked.connect(controller.toggle_pause)
        following.clicked.connect(controller.next)
        expand = bar_button(icons.icon("expand", 14, dim, hover=bright), "Full window", size=14)
        expand.clicked.connect(self.expand_requested)
        close = bar_button(icons.icon("close", 14, dim, hover=bright), "To the tray", size=14)
        close.clicked.connect(self.close_requested)

        transport = QHBoxLayout()
        transport.setSpacing(6)
        transport.addWidget(previous)
        transport.addWidget(self.play)
        transport.addWidget(following)
        transport.addStretch(1)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(self.title)
        text.addWidget(self.artist)
        text.addLayout(transport)
        corner = QVBoxLayout()
        corner.setSpacing(0)
        corner.addWidget(expand)
        corner.addWidget(close)
        corner.addStretch(1)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 6, 12)
        row.setSpacing(12)
        row.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(text, 1)
        row.addLayout(corner)

        for keys, slot in (
            ("Space", controller.toggle_pause),
            ("Ctrl+Right", controller.next),
            ("Ctrl+Left", controller.previous),
        ):
            QShortcut(QKeySequence(keys), self).activated.connect(slot)

        self._progress = QTimer(self)
        self._progress.setInterval(PROGRESS_MS)
        self._progress.timeout.connect(self.update)
        controller.current_changed.connect(self._set_entry)
        controller.state_changed.connect(self._set_state)
        images.loaded.connect(self._image_loaded)
        self._set_entry(controller.queue.current())
        self._set_state(controller.state.value)
        round_corners(self)

    # -- following the controller ---------------------------------------------------------

    def _set_entry(self, entry: QueueEntry | None) -> None:
        track = entry.track if entry is not None else None
        self.title.setText(track.title if track else "Nothing playing")
        self.artist.setText(track.artist_text if track else "")
        thumbnail = track.thumbnail if track else None
        if thumbnail != self._thumbnail:
            self._thumbnail = thumbnail
            self.cover.set_pixmap(self._images.get(thumbnail, COVER))
        self.update()

    def _image_loaded(self, key: str) -> None:
        if self._thumbnail and key == image_key(self._thumbnail, COVER):
            self.cover.set_pixmap(self._images.get(self._thumbnail, COVER))

    def _set_state(self, state: str) -> None:
        playing = state in (PlaybackState.PLAYING.value, PlaybackState.LOADING.value)
        self.play.set_icon_name("pause" if playing else "play")
        if state == PlaybackState.PLAYING.value and self.isVisible():
            self._progress.start()
        else:
            self._progress.stop()
            self.update()

    # -- painting and moving ----------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        position = self._controller.position()
        duration = self._controller.duration()
        if not position or not duration:
            return
        painter = QPainter(self)
        width = round(self.width() * min(1.0, position / duration))
        painter.fillRect(QRect(0, self.height() - 3, width, 3), QColor(ACCENT))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._set_state(self._controller.state.value)

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._progress.stop()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_from = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.expand_requested.emit()
