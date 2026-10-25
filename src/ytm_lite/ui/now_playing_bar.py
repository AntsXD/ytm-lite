"""Bottom bar, on the black canvas: what's playing (left), the controls above the seek bar
(centre), and quality, queue and volume (right)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
)
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
from ytm_lite.ui.theme import ACCENT, TEXT, font
from ytm_lite.ui.widgets import CircleButton, ElidedLabel, draw_cover

_SLIDER_SCALE = 10  # slider units per second
COVER = 56
_REPEAT_TIPS = {
    RepeatMode.OFF: "Repeat: off",
    RepeatMode.ALL: "Repeat: all",
    RepeatMode.ONE: "Repeat: one",
}


class ClickSlider(QSlider):
    """A thin horizontal slider you can click or drag anywhere along its height.

    It paints itself: a 4 px track that turns violet, with a handle, while the mouse is
    over it. A press jumps straight to the clicked value and the handle follows the mouse
    until it is released (QSlider alone only page-steps, and only on its groove).
    """

    HANDLE = 6  # radius; the track's ends are where the handle's centre stops

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # arrow keys stay with the lists
        self.setMinimumHeight(18)
        self._hovered = False

    def value_at(self, x: float) -> int:
        span = max(1, self.width() - 2 * self.HANDLE)
        offset = round(x - self.HANDLE)
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), max(0, min(offset, span)), span
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        event.accept()
        self.setSliderDown(True)
        self.setSliderPosition(self.value_at(event.position().x()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.isSliderDown() and event.buttons() & Qt.MouseButton.LeftButton:
            event.accept()
            self.setSliderPosition(self.value_at(event.position().x()))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            event.accept()
            self.setSliderDown(False)
            self.update()
        else:
            super().mouseReleaseEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        r = self.HANDLE
        track = QRectF(r, self.height() / 2 - 2, max(0, self.width() - 2 * r), 4)
        painter.setBrush(QColor("#4d4d4d"))
        painter.drawRoundedRect(track, 2, 2)
        span = self.maximum() - self.minimum()
        fraction = (self.sliderPosition() - self.minimum()) / span if span > 0 else 0.0
        active = (self._hovered or self.isSliderDown()) and self.isEnabled() and span > 0
        if fraction > 0:
            painter.setBrush(QColor(ACCENT if active else TEXT))
            filled = QRectF(track.left(), track.top(), track.width() * fraction, track.height())
            painter.drawRoundedRect(filled, 2, 2)
        if active:
            painter.setBrush(QColor("#ffffff"))
            center = QPointF(track.left() + track.width() * fraction, track.center().y())
            painter.drawEllipse(center, r, r)


class SeekSlider(ClickSlider):
    """Seeks when the mouse is released, not on every pixel of a drag."""

    seek_requested = Signal(float)  # seconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sliderReleased.connect(self._released)

    def _released(self) -> None:
        if self.maximum() > 0:
            self.seek_requested.emit(self.value() / _SLIDER_SCALE)


class CoverView(QWidget):
    """The current track's cover, rounded."""

    def __init__(self, side: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(side, side)
        self.pixmap: QPixmap | None = None

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        draw_cover(painter, self.rect(), self.pixmap, 4)


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
        self._images = images
        self._thumbnail: str | None = None
        self._unmuted_volume = 70
        self._icons = {
            "repeat": icons.toggle_icon("repeat"),
            "repeat_one": icons.toggle_icon("repeat_one"),
            "volume": icons.icon("volume", 20, icons.DIM, hover=icons.NORMAL),
            "volume_off": icons.icon("volume_off", 20, icons.DIM, hover=icons.NORMAL),
        }

        # Left: what's playing.
        self.cover = CoverView(COVER)
        self.title = ElidedLabel("Nothing playing")
        self.title.setFont(font(10.5, QFont.Weight.Medium))
        self.artist = ElidedLabel("")
        self.artist.setObjectName("Dim")
        self.artist.setFont(font(9))
        heart = icons.toggle_icon("heart_outline", on_name="heart")
        self.like_button = _button(heart, "Like (Ctrl+L)", checkable=True)
        self.like_button.setEnabled(False)
        self.like_button.clicked.connect(self.like_clicked)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.addStretch(1)
        text_box.addWidget(self.title)
        text_box.addWidget(self.artist)
        text_box.addStretch(1)
        left = QWidget()
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(12)
        left_row.addWidget(self.cover)
        left_row.addLayout(text_box)
        left_row.addWidget(self.like_button)
        left_row.addStretch(1)

        # Centre: transport above the seek bar.
        dim, bright = icons.DIM, icons.NORMAL
        self.shuffle_button = _button(icons.toggle_icon("shuffle"), "Shuffle", checkable=True)
        self.prev_button = _button(
            icons.icon("previous", 24, dim, hover=bright), "Previous (Ctrl+←)", size=24
        )
        self.play_button = CircleButton("play", 36, "#ffffff", "#000000", "#e6e6e6")
        self.play_button.setToolTip("Play (Space)")
        self.next_button = _button(
            icons.icon("next", 24, dim, hover=bright), "Next (Ctrl+→)", size=24
        )
        self.repeat_button = _button(self._icons["repeat"], _REPEAT_TIPS[RepeatMode.OFF])
        self.repeat_button.setCheckable(True)
        self.shuffle_button.clicked.connect(self.shuffle_toggled)
        self.prev_button.clicked.connect(self.previous_clicked)
        self.play_button.clicked.connect(self.play_pause_clicked)
        self.next_button.clicked.connect(self.next_clicked)
        self.repeat_button.clicked.connect(self.repeat_clicked)
        transport = QHBoxLayout()
        transport.setSpacing(14)
        transport.addStretch(1)
        for widget in (
            self.shuffle_button,
            self.prev_button,
            self.play_button,
            self.next_button,
            self.repeat_button,
        ):
            transport.addWidget(widget)
        transport.addStretch(1)

        self.elapsed = QLabel("0:00")
        self.total = QLabel("0:00")
        for label, align in (
            (self.elapsed, Qt.AlignmentFlag.AlignRight),
            (self.total, Qt.AlignmentFlag.AlignLeft),
        ):
            label.setObjectName("Dim")
            label.setFont(font(8.5))
            label.setFixedWidth(44)
            label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        self.seek = SeekSlider()
        self.seek.seek_requested.connect(self.seek_requested)
        self.seek.sliderMoved.connect(self._seek_dragged)
        seek_row = QHBoxLayout()
        seek_row.setSpacing(6)
        seek_row.addWidget(self.elapsed)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.total)
        center = QWidget()
        center.setMaximumWidth(720)
        center_box = QVBoxLayout(center)
        center_box.setContentsMargins(0, 0, 0, 0)
        center_box.setSpacing(2)
        center_box.addLayout(transport)
        center_box.addLayout(seek_row)

        # Right: quality, queue, volume.
        self.quality = QLabel("")
        self.quality.setObjectName("Dim")
        self.quality.setFont(font(8.5))
        self.queue_button = _button(
            icons.toggle_icon("queue", 22), "Queue", checkable=True, size=22
        )
        self.queue_button.toggled.connect(self.queue_toggled)
        self.mute_button = _button(self._icons["volume"], "Mute")
        self.mute_button.clicked.connect(self._toggle_mute)
        self.volume = ClickSlider()
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(110)
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(self._volume_moved)
        right = QWidget()
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.setSpacing(6)
        right_row.addStretch(1)
        right_row.addWidget(self.quality)
        right_row.addSpacing(6)
        right_row.addWidget(self.queue_button)
        right_row.addWidget(self.mute_button)
        right_row.addWidget(self.volume)

        # Equal stretch on both sides keeps the controls centred in the window.
        for side in (left, right):
            side.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            side.setMinimumWidth(200)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 12, 10)
        row.setSpacing(16)
        row.addWidget(left, 3)
        row.addWidget(center, 4)
        row.addWidget(right, 3)

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
            self.cover.set_pixmap(None)
            self.set_position(None, None)
            return
        self.title.setText(track.title)
        self.title.setToolTip(track.title)
        self.artist.setText(track.artist_text)
        self.artist.setToolTip(track.artist_text)
        if track.thumbnail != self._thumbnail:
            self._thumbnail = track.thumbnail
            self.cover.set_pixmap(None)
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
        self.play_button.set_icon_name("pause" if playing else "play")
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
        self._update_mute_icon()

    def set_position(self, position: float | None, duration: float | None) -> None:
        self.total.setText(format_duration(duration) if duration else "0:00")
        if self.seek.isSliderDown():
            return  # the elapsed time follows the drag instead
        self.elapsed.setText(format_duration(position) if position is not None else "0:00")
        maximum = int((duration or 0) * _SLIDER_SCALE)
        if self.seek.maximum() != maximum:
            self.seek.setMaximum(maximum)
        self.seek.blockSignals(True)
        self.seek.setValue(int((position or 0) * _SLIDER_SCALE))
        self.seek.blockSignals(False)

    # -- internals ----------------------------------------------------------------------

    def _volume_moved(self, value: int) -> None:
        self._update_mute_icon()
        self.volume_changed.emit(float(value))

    def _toggle_mute(self) -> None:
        if self.volume.value() > 0:
            self._unmuted_volume = self.volume.value()
            self.volume.setValue(0)
        else:
            self.volume.setValue(self._unmuted_volume or 50)

    def _update_mute_icon(self) -> None:
        muted = self.volume.value() == 0
        self.mute_button.setIcon(self._icons["volume_off" if muted else "volume"])
        self.mute_button.setToolTip("Unmute" if muted else "Mute")

    def _seek_dragged(self, value: int) -> None:
        self.elapsed.setText(format_duration(value / _SLIDER_SCALE))

    def _cover_px(self) -> int:
        return round(COVER * self.devicePixelRatioF())

    def _show_cover(self) -> None:
        pixmap = self._images.get(self._thumbnail, self._cover_px())
        if pixmap is not None:
            self.cover.set_pixmap(pixmap)

    def _image_loaded(self, key: str) -> None:
        if self._thumbnail and key == image_key(self._thumbnail, self._cover_px()):
            self._show_cover()
