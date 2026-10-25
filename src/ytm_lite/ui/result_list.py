"""A list that mixes section headers, songs, albums, artists and playlists: search results
and artist pages. One view with one delegate, so long result lists stay cheap.

A click opens an album, artist or playlist; songs play on double-click or Enter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
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
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import (
    AlbumSummary,
    ArtistSummary,
    SearchItem,
    Track,
    format_duration,
)
from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackController
from ytm_lite.ui import icons
from ytm_lite.ui.theme import ACCENT, HOVER, SELECTED, TEXT, TEXT_DIM, TEXT_FAINT, font
from ytm_lite.ui.track_menu import Navigate, show_track_menu
from ytm_lite.ui.widgets import draw_cover

ROW_ROLE = Qt.ItemDataRole.UserRole
_HEIGHTS = {"header": 56, "song": 56, "message": 64}
_ITEM_HEIGHT = 68  # albums, artists, playlists

AnyIndex = QModelIndex | QPersistentModelIndex


@dataclass(frozen=True, slots=True)
class Row:
    kind: str  # "header", "song", "album", "artist", "playlist" or "message"
    item: SearchItem | None = None
    text: str = ""  # header or message text
    action: str = ""  # a header's "Show all" target
    context: tuple[Track, ...] = ()  # songs: the list this one plays from...
    index: int = -1  # ...and its place in it


def item_row(item: SearchItem) -> Row:
    if isinstance(item, Track):
        return Row("song", item, context=(item,), index=0)
    if isinstance(item, AlbumSummary):
        return Row("album", item)
    if isinstance(item, ArtistSummary):
        return Row("artist", item)
    return Row("playlist", item)


def song_rows(tracks: Sequence[Track]) -> list[Row]:
    context = tuple(tracks)
    return [Row("song", t, context=context, index=i) for i, t in enumerate(context)]


class ResultModel(QAbstractListModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[Row] = []

    def set_rows(self, rows: Sequence[Row]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.ToolTipRole and isinstance(row.item, Track):
            return "\n".join(p for p in (row.item.title, row.item.artist_text) if p)
        return None

    def flags(self, index: AnyIndex) -> Qt.ItemFlag:
        if index.isValid() and self.rows[index.row()].kind in ("header", "message"):
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


def _subtitle(item: SearchItem) -> str:
    if isinstance(item, Track):
        album = f" · {item.album.name}" if item.album else ""
        return item.artist_text + album
    if isinstance(item, AlbumSummary):
        return " · ".join(p for p in (item.kind, item.artist_text, item.year or "") if p)
    if isinstance(item, ArtistSummary):
        if item.audience:
            return f"Artist · {item.audience} monthly audience"
        return f"Artist · {item.subscribers} subscribers" if item.subscribers else "Artist"
    count = f"{item.count:,} songs" if item.count else ""
    return " · ".join(p for p in ("Playlist", item.author or "", count) if p)


def _title(item: SearchItem) -> str:
    return item.name if isinstance(item, ArtistSummary) else item.title


class ResultDelegate(QStyledItemDelegate):
    HEART = 18

    def __init__(self, view: ResultList, images: ImageLoader, liked: LikedIndex) -> None:
        super().__init__(view)
        self._view = view
        self._images = images
        self._liked = liked
        self._header_font = font(14, QFont.Weight.Bold)
        self._title_font = font(weight=QFont.Weight.Medium)
        self._small = font(9)

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        row: Row = index.data(ROW_ROLE)
        return QSize(option.rect.width(), _HEIGHTS.get(row.kind, _ITEM_HEIGHT))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        row: Row = index.data(ROW_ROLE)
        rect: QRect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if row.kind == "header":
            painter.setFont(self._header_font)
            painter.setPen(QColor(TEXT))
            painter.drawText(rect.adjusted(8, 14, -8, 0), align, row.text)
            if row.action:
                painter.setFont(self._small)
                painter.setPen(QColor(TEXT_DIM))
                painter.drawText(
                    self.show_all_rect(rect),
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                    "Show all",
                )
            painter.restore()
            return
        if row.kind == "message":
            painter.setPen(QColor(TEXT_DIM))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), row.text)
            painter.restore()
            return

        item = row.item
        assert item is not None
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SELECTED if selected else HOVER))
            painter.drawRoundedRect(QRectF(rect.adjusted(0, 1, 0, -1)), 6, 6)
        side = 40 if row.kind == "song" else 52
        cover = QRect(rect.left() + 8, rect.top() + (rect.height() - side) // 2, side, side)
        px = round(side * painter.device().devicePixelRatioF())
        draw_cover(
            painter, cover, self._images.get(item.thumbnail, px), round_=row.kind == "artist"
        )

        right = rect.right() - 10
        if isinstance(item, Track):
            liked = self._liked.is_liked(item.video_id)
            if liked or hovered:
                heart = self.heart_rect(rect)
                icons.paint(
                    painter,
                    "heart" if liked else "heart_outline",
                    heart,
                    ACCENT if liked else TEXT_DIM,
                )
            right = int(self.heart_rect(rect).left()) - 12
            if item.duration:
                painter.setFont(self._small)
                painter.setPen(QColor(TEXT_DIM))
                text = format_duration(item.duration)
                width = painter.fontMetrics().horizontalAdvance(text)
                painter.drawText(
                    QRect(right - width, rect.top(), width, rect.height()), align, text
                )
                right -= width + 16

        left = cover.right() + 14
        width = max(0, right - left)
        half = rect.height() // 2
        current = isinstance(item, Track) and item.video_id == self._view.current_video_id
        available = not isinstance(item, Track) or item.available
        painter.setFont(self._title_font)
        painter.setPen(QColor(ACCENT if current else TEXT if available else TEXT_FAINT))
        title = painter.fontMetrics().elidedText(_title(item), Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRect(left, rect.top() + 8, width, half - 8), align, title)
        painter.setFont(self._small)
        painter.setPen(QColor(TEXT_DIM if available else TEXT_FAINT))
        subtitle = painter.fontMetrics().elidedText(
            _subtitle(item), Qt.TextElideMode.ElideRight, width
        )
        painter.drawText(QRect(left, rect.top() + half, width, half - 8), align, subtitle)
        painter.restore()

    def heart_rect(self, rect: QRect) -> QRectF:
        side = self.HEART
        return QRectF(rect.right() - 12 - side, rect.center().y() - side / 2 + 0.5, side, side)

    @staticmethod
    def show_all_rect(rect: QRect) -> QRect:
        return QRect(rect.right() - 90, rect.top() + 14, 82, rect.height() - 14)

    def editorEvent(
        self, event: QEvent, model: Any, option: QStyleOptionViewItem, index: AnyIndex
    ) -> bool:
        if not (
            event.type() == QEvent.Type.MouseButtonRelease
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            return False
        row: Row = index.data(ROW_ROLE)
        pos = event.position().toPoint()
        if row.kind == "header" and row.action and self.show_all_rect(option.rect).contains(pos):
            self._view.show_all.emit(row.action)
            return True
        if isinstance(row.item, Track) and self.heart_rect(option.rect).contains(pos.toPointF()):
            self._liked.toggle(row.item)
            return True
        return False


class ResultList(QListView):
    song_activated = Signal(object, int)  # the list a song plays from, its index
    item_activated = Signal(object)  # AlbumSummary | ArtistSummary | PlaylistSummary
    show_all = Signal(str)

    def __init__(
        self,
        images: ImageLoader,
        liked: LikedIndex,
        controller: PlaybackController,
        navigate: Navigate | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._liked = liked
        self._controller = controller
        self._navigate = navigate
        self.result_model = ResultModel(self)
        self.setModel(self.result_model)
        self.setItemDelegate(ResultDelegate(self, images, liked))
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.clicked.connect(self._clicked)
        self.doubleClicked.connect(self._double_clicked)

        self._repaint = QTimer(self)  # once per burst of finished cover downloads
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(40)
        self._repaint.timeout.connect(self.viewport().update)
        images.loaded.connect(self._image_loaded)
        liked.changed.connect(self._liked_changed)
        controller.current_changed.connect(self._current_changed)
        current = controller.queue.current()
        self.current_video_id = current.track.video_id if current is not None else None

    def set_rows(self, rows: Sequence[Row]) -> None:
        self.result_model.set_rows(rows)

    def show_message(self, text: str) -> None:
        self.result_model.set_rows([Row("message", text=text)])

    def _image_loaded(self, _key: str) -> None:
        if self.isVisible():
            self._repaint.start()

    def _liked_changed(self, _video_id: str) -> None:
        self.viewport().update()

    def _current_changed(self, entry: QueueEntry | None) -> None:
        self.current_video_id = entry.track.video_id if entry is not None else None
        self.viewport().update()

    def _row(self, index: QModelIndex) -> Row | None:
        return index.data(ROW_ROLE) if index.isValid() else None

    def _clicked(self, index: QModelIndex) -> None:
        row = self._row(index)
        if row is not None and row.kind in ("album", "artist", "playlist"):
            self.item_activated.emit(row.item)

    def _double_clicked(self, index: QModelIndex) -> None:
        row = self._row(index)
        if row is not None and isinstance(row.item, Track) and row.item.available:
            self.song_activated.emit(list(row.context), row.index)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = self.currentIndex()
            self._double_clicked(index)
            self._clicked(index)
            return
        super().keyPressEvent(event)

    def _context_menu(self, pos: QPoint) -> None:
        index = self.indexAt(pos)
        row = self._row(index)
        if row is None or not isinstance(row.item, Track):
            return
        track = row.item
        show_track_menu(
            self,
            self.viewport().mapToGlobal(pos),
            track,
            [track],
            controller=self._controller,
            liked=self._liked,
            navigate=self._navigate,
            play=lambda: self._double_clicked(index),
        )
