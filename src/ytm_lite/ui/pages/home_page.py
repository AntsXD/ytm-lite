"""Home: YouTube Music's home feed as a simple list of shelves (Quick picks, mixes, new
releases...), with small covers. A shelf shows its first few items; "Show all" opens it
up in place. A song plays with its radio after it, as in YouTube Music.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ytm_lite.app.api import Api
from ytm_lite.app.images import ImageLoader
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import HomeShelf, Track
from ytm_lite.app.playback import PlaybackController
from ytm_lite.app.workers import Channel, Workers
from ytm_lite.ui.result_list import ResultList, Row, item_row
from ytm_lite.ui.theme import font
from ytm_lite.ui.track_menu import Navigate

SHOWN = 5


def home_rows(shelves: tuple[HomeShelf, ...], expanded: set[int]) -> list[Row]:
    rows: list[Row] = []
    for index, shelf in enumerate(shelves):
        whole = index in expanded or len(shelf.items) <= SHOWN
        more = "" if whole else str(index)  # "Show all" opens this shelf up
        rows.append(Row("header", text=shelf.title, action=more))
        rows += [item_row(item) for item in (shelf.items if whole else shelf.items[:SHOWN])]
    return rows


class HomePage(QWidget):
    song_requested = Signal(object)  # Track: play it with its radio
    item_activated = Signal(object)  # an album, artist or playlist
    tint_changed = Signal(object)  # never: home has no cover of its own

    def __init__(
        self,
        workers: Workers,
        api: Api,
        images: ImageLoader,
        liked: LikedIndex,
        controller: PlaybackController,
        navigate: Navigate | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._channel = Channel(workers)
        self.destroyed.connect(self._channel.cancel)
        self._shelves: tuple[HomeShelf, ...] = ()
        self._expanded: set[int] = set()

        heading = QLabel("Home")
        heading.setFont(font(20, QFont.Weight.Bold))
        self.list = ResultList(images, liked, controller, navigate)
        self.list.song_activated.connect(self._song_activated)
        self.list.item_activated.connect(self.item_activated)
        self.list.show_all.connect(self._expand)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 12, 0)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addWidget(self.list, 1)
        self.reload()

    def tint(self) -> QColor | None:
        return None

    def reload(self) -> None:
        self.list.show_message("Loading…")
        self._channel.submit(self._api.home, on_done=self._got, on_error=self._failed)

    def _got(self, shelves: tuple[HomeShelf, ...]) -> None:
        self._shelves = shelves
        if shelves:
            self.list.set_rows(home_rows(shelves, self._expanded))
        else:
            self.list.show_message("YouTube Music has nothing for you here right now.")

    def _failed(self, error: Exception) -> None:
        self.list.show_message("Couldn't load your home feed. Check your connection.")

    def _expand(self, action: str) -> None:
        if action.isdigit():
            self._expanded.add(int(action))
            scroll = self.list.verticalScrollBar().value()
            self.list.set_rows(home_rows(self._shelves, self._expanded))
            self.list.verticalScrollBar().setValue(scroll)

    def _song_activated(self, tracks: list[Track], index: int) -> None:
        self.song_requested.emit(tracks[index])
