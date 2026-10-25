"""Bottom bar: cover, title and artist, like, transport and modes, seek, volume, queue."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.images import ImageLoader, image_key
from ytm_lite.app.models import Track, format_duration
from ytm_lite.app.play_queue import QueueEntry, RepeatMode
from ytm_lite.app.playback import PlaybackState
from ytm_lite.app.resolver import StreamInfo
from ytm_lite.ui import icons

_SLIDER_SCALE = 10  # slider units per second
COVER = 48
_REPEAT_TIPS = {
    RepeatMode.OFF: "Repeat: off",
    RepeatMode.ALL: "Repeat: all",
    RepeatMode.ONE: "Repeat: one",
}


class ElidedLabel(QLabel):
    """One line of text that ends in "…" when it doesn't fit."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._elide()

    def setText(self, text: str) -> None:
        self._full = text
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, self.width()))


class SeekSlider(QSlider):
    """A slider that jumps to where you click instead of paging."""

    seek_requested = Signal(float)  # seconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.sliderReleased.connect(self._released)

    def _released(self) -> None:
        self.seek_requested.emit(self.value() / _SLIDER_SCALE)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(event.position().x()), self.width()
            )
            self.setValue(value)
            self.seek_requested.emit(value / _SLIDER_SCALE)
        super().mousePressEvent(event)


def _button(icon: QIcon, tip: str, *, checkable: bool = False, size: int = 20) -> QToolButton:
    button = QToolButton()
    button.setIcon(icon)
    button.setIconSize(QSize(size, size))
    button.setToolTip(tip)
    button.setAutoRaise(True)
    button.setCheckable(checkable)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep Space for play/pause
    return button


class NowPlayingBar(QWidget):
    play_pause_clicked = Signal()
    next_clicked = Signal()
    previous_clicked = Signal()
    seek_requested = Signal(float)
    volume_changed = Signal(float)
    shuffle_toggled = Signal(bool)
    repeat_clicked = Signal()  # cycles off -> all -> one
    like_clicked = Signal()
    queue_toggled = Signal(bool)

    def __init__(self, images: ImageLoader, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NowPlayingBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._images = images
        self._thumbnail: str | None = None
        self._icons = {
            "play": icons.icon("play", 28),
            "pause": icons.icon("pause", 28),
            "repeat": icons.toggle_icon("repeat"),
            "repeat_one": icons.toggle_icon("repeat_one"),
        }

        self.cover = QLabel()
        self.cover.setFixedSize(COVER, COVER)
        self.cover.setStyleSheet("background: #2a2a2a;")
        self.title = ElidedLabel("Nothing playing")
        self.title.setObjectName("TrackTitle")
        self.artist = ElidedLabel("")
        self.artist.setObjectName("Dim")
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        text_box.addStretch(1)
        text_box.addWidget(self.title)
        text_box.addWidget(self.artist)
        text_box.addStretch(1)
        heart = icons.toggle_icon("heart_outline", on_name="heart")
        self.like_button = _button(heart, "Like (Ctrl+L)", checkable=True)
        self.like_button.setEnabled(False)
        self.like_button.clicked.connect(self.like_clicked)

        self.shuffle_button = _button(icons.toggle_icon("shuffle"), "Shuffle", checkable=True)
        self.prev_button = _button(icons.icon("previous", 24), "Previous (Ctrl+←)", size=24)
        self.play_button = _button(self._icons["play"], "Play (Space)", size=28)
        self.next_button = _button(icons.icon("next", 24), "Next (Ctrl+→)", size=24)
        self.repeat_button = _button(self._icons["repeat"], _REPEAT_TIPS[RepeatMode.OFF])
        self.repeat_button.setCheckable(True)
        self.shuffle_button.clicked.connect(self.shuffle_toggled)
        self.prev_button.clicked.connect(self.previous_clicked)
        self.play_button.clicked.connect(self.play_pause_clicked)
        self.next_button.clicked.connect(self.next_clicked)
        self.repeat_button.clicked.connect(self.repeat_clicked)

        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("Dim")
        self.total = QLabel("0:00")
        self.total.setObjectName("Dim")
        self.seek = SeekSlider()
        self.seek.setMinimumWidth(200)
        self.seek.seek_requested.connect(self.seek_requested)

        volume_icon = QLabel()
        volume_icon.setPixmap(icons.icon("volume", 18, icons.DIM).pixmap(18, 18))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(96)
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(self._volume_moved)
        self.quality = QLabel("")
        self.quality.setObjectName("Dim")
        self.queue_button = _button(
            icons.toggle_icon("queue", 22), "Queue", checkable=True, size=22
        )
        self.queue_button.toggled.connect(self.queue_toggled)

        left = QHBoxLayout()
        left.setSpacing(10)
        left.addWidget(self.cover)
        left.addLayout(text_box, 1)
        left.addWidget(self.like_button)
        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setMinimumWidth(180)
        left_box.setMaximumWidth(360)
        left.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(4)
        row.addWidget(left_box, 3)
        row.addSpacing(12)
        for button in (
            self.shuffle_button,
            self.prev_button,
            self.play_button,
            self.next_button,
            self.repeat_button,
        ):
            row.addWidget(button)
        row.addSpacing(12)
        row.addWidget(self.elapsed)
        row.addWidget(self.seek, 4)
        row.addWidget(self.total)
        row.addSpacing(12)
        row.addWidget(volume_icon)
        row.addWidget(self.volume)
        row.addSpacing(8)
        row.addWidget(self.quality)
        row.addWidget(self.queue_button)

        images.loaded.connect(self._image_loaded)

    # -- updates from the controller -----------------------------------------------------

    def set_entry(self, entry: QueueEntry | None) -> None:
        track: Track | None = entry.track if entry is not None else None
        self.like_button.setEnabled(track is not None)
        if track is None:
            self.title.setText("Nothing playing")
            self.title.setToolTip("")
            self.artist.setText("")
            self.quality.setText("")
            self._thumbnail = None
            self.cover.clear()
            self.set_position(None, None)
            return
        self.title.setText(track.title)
        self.title.setToolTip(track.title)
        self.artist.setText(track.artist_text)
        self.artist.setToolTip(track.artist_text)
        if track.thumbnail != self._thumbnail:
            self._thumbnail = track.thumbnail
            self.cover.clear()
            self._show_cover()

    def set_liked(self, liked: bool) -> None:
        self.like_button.setChecked(liked)
        self.like_button.setToolTip(
            "Remove from liked songs (Ctrl+L)" if liked else "Like (Ctrl+L)"
        )

    def set_stream(self, stream: StreamInfo | None) -> None:
        self.quality.setText(stream.quality_text if stream is not None else "")

    def set_state(self, state: str) -> None:
        playing = state in (PlaybackState.PLAYING.value, PlaybackState.LOADING.value)
        self.play_button.setIcon(self._icons["pause" if playing else "play"])
        self.play_button.setToolTip("Pause (Space)" if playing else "Play (Space)")
        if state == PlaybackState.LOADING.value:
            self.quality.setText("Loading…")

    def set_modes(self, shuffle: bool, repeat: RepeatMode) -> None:
        self.shuffle_button.setChecked(shuffle)
        self.shuffle_button.setToolTip("Shuffle: on" if shuffle else "Shuffle: off")
        self.repeat_button.setChecked(repeat is not RepeatMode.OFF)
        self.repeat_button.setIcon(
            self._icons["repeat_one" if repeat is RepeatMode.ONE else "repeat"]
        )
        self.repeat_button.setToolTip(_REPEAT_TIPS[repeat])

    def set_queue_visible(self, visible: bool) -> None:
        self.queue_button.blockSignals(True)
        self.queue_button.setChecked(visible)
        self.queue_button.blockSignals(False)

    def set_volume(self, volume: float) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(round(volume))
        self.volume.blockSignals(False)

    def set_position(self, position: float | None, duration: float | None) -> None:
        self.elapsed.setText(format_duration(position) if position is not None else "0:00")
        self.total.setText(format_duration(duration) if duration else "0:00")
        if self.seek.isSliderDown():
            return
        maximum = int((duration or 0) * _SLIDER_SCALE)
        if self.seek.maximum() != maximum:
            self.seek.setMaximum(maximum)
        self.seek.blockSignals(True)
        self.seek.setValue(int((position or 0) * _SLIDER_SCALE))
        self.seek.blockSignals(False)

    # -- internals ----------------------------------------------------------------------

    def _volume_moved(self, value: int) -> None:
        self.volume_changed.emit(float(value))

    def _cover_px(self) -> int:
        return round(COVER * self.devicePixelRatioF())

    def _show_cover(self) -> None:
        px = self._cover_px()
        pixmap = self._images.get(self._thumbnail, px)
        if pixmap is None:
            return
        side = min(pixmap.width(), pixmap.height())  # centre-crop video thumbnails
        square = pixmap.copy(
            (pixmap.width() - side) // 2, (pixmap.height() - side) // 2, side, side
        )
        if side != px:
            square = square.scaled(
                px,
                px,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        square.setDevicePixelRatio(self.devicePixelRatioF())
        self.cover.setPixmap(square)

    def _image_loaded(self, key: str) -> None:
        if self._thumbnail and key == image_key(self._thumbnail, self._cover_px()):
            self._show_cover()
