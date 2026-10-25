"""Search results: All (a few of each kind, top result first), or one kind per tab.

Each tab searches the first time it's opened; a song plays with its radio after it,
the way YouTube Music does.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ytm_lite.app.api import Api
from ytm_lite.app.images import ImageLoader
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import SearchResults, Track
from ytm_lite.app.playback import PlaybackController
from ytm_lite.app.workers import Channel, Workers
from ytm_lite.ui.result_list import ResultList, Row, item_row
from ytm_lite.ui.theme import font
from ytm_lite.ui.track_menu import Navigate

FILTERS: tuple[tuple[str, str | None], ...] = (
    ("All", None),
    ("Songs", "songs"),
    ("Albums", "albums"),
    ("Artists", "artists"),
    ("Playlists", "playlists"),
)
SONGS_IN_ALL = 5
OTHERS_IN_ALL = 4


def _section(title: str, action: str, rows: list[Row], shown: int) -> list[Row]:
    if not rows:
        return []
    more = action if len(rows) > shown else ""  # a "Show all" link to that tab
    return [Row("header", text=title, action=more), *rows[:shown]]


def result_rows(results: SearchResults) -> list[Row]:
    songs = [item_row(t) for t in results.songs]
    albums = [item_row(a) for a in results.albums]
    artists = [item_row(a) for a in results.artists]
    playlists = [item_row(p) for p in results.playlists]
    if results.filter is None:
        rows: list[Row] = []
        if results.top is not None:
            rows += [Row("header", text="Top result"), item_row(results.top)]
        rows += _section("Songs", "songs", songs, SONGS_IN_ALL)
        rows += _section("Albums", "albums", albums, OTHERS_IN_ALL)
        rows += _section("Artists", "artists", artists, OTHERS_IN_ALL)
        rows += _section("Playlists", "playlists", playlists, OTHERS_IN_ALL)
        return rows
    tabs = {"songs": songs, "albums": albums, "artists": artists, "playlists": playlists}
    return tabs.get(results.filter, [])


class SearchPage(QWidget):
    song_requested = Signal(object)  # Track: play it with its radio
    item_activated = Signal(object)  # an album, artist or playlist
    tint_changed = Signal(object)  # never: search results have no cover

    def __init__(
        self,
        query: str,
        workers: Workers,
        api: Api,
        images: ImageLoader,
        liked: LikedIndex,
        controller: PlaybackController,
        navigate: Navigate | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.query = query
        self._api = api
        self._channel = Channel(workers)
        self.destroyed.connect(self._channel.cancel)
        self._results: dict[str | None, SearchResults] = {}
        self._filter: str | None = None

        heading = QLabel(f"Results for “{query}”")
        heading.setFont(font(20, QFont.Weight.Bold))
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str | None, QPushButton] = {}
        for label, filter_ in FILTERS:
            button = QPushButton(label)
            button.setObjectName("Chip")
            button.setCheckable(True)
            button.clicked.connect(self._chip_clicked)
            button.setProperty("filter", filter_ or "")
            self._group.addButton(button)
            self._buttons[filter_] = button
            chips.addWidget(button)
        chips.addStretch(1)

        self.list = ResultList(images, liked, controller, navigate)
        self.list.song_activated.connect(self._song_activated)
        self.list.item_activated.connect(self.item_activated)
        self.list.show_all.connect(self.select)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 12, 0)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addLayout(chips)
        layout.addWidget(self.list, 1)
        self.select(None)

    def tint(self) -> QColor | None:
        return None

    def select(self, filter_: str | None) -> None:
        """Show a tab, searching the first time it's opened."""
        filter_ = filter_ or None
        self._filter = filter_
        self._buttons[filter_].setChecked(True)
        results = self._results.get(filter_)
        if results is not None:
            self._show(results)
            return
        self.list.show_message("Searching…")
        api, query = self._api, self.query

        def search() -> SearchResults:
            return api.search(query, filter_)

        self._channel.submit(search, on_done=self._got, on_error=self._failed)

    def _chip_clicked(self) -> None:
        button = self._group.checkedButton()
        if button is not None:
            self.select(str(button.property("filter")) or None)

    def _got(self, results: SearchResults) -> None:
        self._results[results.filter] = results
        if results.filter == self._filter:
            self._show(results)

    def _show(self, results: SearchResults) -> None:
        rows = result_rows(results)
        if rows:
            self.list.set_rows(rows)
        else:
            self.list.show_message(f"No results for “{self.query}”")

    def _failed(self, error: Exception) -> None:
        self.list.show_message("Couldn't search right now. Check your connection and try again.")

    def _song_activated(self, tracks: list[Track], index: int) -> None:
        self.song_requested.emit(tracks[index])
