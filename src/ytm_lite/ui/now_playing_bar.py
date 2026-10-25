"""Bottom bar: current track, transport buttons, seek slider, volume."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
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

from ytm_lite.app.models import format_duration
from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackState
from ytm_lite.app.resolver import StreamInfo

_SLIDER_SCALE = 10  # slider units per second


class SeekSlider(QSlider):
    """A slider that jumps to where you click instead of paging."""

    seek_requested = Signal(float)  # seconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.sliderReleased.connect(lambda: self.seek_requested.emit(self.value() / _SLIDER_SCALE))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(event.position().x()), self.width()
            )
            self.setValue(value)
            self.seek_requested.emit(value / _SLIDER_SCALE)
        super().mousePressEvent(event)


class NowPlayingBar(QWidget):
    play_pause_clicked = Signal()
    next_clicked = Signal()
    previous_clicked = Signal()
    seek_requested = Signal(float)
    volume_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NowPlayingBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        style = self.style()

        self.title = QLabel("Nothing playing")
        self.title.setObjectName("TrackTitle")
        self.artist = QLabel("")
        self.artist.setObjectName("Dim")
        for label in (self.title, self.artist):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        text_box.addWidget(self.title)
        text_box.addWidget(self.artist)

        def button(icon: QStyle.StandardPixmap, tip: str) -> QToolButton:
            btn = QToolButton()
            btn.setIcon(style.standardIcon(icon))
            btn.setToolTip(tip)
            btn.setAutoRaise(True)
            return btn

        self.prev_button = button(QStyle.StandardPixmap.SP_MediaSkipBackward, "Previous (Ctrl+←)")
        self.play_button = button(QStyle.StandardPixmap.SP_MediaPlay, "Play (Space)")
        self.next_button = button(QStyle.StandardPixmap.SP_MediaSkipForward, "Next (Ctrl+→)")
        self.prev_button.clicked.connect(self.previous_clicked)
        self.play_button.clicked.connect(self.play_pause_clicked)
        self.next_button.clicked.connect(self.next_clicked)

        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("Dim")
        self.total = QLabel("0:00")
        self.total.setObjectName("Dim")
        self.seek = SeekSlider()
        self.seek.setMinimumWidth(240)
        self.seek.seek_requested.connect(self.seek_requested)

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(100)
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(lambda v: self.volume_changed.emit(float(v)))
        volume_icon = QLabel()
        volume_icon.setPixmap(
            style.standardIcon(QStyle.StandardPixmap.SP_MediaVolume).pixmap(16, 16)
        )

        self.quality = QLabel("")
        self.quality.setObjectName("Dim")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.addLayout(text_box, 3)
        row.addWidget(self.prev_button)
        row.addWidget(self.play_button)
        row.addWidget(self.next_button)
        row.addSpacing(8)
        row.addWidget(self.elapsed)
        row.addWidget(self.seek, 4)
        row.addWidget(self.total)
        row.addSpacing(12)
        row.addWidget(volume_icon)
        row.addWidget(self.volume)
        row.addSpacing(8)
        row.addWidget(self.quality)

    def set_entry(self, entry: QueueEntry | None) -> None:
        if entry is None:
            self.title.setText("Nothing playing")
            self.artist.setText("")
            self.set_position(None, None)
            return
        self.title.setText(entry.track.title)
        self.artist.setText(entry.track.artist_text)
        self.title.setToolTip(entry.track.title)

    def set_stream(self, stream: StreamInfo | None) -> None:
        self.quality.setText(stream.quality_text if stream is not None else "")

    def set_state(self, state: str) -> None:
        playing = state in (PlaybackState.PLAYING.value, PlaybackState.LOADING.value)
        icon = (
            QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        )
        self.play_button.setIcon(self.style().standardIcon(icon))
        self.play_button.setToolTip("Pause (Space)" if playing else "Play (Space)")
        if state == PlaybackState.LOADING.value:
            self.quality.setText("Loading…")

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
