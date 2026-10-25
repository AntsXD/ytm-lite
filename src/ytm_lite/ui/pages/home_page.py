"""Home: YouTube Music's home feed, laid out like YouTube Music's own.

Each shelf is a row that scrolls sideways (its arrows, Shift+wheel or a touchpad; the
plain wheel scrolls the page): big covers for albums, playlists and artists, and columns
of four songs for shelves of songs (Quick picks). Clicking a cover opens it; its play
button, shown on hover, plays it straight away; a song plays with its radio after it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QScrollArea,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.api import Api
from ytm_lite.app.images import ImageLoader
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import (
    AlbumSummary,
    ArtistSummary,
    HomeShelf,
    SearchItem,
    Track,
)
from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackController
from ytm_lite.app.workers import Channel, Workers
from ytm_lite.ui import icons
from ytm_lite.ui.theme import ACCENT, ACCENT_FILL, HOVER, TEXT, TEXT_DIM, font
from ytm_lite.ui.track_menu import Navigate, show_track_menu
from ytm_lite.ui.widgets import draw_cover

AnyIndex = QModelIndex | QPersistentModelIndex
ITEM_ROLE = Qt.ItemDataRole.UserRole

CARD_COVER = 160
CARD_PAD = 8
CARD_SIZE = QSize(CARD_COVER + 2 * CARD_PAD, CARD_COVER + 2 * CARD_PAD + 58)
SONG_SIZE = QSize(300, 58)
SONG_COVER = 46
SONG_ROWS = 4
PLAY_SIDE = 40
TINT = QColor("#2b2150")  # a dusk violet behind the greeting


def greeting(now: datetime, name: str | None = None) -> str:
    part = "morning" if 5 <= now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    first = name.split()[0] if name and name.strip() else None
    return f"Good {part}, {first}" if first else f"Good {part}"


def is_song_shelf(shelf: HomeShelf) -> bool:
    return all(isinstance(item, Track) for item in shelf.items)


def subtitle(item: SearchItem) -> str:
    if isinstance(item, Track):
        return item.artist_text
    if isinstance(item, AlbumSummary):
        return " • ".join(p for p in (item.kind, item.artist_text, item.year or "") if p)
    if isinstance(item, ArtistSummary):
        return "Artist"
    return item.author or "Playlist"


def title_of(item: SearchItem) -> str:
    return item.name if isinstance(item, ArtistSummary) else item.title


def two_lines(metrics: QFontMetrics, text: str, width: int) -> list[str]:
    """Word-wrap `text` into at most two lines, the second elided if it runs over."""
    words = text.split()
    first = ""
    while words and metrics.horizontalAdvance(f"{first} {words[0]}".strip()) <= width:
        first = f"{first} {words.pop(0)}".strip()
    if not first:  # one long word
        return [metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)]
    rest = " ".join(words)
    if not rest:
        return [first]
    return [first, metrics.elidedText(rest, Qt.TextElideMode.ElideRight, width)]


class ShelfModel(QAbstractListModel):
    def __init__(self, items: Sequence[SearchItem], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.items = list(items)

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role == ITEM_ROLE:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return title_of(item)
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{title_of(item)}\n{subtitle(item)}".strip()
        return None


class ShelfView(QListView):
    """A row of items that scrolls sideways; the wheel scrolls the page unless it's a
    sideways (touchpad) or Shift+wheel movement."""

    opened = Signal(object)  # SearchItem: open it (or play a song)
    play = Signal(object)  # SearchItem: play it now

    def __init__(self, shelf: HomeShelf, images: ImageLoader, songs: bool) -> None:
        super().__init__()
        self.songs = songs
        self.hovered = -1
        self.current_video_id: str | None = None
        self.shelf_model = ShelfModel(shelf.items, self)
        self.setModel(self.shelf_model)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setMovement(QListView.Movement.Static)
        if songs:
            self.setFlow(QListView.Flow.TopToBottom)
            self.setWrapping(True)
            self.setFixedHeight(SONG_ROWS * SONG_SIZE.height() + 4)  # a column wraps at full
            self.setItemDelegate(SongDelegate(self, images))
        else:
            self.setFlow(QListView.Flow.LeftToRight)
            self.setWrapping(False)
            self.setFixedHeight(CARD_SIZE.height())
            self.setItemDelegate(CardDelegate(self, images))
        self._repaint = QTimer(self)  # once per burst of finished cover downloads
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(40)
        self._repaint.timeout.connect(self.viewport().update)
        images.loaded.connect(self._image_loaded)

    def _image_loaded(self, _key: str) -> None:
        if self.isVisible():
            self._repaint.start()

    def scroll_page(self, direction: int) -> None:
        """Scroll by the items that fit, landing on an item's edge."""
        bar = self.horizontalScrollBar()
        step = (SONG_SIZE if self.songs else CARD_SIZE).width() + self.spacing()
        per_page = max(1, self.viewport().width() // step)
        target = (bar.value() // step + direction * per_page) * step
        bar.setValue(max(bar.minimum(), min(bar.maximum(), target)))

    def item_at(self, pos: QPoint) -> SearchItem | None:
        index = self.indexAt(pos)
        return index.data(ITEM_ROLE) if index.isValid() else None

    def play_rect(self, rect: QRect) -> QRect:
        """Where the play button is drawn on an item (in viewport coordinates)."""
        if self.songs:
            return QRect(rect.x() + 6, rect.y() + 6, SONG_COVER, SONG_COVER)
        cover = QRect(rect.x() + CARD_PAD, rect.y() + CARD_PAD, CARD_COVER, CARD_COVER)
        return QRect(
            cover.right() - PLAY_SIDE - 8, cover.bottom() - PLAY_SIDE - 8, PLAY_SIDE, PLAY_SIDE
        )

    # -- mouse ------------------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta()
        sideways = delta.x() != 0 or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if not sideways:
            event.ignore()  # let the page scroll
            return
        amount = delta.x() or delta.y()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - amount)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        row = self.indexAt(event.position().toPoint()).row()
        if row != self.hovered:
            self.hovered = row
            self.viewport().update()
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if row >= 0 else Qt.CursorShape.ArrowCursor
        )

    def leaveEvent(self, event: Any) -> None:
        super().leaveEvent(event)
        self.hovered = -1
        self.viewport().update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        if not index.isValid():
            return
        item: SearchItem = index.data(ITEM_ROLE)
        if isinstance(item, Track) or self.play_rect(self.visualRect(index)).contains(pos):
            self.play.emit(item)
        else:
            self.opened.emit(item)


class CardDelegate(QStyledItemDelegate):
    def __init__(self, view: ShelfView, images: ImageLoader) -> None:
        super().__init__(view)
        self._view = view
        self._images = images
        self._title = font(10, QFont.Weight.DemiBold)
        self._small = font(9)

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        return CARD_SIZE

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        item: SearchItem = index.data(ITEM_ROLE)
        rect: QRect = option.rect
        hovered = index.row() == self._view.hovered
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(HOVER))
            painter.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 8, 8)
        cover = QRect(rect.x() + CARD_PAD, rect.y() + CARD_PAD, CARD_COVER, CARD_COVER)
        artist = isinstance(item, ArtistSummary)
        px = round(CARD_COVER * painter.device().devicePixelRatioF())
        draw_cover(painter, cover, self._images.get(item.thumbnail, px), 6, round_=artist)
        if hovered and not artist:
            _play_button(painter, self._view.play_rect(rect))
        align = Qt.AlignmentFlag.AlignHCenter if artist else Qt.AlignmentFlag.AlignLeft
        y = cover.bottom() + 8
        painter.setFont(self._title)
        metrics = QFontMetrics(self._title)
        painter.setPen(QColor(TEXT))
        lines = two_lines(metrics, title_of(item), CARD_COVER)
        for line in lines:
            painter.drawText(QRect(cover.x(), y, CARD_COVER, metrics.height()), int(align), line)
            y += metrics.height()
        painter.setFont(self._small)
        small = QFontMetrics(self._small)
        painter.setPen(QColor(TEXT_DIM))
        text = small.elidedText(subtitle(item), Qt.TextElideMode.ElideRight, CARD_COVER)
        painter.drawText(QRect(cover.x(), y + 2, CARD_COVER, small.height()), int(align), text)
        painter.restore()


class SongDelegate(QStyledItemDelegate):
    def __init__(self, view: ShelfView, images: ImageLoader) -> None:
        super().__init__(view)
        self._view = view
        self._images = images
        self._title = font(10, QFont.Weight.Medium)
        self._small = font(9)

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        return SONG_SIZE

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        track: Track = index.data(ITEM_ROLE)
        rect: QRect = option.rect
        hovered = index.row() == self._view.hovered
        playing = track.video_id == self._view.current_video_id
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(HOVER))
            painter.drawRoundedRect(QRectF(rect).adjusted(1, 1, -4, -1), 6, 6)
        cover = self._view.play_rect(rect)
        px = round(SONG_COVER * painter.device().devicePixelRatioF())
        draw_cover(painter, cover, self._images.get(track.thumbnail, px), 4)
        if hovered or playing:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.drawRoundedRect(QRectF(cover), 4, 4)
            glyph = QRectF(cover).adjusted(11, 11, -11, -11)
            icons.paint(painter, "music_note" if playing and not hovered else "play", glyph, TEXT)
        x = cover.right() + 12
        width = rect.right() - x - 10
        painter.setFont(self._title)
        metrics = QFontMetrics(self._title)
        painter.setPen(QColor(ACCENT if playing else TEXT))
        title = metrics.elidedText(track.title, Qt.TextElideMode.ElideRight, width)
        top = rect.y() + (rect.height() - 2 * metrics.height()) // 2
        painter.drawText(QRect(x, top, width, metrics.height()), 0, title)
        painter.setFont(self._small)
        small = QFontMetrics(self._small)
        painter.setPen(QColor(TEXT_DIM))
        artist = small.elidedText(subtitle(track), Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRect(x, top + metrics.height() + 1, width, small.height()), 0, artist)
        painter.restore()


def _play_button(painter: QPainter, rect: QRect) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ACCENT_FILL))
    painter.drawEllipse(QRectF(rect))
    glyph = QRectF(rect).adjusted(10, 10, -9, -10)
    icons.paint(painter, "play", glyph, "#ffffff")


def _arrow(name: str, tip: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("Round")
    button.setIcon(icons.icon(name, 20, icons.NORMAL))
    button.setIconSize(QSize(20, 20))
    button.setToolTip(tip)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


class Shelf(QWidget):
    """A shelf's title and arrows above its sideways row."""

    def __init__(self, shelf: HomeShelf, images: ImageLoader) -> None:
        super().__init__()
        self.view = ShelfView(shelf, images, songs=is_song_shelf(shelf))
        title = QLabel(shelf.title)
        title.setFont(font(17, QFont.Weight.Bold))
        self.back = _arrow("back", "Scroll back")
        self.forward = _arrow("forward", "Scroll on")
        self.back.clicked.connect(lambda: self.view.scroll_page(-1))
        self.forward.clicked.connect(lambda: self.view.scroll_page(1))
        bar = self.view.horizontalScrollBar()
        bar.valueChanged.connect(self._update_arrows)
        bar.rangeChanged.connect(self._update_arrows)
        header = QHBoxLayout()
        header.setContentsMargins(CARD_PAD, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.back)
        header.addWidget(self.forward)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.view)
        self._update_arrows()

    def _update_arrows(self, *_args: object) -> None:
        bar = self.view.horizontalScrollBar()
        self.back.setEnabled(bar.value() > bar.minimum())
        self.forward.setEnabled(bar.value() < bar.maximum())
        scrollable = bar.maximum() > bar.minimum()
        self.back.setVisible(scrollable)
        self.forward.setVisible(scrollable)


class HomePage(QWidget):
    song_requested = Signal(object)  # Track: play it with its radio
    item_activated = Signal(object)  # an album, artist or playlist: open it
    play_requested = Signal(object)  # an album, artist or playlist: play it now

    def __init__(
        self,
        workers: Workers,
        api: Api,
        images: ImageLoader,
        liked: LikedIndex,
        controller: PlaybackController,
        navigate: Navigate | None = None,
        account_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._images = images
        self._liked = liked
        self._controller = controller
        self._navigate = navigate
        self._channel = Channel(workers)
        self.destroyed.connect(self._channel.cancel)
        self.shelves: list[Shelf] = []

        self.heading = QLabel(greeting(datetime.now(), account_name))
        self.heading.setFont(font(26, QFont.Weight.Bold))
        self.message = QLabel("")
        self.message.setObjectName("Dim")
        self._content = QWidget()
        self._column = QVBoxLayout(self._content)
        self._column.setContentsMargins(24, 12, 16, 24)
        self._column.setSpacing(28)
        self._column.addWidget(self.heading)
        self._column.addWidget(self.message)
        self._column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(self._content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        self._content.setAutoFillBackground(False)
        self.scroll_area = scroll
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        controller.current_changed.connect(self._current_changed)
        self.reload()

    def tint(self) -> QColor | None:
        return TINT

    def reload(self) -> None:
        self.message.setText("Loading your home feed…")
        self.message.show()
        self._channel.submit(self._api.home, on_done=self._got, on_error=self._failed)

    def _got(self, shelves: tuple[HomeShelf, ...]) -> None:
        for old in self.shelves:
            old.deleteLater()
        self.shelves = []
        if not shelves:
            self.message.setText("YouTube Music has nothing for you here right now.")
            return
        self.message.hide()
        current = self._controller.queue.current()
        playing = current.track.video_id if current is not None else None
        for index, data in enumerate(shelves):
            shelf = Shelf(data, self._images)
            shelf.view.current_video_id = playing
            shelf.view.opened.connect(self.item_activated)
            shelf.view.play.connect(self._play)
            shelf.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            shelf.view.customContextMenuRequested.connect(self._context_menu)
            self._column.insertWidget(2 + index, shelf)
            self.shelves.append(shelf)

    def _failed(self, error: Exception) -> None:
        self.message.setText("Couldn't load your home feed. Check your connection.")

    def _play(self, item: SearchItem) -> None:
        if isinstance(item, Track):
            self.song_requested.emit(item)
        else:
            self.play_requested.emit(item)

    def _current_changed(self, entry: QueueEntry | None) -> None:
        playing = entry.track.video_id if entry is not None else None
        for shelf in self.shelves:
            shelf.view.current_video_id = playing
            shelf.view.viewport().update()

    def _context_menu(self, pos: QPoint) -> None:
        view = self.sender()
        if not isinstance(view, ShelfView):
            return
        item = view.item_at(pos)
        if not isinstance(item, Track):
            return
        show_track_menu(
            view,
            view.viewport().mapToGlobal(pos),
            item,
            [item],
            controller=self._controller,
            liked=self._liked,
            navigate=self._navigate,
            play=lambda: self.song_requested.emit(item),
        )
