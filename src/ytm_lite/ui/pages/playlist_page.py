"""A playlist (or Liked Songs): a header tinted from its cover, then the track list.

The header shrinks to a single line once the list is scrolled, so long lists get the room.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ytm_lite.app.images import ImageLoader, image_key
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import LIKED_SONGS_ID, Playlist, Track, format_duration
from ytm_lite.app.playback import PlaybackController
from ytm_lite.ui import icons
from ytm_lite.ui.theme import (
    ACCENT_FILL,
    ACCENT_FILL_HOVER,
    PANEL,
    RADIUS,
    TEXT,
    font,
)
from ytm_lite.ui.track_table import TrackTable, TrackTableModel
from ytm_lite.ui.widgets import CircleButton, ElidedLabel, draw_cover, tint_of

LIKED_TINT = "#3a2a66"  # Liked Songs has no cover: a violet header with a heart


class PlaylistHeader(QWidget):
    COVER = 168
    COLLAPSE_AT = 80  # px of scrolling before the header shrinks

    play_clicked = Signal()
    shuffle_clicked = Signal()

    def __init__(self, images: ImageLoader, liked_songs: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self._images = images
        self._liked_songs = liked_songs
        self._thumbnail: str | None = None
        self._pixmap: QPixmap | None = None
        self._tint = QColor(LIKED_TINT if liked_songs else "#2a2a2a")
        self.collapsed = False

        self.cover_space = QWidget()
        self.cover_space.setFixedSize(self.COVER, self.COVER)
        self.kind = QLabel("Playlist")
        self.kind.setFont(font(9, QFont.Weight.DemiBold))
        self.title = ElidedLabel("")
        self.title.setFont(font(26, QFont.Weight.Bold))
        self.compact_title = ElidedLabel("")  # shown instead, next to Play, when collapsed
        self.compact_title.setFont(font(15, QFont.Weight.Bold))
        self.meta = QLabel("")
        self.meta.setObjectName("Dim")
        self.status = QLabel("Loading…")
        self.status.setObjectName("Dim")
        self.play = CircleButton("play", 48, ACCENT_FILL, "#ffffff", ACCENT_FILL_HOVER)
        self.play.setToolTip("Play")
        self.play.setEnabled(False)
        self.play.clicked.connect(self.play_clicked)
        self.shuffle = QToolButton()
        self.shuffle.setIcon(icons.icon("shuffle", 28, icons.DIM))
        self.shuffle.setIconSize(QSize(28, 28))
        self.shuffle.setToolTip("Shuffle play")
        self.shuffle.setEnabled(False)
        self.shuffle.clicked.connect(self.shuffle_clicked)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addWidget(self.play)
        actions.addWidget(self.compact_title, 1)
        actions.addWidget(self.shuffle)
        actions.addSpacing(8)
        actions.addWidget(self.status, 1)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addStretch(1)
        text.addWidget(self.kind)
        text.addWidget(self.title)
        text.addWidget(self.meta)
        text.addSpacing(10)
        text.addLayout(actions)
        self._row = QHBoxLayout(self)
        self._row.setSpacing(22)
        self._row.addWidget(self.cover_space, 0, Qt.AlignmentFlag.AlignBottom)
        self._row.addLayout(text, 1)
        self._apply_collapsed()
        images.loaded.connect(self._image_loaded)

    # -- content ---------------------------------------------------------------------------

    def set_thumbnail(self, url: str | None) -> None:
        if url == self._thumbnail:
            return
        self._thumbnail = url
        self._pixmap = None
        self._show_cover()

    def _cover_px(self) -> int:
        return round(self.COVER * self.devicePixelRatioF())

    def _show_cover(self) -> None:
        pixmap = self._images.get(self._thumbnail, self._cover_px())
        if pixmap is not None:
            self._pixmap = pixmap
            self._tint = tint_of(pixmap)
            self.update()

    def _image_loaded(self, key: str) -> None:
        if self._thumbnail and key == image_key(self._thumbnail, self._cover_px()):
            self._show_cover()

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed != self.collapsed:
            self.collapsed = collapsed
            self._apply_collapsed()

    def set_title(self, text: str) -> None:
        self.title.setText(text)
        self.title.setToolTip(text)
        self.compact_title.setText(text)

    def _apply_collapsed(self) -> None:
        collapsed = self.collapsed
        for widget in (self.cover_space, self.kind, self.title, self.meta, self.status):
            widget.setVisible(not collapsed)
        self.compact_title.setVisible(collapsed)
        if collapsed:
            self._row.setContentsMargins(24, 12, 24, 12)
        else:
            self._row.setContentsMargins(24, 28, 24, 18)

    # -- painting ------------------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, self._tint)
        gradient.setColorAt(1.0, QColor(PANEL))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        # Round the top corners only, to match the panel this header sits in.
        painter.setClipRect(self.rect())
        painter.drawRoundedRect(QRectF(self.rect().adjusted(0, 0, 0, RADIUS)), RADIUS, RADIUS)
        painter.setClipping(False)
        if self.cover_space.isVisible():
            cover = QRect(self.cover_space.pos(), self.cover_space.size())
            if self._liked_songs and self._pixmap is None:
                self._paint_liked_cover(painter, cover)
            else:
                draw_cover(painter, cover, self._pixmap, 6)

    @staticmethod
    def _paint_liked_cover(painter: QPainter, rect: QRect) -> None:
        gradient = QLinearGradient(rect.topLeft().toPointF(), rect.bottomRight().toPointF())
        gradient.setColorAt(0.0, QColor("#5b3fd1"))
        gradient.setColorAt(1.0, QColor("#a78bfa"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(rect), 6, 6)
        side = rect.width() * 0.4
        heart = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
        icons.paint(painter, "heart", heart, TEXT)


class PlaylistPage(QWidget):
    play_requested = Signal(object, int)  # list[Track], start row
    shuffle_requested = Signal(object)  # list[Track]

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
        self.header = PlaylistHeader(images, liked_songs=playlist_id == LIKED_SONGS_ID)
        self.header.set_title(title)
        self.header.play_clicked.connect(self._play_all)
        self.header.shuffle_clicked.connect(self._shuffle_all)

        self.model = TrackTableModel(liked, self)
        self.table = TrackTable(self.model, images, liked, controller)
        self.table.play_requested.connect(self._play_from)
        self.table.verticalScrollBar().valueChanged.connect(self._scrolled)

        body = QVBoxLayout()
        body.setContentsMargins(16, 4, 12, 0)
        body.addWidget(self.table)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addLayout(body, 1)

    def set_playlist(self, playlist: Playlist) -> None:
        header = self.header
        header.set_title(playlist.title)
        header.kind.setText("Playlist")
        count = playlist.track_count or len(playlist.tracks)
        total = sum(t.duration or 0 for t in playlist.tracks)
        parts = [playlist.author or "", f"{count:,} songs" if count else ""]
        if playlist.complete and total:
            parts.append(format_duration(total))
        header.meta.setText(" • ".join(p for p in parts if p))
        if playlist.complete:
            header.status.setText("" if playlist.tracks else "No songs yet")
        else:
            header.status.setText(f"Loading… {len(playlist.tracks):,} of {count:,}")
        self.model.set_tracks(playlist.tracks)
        playable = any(t.available for t in playlist.tracks)
        header.play.setEnabled(playable)
        header.shuffle.setEnabled(playable)
        if playlist.id != LIKED_SONGS_ID:
            header.set_thumbnail(playlist.thumbnail)

    def tracks(self) -> list[Track]:
        return self.model.tracks

    def _scrolled(self, value: int) -> None:
        # Collapse after a real scroll and expand only at the very top, so the header
        # changing height (and the list with it) can't flip it back and forth.
        if value > PlaylistHeader.COLLAPSE_AT:
            self.header.set_collapsed(True)
        elif value == 0:
            self.header.set_collapsed(False)

    def _play_all(self) -> None:
        self._play_from(0)

    def _shuffle_all(self) -> None:
        if any(t.available for t in self.model.tracks):
            self.shuffle_requested.emit(self.model.tracks)

    def _play_from(self, row: int) -> None:
        tracks = self.model.tracks
        while row < len(tracks) and not tracks[row].available:
            row += 1
        if row < len(tracks):
            self.play_requested.emit(tracks, row)
