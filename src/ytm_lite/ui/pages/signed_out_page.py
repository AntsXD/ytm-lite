"""Shown when signed out: a way to sign in, and the built-in test tracks until search exists."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ytm_lite.app.dev import TEST_TRACKS
from ytm_lite.app.images import ImageLoader
from ytm_lite.app.liked import LikedIndex
from ytm_lite.ui.track_table import TrackTable, TrackTableModel


class SignedOutPage(QWidget):
    sign_in_clicked = Signal()
    play_requested = Signal(object, int)  # list[Track], start row

    def __init__(
        self, images: ImageLoader, liked: LikedIndex, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        heading = QLabel("Sign in to see your library")
        font = QFont(heading.font())
        font.setPointSizeF(font.pointSizeF() * 1.8)
        font.setBold(True)
        heading.setFont(font)
        body = QLabel(
            "Your liked songs, playlists, albums and artists appear here once you sign in. "
            "Signed out, you can still play music (search arrives in milestone 4)."
        )
        body.setWordWrap(True)
        body.setObjectName("Dim")
        button = QPushButton("Sign in…")
        button.clicked.connect(self.sign_in_clicked)
        row = QHBoxLayout()
        row.addWidget(button)
        row.addStretch(1)
        tracks_label = QLabel("Test tracks")
        tracks_label.setObjectName("Dim")
        model = TrackTableModel(liked, self)
        model.set_tracks(TEST_TRACKS)
        table = TrackTable(model, images, liked)
        table.play_requested.connect(lambda r: self.play_requested.emit(list(TEST_TRACKS), r))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 0)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addLayout(row)
        layout.addSpacing(16)
        layout.addWidget(tracks_label)
        layout.addWidget(table, 1)
