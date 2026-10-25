"""An artist: a header with their picture, then top songs, albums, and singles and EPs."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import Artist, Track
from ytm_lite.app.playback import PlaybackController
from ytm_lite.ui.pages.playlist_page import TrackListHeader
from ytm_lite.ui.result_list import ResultList, Row, item_row, song_rows
from ytm_lite.ui.track_menu import Navigate

ALBUMS_SHOWN = 12


class ArtistPage(QWidget):
    play_requested = Signal(object, int)  # list[Track], start index
    shuffle_requested = Signal(object)  # list[Track]
    item_activated = Signal(object)  # an album
    tint_changed = Signal(object)  # QColor

    def __init__(
        self,
        channel_id: str,
        name: str,
        images: ImageLoader,
        liked: LikedIndex,
        controller: PlaybackController,
        navigate: Navigate | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.channel_id = channel_id
        self._top: tuple[Track, ...] = ()
        self.header = TrackListHeader(images, "Artist", round_cover=True)
        self.header.set_title(name)
        self.header.play.setToolTip("Play top songs")
        self.header.play_clicked.connect(self._play_top)
        self.header.shuffle_clicked.connect(self._shuffle_top)
        self.header.tint_changed.connect(self.tint_changed)
        self.list = ResultList(images, liked, controller, navigate)
        self.list.song_activated.connect(self.play_requested)
        self.list.item_activated.connect(self.item_activated)
        self.list.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.list.show_message("Loading…")

        body = QVBoxLayout()
        body.setContentsMargins(16, 4, 12, 0)
        body.addWidget(self.list)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addLayout(body, 1)

    def tint(self) -> QColor:
        return self.header.tint

    def set_artist(self, artist: Artist) -> None:
        header = self.header
        header.set_title(artist.name)
        counts = [
            f"{artist.subscribers} subscribers" if artist.subscribers else "",
            f"{artist.audience} monthly audience" if artist.audience else "",
        ]
        header.meta.setText(" • ".join(c for c in counts if c))
        header.status.setText("")
        header.set_thumbnail(artist.thumbnail)
        self._top = artist.top_songs
        playable = any(t.available for t in self._top)
        header.play.setEnabled(playable)
        header.shuffle.setEnabled(playable)
        rows: list[Row] = []
        if artist.top_songs:
            rows += [Row("header", text="Top songs"), *song_rows(artist.top_songs)]
        if artist.albums:
            rows.append(Row("header", text="Albums"))
            rows += [item_row(a) for a in artist.albums[:ALBUMS_SHOWN]]
        if artist.singles:
            rows.append(Row("header", text="Singles and EPs"))
            rows += [item_row(a) for a in artist.singles[:ALBUMS_SHOWN]]
        if rows:
            self.list.set_rows(rows)
        else:
            self.list.show_message("Nothing to play here yet")

    def show_error(self, message: str) -> None:
        self.header.status.setText("")
        self.list.show_message(message)

    def _play_top(self) -> None:
        for index, track in enumerate(self._top):
            if track.available:
                self.play_requested.emit(list(self._top), index)
                return

    def _shuffle_top(self) -> None:
        if any(t.available for t in self._top):
            self.shuffle_requested.emit(list(self._top))

    def _scrolled(self, value: int) -> None:
        if value > TrackListHeader.COLLAPSE_AT:
            self.header.set_collapsed(True)
        elif value == 0:
            self.header.set_collapsed(False)
