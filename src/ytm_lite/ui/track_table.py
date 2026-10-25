"""Track lists: a QTableView over a QAbstractTableModel, with rows painted by one delegate.

Covers are requested while painting, so only visible rows ever load one. The view tracks
the row under the mouse itself, so the whole row lights up (an item view's own hover
state is per cell).
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import IntEnum
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import Track, format_duration
from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackController
from ytm_lite.ui import icons
from ytm_lite.ui.theme import ACCENT, HOVER, SELECTED, TEXT, TEXT_DIM, TEXT_FAINT, font
from ytm_lite.ui.widgets import draw_cover

ROW_HEIGHT = 56
COVER = 40
TRACK_ROLE = Qt.ItemDataRole.UserRole

AnyIndex = QModelIndex | QPersistentModelIndex


class Column(IntEnum):
    INDEX = 0
    TITLE = 1  # cover, title and artist
    ALBUM = 2
    DURATION = 3
    LIKED = 4


_HEADERS = ("#", "Title", "Album", "Time", "")


class TrackTableModel(QAbstractTableModel):
    def __init__(self, liked: LikedIndex, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._liked = liked
        self._tracks: list[Track] = []
        self._rows: dict[str, list[int]] = {}
        self.current_video_id: str | None = None  # the track playing now, drawn in violet
        liked.changed.connect(self._liked_changed)
        liked.reset.connect(self._liked_reset)

    @property
    def tracks(self) -> list[Track]:
        return self._tracks

    def set_tracks(self, tracks: Sequence[Track]) -> None:
        """Replace the rows; if the old rows are a prefix of the new ones, append instead,
        so scroll position and selection survive a playlist finishing loading."""
        old, new = self._tracks, list(tracks)
        if new == old:
            return
        is_prefix = 0 < len(old) < len(new) and all(
            a.video_id == b.video_id for a, b in zip(old, new, strict=False)
        )
        if is_prefix:
            self.beginInsertRows(QModelIndex(), len(old), len(new) - 1)
            self._tracks = new
            self._reindex()
            self.endInsertRows()
        else:
            self.beginResetModel()
            self._tracks = new
            self._reindex()
            self.endResetModel()

    def _reindex(self) -> None:
        self._rows = {}
        for row, track in enumerate(self._tracks):
            self._rows.setdefault(track.video_id, []).append(row)

    def track(self, row: int) -> Track | None:
        return self._tracks[row] if 0 <= row < len(self._tracks) else None

    def set_current(self, video_id: str | None) -> None:
        if video_id == self.current_video_id:
            return
        old, self.current_video_id = self.current_video_id, video_id
        for changed in (old, video_id):
            for row in self._rows.get(changed or "", ()):
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(Column) - 1))

    # -- Qt model API -----------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(Column)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if section == Column.INDEX:
                return int(Qt.AlignmentFlag.AlignCenter)
            if section == Column.DURATION:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        track = self._tracks[index.row()]
        column = index.column()
        if role == TRACK_ROLE:
            return track
        if role == Qt.ItemDataRole.DisplayRole:
            if column == Column.INDEX:
                return str(index.row() + 1)
            if column == Column.TITLE:
                return track.title
            if column == Column.ALBUM:
                return track.album.name if track.album else ""
            if column == Column.DURATION:
                return format_duration(track.duration) if track.duration else ""
            return None
        if role == Qt.ItemDataRole.ToolTipRole and column in (Column.TITLE, Column.ALBUM):
            parts = [track.title, track.artist_text, track.album.name if track.album else ""]
            tip = "\n".join(p for p in parts if p)
            return tip if track.available else f"{tip}\n(unavailable)"
        if role == Qt.ItemDataRole.ForegroundRole and not track.available:
            return QColor(TEXT_FAINT)
        return None

    def _liked_changed(self, video_id: str) -> None:
        for row in self._rows.get(video_id, ()):
            index = self.index(row, Column.LIKED)
            self.dataChanged.emit(index, index)

    def _liked_reset(self) -> None:
        if self._tracks:
            last = len(self._tracks) - 1
            self.dataChanged.emit(self.index(0, Column.LIKED), self.index(last, Column.LIKED))


class RowDelegate(QStyledItemDelegate):
    """Paints every cell of a row: background, then that column's part."""

    HEART = 18

    def __init__(self, table: TrackTable, images: ImageLoader, liked: LikedIndex) -> None:
        super().__init__(table)
        self._table = table
        self._images = images
        self._liked = liked
        self._title_font = font(weight=QFont.Weight.Medium)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        track: Track = index.data(TRACK_ROLE)
        table = self._table
        row, column = index.row(), index.column()
        rect: QRect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = row == table.hovered_row
        current = track.video_id == table.track_model.current_video_id
        if selected or hovered:
            self._paint_background(painter, rect, column, SELECTED if selected else HOVER)

        dim = QColor(TEXT_FAINT if not track.available else TEXT_DIM)
        if column == Column.INDEX:
            mark = QRectF(rect.center().x() - 8, rect.center().y() - 8, 16, 16)
            if current:
                icons.paint(painter, "volume", mark, ACCENT)
            elif hovered and track.available:
                icons.paint(painter, "play", mark, TEXT)
            else:
                painter.setPen(dim)
                painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), str(row + 1))
        elif column == Column.TITLE:
            self._paint_title(painter, option, track, current)
        elif column == Column.ALBUM:
            text = track.album.name if track.album else ""
            self._text(painter, option, rect.adjusted(8, 0, -8, 0), text, dim)
        elif column == Column.DURATION:
            text = format_duration(track.duration) if track.duration else ""
            painter.setPen(dim)
            painter.drawText(
                rect.adjusted(0, 0, -12, 0),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                text,
            )
        elif column == Column.LIKED:
            liked = self._liked.is_liked(track.video_id)
            if liked or hovered:
                icons.paint(
                    painter,
                    "heart" if liked else "heart_outline",
                    self._heart_rect(rect),
                    ACCENT if liked else TEXT_DIM,
                )

    @staticmethod
    def _paint_background(painter: QPainter, rect: QRect, column: int, color: str) -> None:
        """One rounded rectangle across the row: each cell paints its slice, with the
        corners only at the row's two ends."""
        shape = QRectF(rect)
        if column != Column.INDEX:
            shape.setLeft(shape.left() - 12)
        if column != Column.LIKED:
            shape.setRight(shape.right() + 12)
        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(shape, 6, 6)
        painter.restore()

    def _paint_title(
        self, painter: QPainter, option: QStyleOptionViewItem, track: Track, current: bool
    ) -> None:
        rect: QRect = option.rect
        cover = QRect(rect.left() + 4, rect.top() + (rect.height() - COVER) // 2, COVER, COVER)
        px = round(COVER * painter.device().devicePixelRatioF())
        draw_cover(painter, cover, self._images.get(track.thumbnail, px))
        left = cover.right() + 12
        text = QRect(left, rect.top() + 9, rect.right() - left - 8, rect.height() - 18)
        top, bottom = QRect(text), QRect(text)
        top.setHeight(text.height() // 2)
        bottom.setTop(text.top() + text.height() // 2)
        color = QColor(TEXT_FAINT if not track.available else ACCENT if current else TEXT)
        painter.save()
        painter.setFont(self._title_font)
        title = painter.fontMetrics().elidedText(
            track.title, Qt.TextElideMode.ElideRight, top.width()
        )
        painter.setPen(color)
        painter.drawText(
            top, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), title
        )
        painter.restore()
        dim = QColor(TEXT_FAINT if not track.available else TEXT_DIM)
        artist = track.artist_text + (" · unavailable" if not track.available else "")
        self._text(painter, option, bottom, artist, dim)

    @staticmethod
    def _text(
        painter: QPainter, option: QStyleOptionViewItem, rect: QRect, text: str, color: QColor
    ) -> None:
        # The painter's own metrics: elsewhere glyph widths round differently on
        # HiDPI screens, and the "…" of text elided with them gets clipped.
        painter.setFont(option.font)
        elided = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
        painter.setPen(color)
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), elided
        )

    def _heart_rect(self, rect: QRect) -> QRectF:
        side = self.HEART
        return QRectF(
            rect.center().x() - side / 2 + 0.5, rect.center().y() - side / 2 + 0.5, side, side
        )

    def editorEvent(
        self, event: QEvent, model: Any, option: QStyleOptionViewItem, index: AnyIndex
    ) -> bool:
        if not (
            event.type() == QEvent.Type.MouseButtonRelease
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
            and option.rect.contains(event.position().toPoint())
        ):
            return False
        track: Track = index.data(TRACK_ROLE)
        if index.column() == Column.LIKED:
            self._liked.toggle(track)
            return True
        if index.column() == Column.INDEX and track.available:
            self._table.play_row(index.row())
            return True
        return False


class TrackTable(QTableView):
    play_requested = Signal(int)  # row

    def __init__(
        self,
        model: TrackTableModel,
        images: ImageLoader,
        liked: LikedIndex,
        controller: PlaybackController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._liked = liked
        self._controller = controller
        self.setModel(model)
        self.track_model = model
        self.hovered_row = -1
        self.setItemDelegate(RowDelegate(self, images, liked))

        vertical = self.verticalHeader()
        vertical.hide()
        vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vertical.setDefaultSectionSize(ROW_HEIGHT)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(False)
        header.setMinimumHeight(34)
        widths = {Column.INDEX: 44, Column.ALBUM: 240, Column.DURATION: 70, Column.LIKED: 44}
        for column, width in widths.items():
            fixed = column != Column.ALBUM
            mode = QHeaderView.ResizeMode.Fixed if fixed else QHeaderView.ResizeMode.Interactive
            header.setSectionResizeMode(column, mode)
            self.setColumnWidth(column, width)
        header.setSectionResizeMode(Column.TITLE, QHeaderView.ResizeMode.Stretch)

        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setFrameShape(QTableView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.doubleClicked.connect(self._double_clicked)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

        # Repaint once per burst of finished cover downloads. Bound methods (not
        # lambdas), so Qt drops the connections when this table is deleted.
        self._repaint = QTimer(self)
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(40)
        self._repaint.timeout.connect(self.viewport().update)
        images.loaded.connect(self._image_loaded)
        controller.current_changed.connect(self._current_changed)
        self._current_changed(controller.queue.current())

    # -- painting state ----------------------------------------------------------------

    def _current_changed(self, entry: QueueEntry | None) -> None:
        self.track_model.set_current(entry.track.video_id if entry is not None else None)

    def _set_hovered(self, row: int) -> None:
        if row == self.hovered_row:
            return
        old, self.hovered_row = self.hovered_row, row
        width = self.viewport().width()
        for changed in (old, row):
            if changed >= 0:
                top = self.rowViewportPosition(changed)
                self.viewport().update(QRect(0, top, width, self.rowHeight(changed)))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        self._set_hovered(self.rowAt(int(event.position().y())))

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._set_hovered(-1)

    def _scrolled(self, _value: int) -> None:
        # The row under a still mouse changes as the list scrolls.
        pos = self.viewport().mapFromGlobal(QCursor.pos())
        inside = self.viewport().rect().contains(pos)
        self._set_hovered(self.rowAt(pos.y()) if inside else -1)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Album gets a third of the room the title doesn't need; narrow lists (say, with
        the queue open) drop the album column altogether."""
        width = self.viewport().width()
        fixed = sum(self.columnWidth(c) for c in (Column.INDEX, Column.DURATION, Column.LIKED))
        narrow = width < 560
        self.setColumnHidden(Column.ALBUM, narrow)
        if not narrow:
            self.setColumnWidth(Column.ALBUM, max(140, min(320, (width - fixed) // 3)))

    def _image_loaded(self, _key: str) -> None:
        self._repaint.start()

    # -- actions ---------------------------------------------------------------------------

    def play_row(self, row: int) -> None:
        track = self.track_model.track(row)
        if track is not None and track.available:
            self.play_requested.emit(row)

    def _double_clicked(self, index: QModelIndex) -> None:
        self.play_row(index.row())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.play_row(self.currentIndex().row())
            return
        super().keyPressEvent(event)

    def _selected_tracks(self, row: int) -> list[Track]:
        """The selected rows in order if `row` is among them, else just that row."""
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        if row not in rows:
            rows = [row]
        return [t for r in rows if (t := self.track_model.track(r)) is not None and t.available]

    def _context_menu(self, pos: QPoint) -> None:
        index = self.indexAt(pos)
        track = self.track_model.track(index.row())
        if track is None:
            return
        tracks = self._selected_tracks(index.row())
        controller = self._controller
        menu = QMenu(self)
        play = menu.addAction("Play")
        play.setEnabled(track.available)
        play.triggered.connect(lambda: self.play_row(index.row()))
        many = f" ({len(tracks)} songs)" if len(tracks) > 1 else ""
        play_next = menu.addAction(f"Play next{many}")
        play_next.setEnabled(bool(tracks))
        play_next.triggered.connect(lambda: controller.play_next(tracks))
        add = menu.addAction(f"Add to queue{many}")
        add.setEnabled(bool(tracks))
        add.triggered.connect(lambda: controller.add_to_queue(tracks))
        radio = menu.addAction("Start radio")
        radio.setEnabled(track.available)
        radio.triggered.connect(lambda: controller.start_radio(track))
        menu.addSeparator()
        like_text = "Remove from liked songs" if self._liked.is_liked(track.video_id) else "Like"
        menu.addAction(like_text).triggered.connect(lambda: self._liked.toggle(track))
        menu.addAction("Copy link").triggered.connect(
            lambda: QGuiApplication.clipboard().setText(track.link)
        )
        menu.exec(self.viewport().mapToGlobal(pos))
