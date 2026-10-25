"""A playlist (or Liked Songs): small header with cover, then the track table."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ytm_lite.app.images import ImageLoader, image_key
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import Playlist, Track, format_duration
from ytm_lite.app.playback import PlaybackController
from ytm_lite.ui.track_table import TrackTable, TrackTableModel

HEADER_COVER = 120


class PlaylistPage(QWidget):
    play_requested = Signal(object, int)  # list[Track], start row

    def __init__(
        self,
        playlist_id: str,
        title: str,
        images: ImageLoader,
        liked: LikedIndex,
        controller: PlaybackController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.playlist_id = playlist_id
        self._images = images
        self._thumbnail: str | None = None

        self.cover = QLabel()
        self.cover.setFixedSize(HEADER_COVER, HEADER_COVER)
        self.cover.setStyleSheet("background: #2a2a2a;")
        self.title = QLabel(title)
        font = QFont(self.title.font())
        font.setPointSizeF(font.pointSizeF() * 1.8)
        font.setBold(True)
        self.title.setFont(font)
        self.meta = QLabel("")
        self.meta.setObjectName("Dim")
        self.status = QLabel("Loading…")
        self.status.setObjectName("Dim")
        self.play_button = QPushButton("Play")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(lambda: self._play_from(0))

        text = QVBoxLayout()
        text.addStretch(1)
        text.addWidget(self.title)
        text.addWidget(self.meta)
        buttons = QHBoxLayout()
        buttons.addWidget(self.play_button)
        buttons.addWidget(self.status)
        buttons.addStretch(1)
        text.addLayout(buttons)
        header = QHBoxLayout()
        header.setContentsMargins(24, 20, 24, 12)
        header.setSpacing(20)
        header.addWidget(self.cover)
        header.addLayout(text, 1)

        self.model = TrackTableModel(liked, self)
        self.table = TrackTable(self.model, images, liked, controller)
        self.table.play_requested.connect(self._play_from)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.table, 1)
        images.loaded.connect(self._image_loaded)

    def set_playlist(self, playlist: Playlist) -> None:
        self.title.setText(playlist.title)
        count = playlist.track_count or len(playlist.tracks)
        total = sum(t.duration or 0 for t in playlist.tracks)
        parts = [playlist.author or "", f"{count:,} songs" if count else ""]
        if playlist.complete and total:
            parts.append(format_duration(total))
        self.meta.setText(" • ".join(p for p in parts if p))
        if playlist.complete:
            self.status.setText("" if playlist.tracks else "No songs yet")
        else:
            self.status.setText(f"Loading… {len(playlist.tracks):,} of {count:,}")
        self.model.set_tracks(playlist.tracks)
        self.play_button.setEnabled(any(t.available for t in playlist.tracks))
        self._thumbnail = playlist.thumbnail
        self._show_cover()

    def tracks(self) -> list[Track]:
        return self.model.tracks

    def _play_from(self, row: int) -> None:
        tracks = self.model.tracks
        while row < len(tracks) and not tracks[row].available:
            row += 1
        if row < len(tracks):
            self.play_requested.emit(tracks, row)

    def _cover_px(self) -> int:
        return round(HEADER_COVER * self.devicePixelRatioF())

    def _show_cover(self) -> None:
        pixmap = self._images.get(self._thumbnail, self._cover_px())
        if pixmap is not None:
            scaled = pixmap.scaled(
                self.cover.size() * self.devicePixelRatioF(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(self.devicePixelRatioF())  # sharp, not doubled, on HiDPI
            self.cover.setPixmap(scaled)

    def _image_loaded(self, key: str) -> None:
        if self._thumbnail and key == image_key(self._thumbnail, self._cover_px()):
            self._show_cover()
