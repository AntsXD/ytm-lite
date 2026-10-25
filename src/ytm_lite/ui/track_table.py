"""Track lists: a QTableView over a QAbstractTableModel, with rows painted by delegates.

Covers are requested while painting, so only visible rows ever load one.
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
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
from ytm_lite.app.playback import PlaybackController
from ytm_lite.ui.theme import ACCENT

ROW_HEIGHT = 44
COVER = 34
TRACK_ROLE = Qt.ItemDataRole.UserRole
UNAVAILABLE = QColor("#666666")

AnyIndex = QModelIndex | QPersistentModelIndex


class Column(IntEnum):
    INDEX = 0
    TITLE = 1
    ARTIST = 2
    ALBUM = 3
    DURATION = 4
    LIKED = 5


_HEADERS = ("#", "Title", "Artist", "Album", "Time", "")


class TrackTableModel(QAbstractTableModel):
    def __init__(self, liked: LikedIndex, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._liked = liked
        self._tracks: list[Track] = []
        self._rows: dict[str, list[int]] = {}
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

    # -- Qt model API -----------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(Column)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
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
            if column == Column.ARTIST:
                return track.artist_text
            if column == Column.ALBUM:
                return track.album.name if track.album else ""
            if column == Column.DURATION:
                return format_duration(track.duration) if track.duration else ""
            return None
        if role == Qt.ItemDataRole.ToolTipRole and column in (
            Column.TITLE,
            Column.ARTIST,
            Column.ALBUM,
        ):
            parts = [track.title, track.artist_text, track.album.name if track.album else ""]
            tip = "\n".join(p for p in parts if p)
            return tip if track.available else f"{tip}\n(unavailable)"
        if role == Qt.ItemDataRole.ForegroundRole and not track.available:
            return UNAVAILABLE
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (Column.INDEX, Column.DURATION):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _liked_changed(self, video_id: str) -> None:
        for row in self._rows.get(video_id, ()):
            index = self.index(row, Column.LIKED)
            self.dataChanged.emit(index, index)

    def _liked_reset(self) -> None:
        if self._tracks:
            last = len(self._tracks) - 1
            self.dataChanged.emit(self.index(0, Column.LIKED), self.index(last, Column.LIKED))


def _draw_background(option: QStyleOptionViewItem, painter: QPainter, index: AnyIndex) -> None:
    widget = option.widget
    style = widget.style() if widget is not None else QApplication.style()
    opt = QStyleOptionViewItem(option)
    opt.text = ""
    style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)


def _text_color(option: QStyleOptionViewItem, track: Track) -> QColor:
    if not track.available:
        return UNAVAILABLE
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
    return option.palette.color(role)


class TitleDelegate(QStyledItemDelegate):
    """Cover thumbnail plus title."""

    def __init__(self, images: ImageLoader, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._images = images

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        track: Track = index.data(TRACK_ROLE)
        _draw_background(option, painter, index)
        rect: QRect = option.rect
        cover = QRect(rect.left() + 6, rect.top() + (rect.height() - COVER) // 2, COVER, COVER)
        px = round(COVER * painter.device().devicePixelRatioF())
        pixmap = self._images.get(track.thumbnail, px)
        if pixmap is None:
            painter.fillRect(cover, QColor("#2a2a2a"))
        else:
            side = min(pixmap.width(), pixmap.height())  # centre-crop non-square video thumbnails
            source = QRect((pixmap.width() - side) // 2, (pixmap.height() - side) // 2, side, side)
            painter.drawPixmap(cover, pixmap, source)
        text_rect = rect.adjusted(COVER + 16, 0, -6, 0)
        metrics = option.fontMetrics
        text = metrics.elidedText(track.title, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.save()
        painter.setPen(_text_color(option, track))
        painter.drawText(
            text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text
        )
        painter.restore()


class HeartDelegate(QStyledItemDelegate):
    """♥ / ♡; a click toggles the like."""

    def __init__(self, liked: LikedIndex, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._liked = liked

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        track: Track = index.data(TRACK_ROLE)
        _draw_background(option, painter, index)
        liked = self._liked.is_liked(track.video_id)
        painter.save()
        painter.setPen(QColor(ACCENT) if liked else QColor("#7a7a7a"))
        painter.drawText(
            option.rect,
            int(Qt.AlignmentFlag.AlignCenter),
            "♥" if liked else "♡",
        )
        painter.restore()

    def editorEvent(
        self, event: QEvent, model: Any, option: QStyleOptionViewItem, index: AnyIndex
    ) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
            and option.rect.contains(event.position().toPoint())
        ):
            self._liked.toggle(index.data(TRACK_ROLE))
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
        self._model = model
        self.setItemDelegateForColumn(Column.TITLE, TitleDelegate(images, self))
        self.setItemDelegateForColumn(Column.LIKED, HeartDelegate(liked, self))

        vertical = self.verticalHeader()
        vertical.hide()
        vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vertical.setDefaultSectionSize(ROW_HEIGHT)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(False)
        widths = {
            Column.INDEX: 48,
            Column.ARTIST: 220,
            Column.ALBUM: 220,
            Column.DURATION: 64,
            Column.LIKED: 40,
        }
        for column, width in widths.items():
            fixed = column in (Column.INDEX, Column.DURATION, Column.LIKED)
            mode = QHeaderView.ResizeMode.Fixed if fixed else QHeaderView.ResizeMode.Interactive
            header.setSectionResizeMode(column, mode)
            self.setColumnWidth(column, width)
        header.setSectionResizeMode(Column.TITLE, QHeaderView.ResizeMode.Stretch)

        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setFrameShape(QTableView.Shape.NoFrame)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.doubleClicked.connect(lambda index: self._play(index.row()))

        # Repaint once per burst of finished cover downloads. A bound method (not a
        # lambda), so Qt drops the connection when this table is deleted.
        self._repaint = QTimer(self)
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(40)
        self._repaint.timeout.connect(self.viewport().update)
        images.loaded.connect(self._image_loaded)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Split the width left by the fixed columns 2:1:1 between title, artist and album,
        so a narrow window (say, with the queue open) never squeezes the title out."""
        fixed = sum(self.columnWidth(c) for c in (Column.INDEX, Column.DURATION, Column.LIKED))
        side = max(80, min(260, (self.viewport().width() - fixed) // 4))
        for column in (Column.ARTIST, Column.ALBUM):
            self.setColumnWidth(column, side)

    def _image_loaded(self, _key: str) -> None:
        self._repaint.start()

    def _play(self, row: int) -> None:
        track = self._model.track(row)
        if track is not None and track.available:
            self.play_requested.emit(row)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self._play(self.currentIndex().row())
            return
        super().keyPressEvent(event)

    def _selected_tracks(self, row: int) -> list[Track]:
        """The selected rows in order if `row` is among them, else just that row."""
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        if row not in rows:
            rows = [row]
        return [t for r in rows if (t := self._model.track(r)) is not None and t.available]

    def _context_menu(self, pos: QPoint) -> None:
        index = self.indexAt(pos)
        track = self._model.track(index.row())
        if track is None:
            return
        tracks = self._selected_tracks(index.row())
        controller = self._controller
        menu = QMenu(self)
        play = menu.addAction("Play")
        play.setEnabled(track.available)
        play.triggered.connect(lambda: self._play(index.row()))
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
