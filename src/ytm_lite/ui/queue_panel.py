"""Queue panel: the play queue in play order, with drag-to-reorder.

The model shows a snapshot of the queue, refreshed on `queue_changed`, so the view is
never told about rows that changed underneath it. A drop moves the dragged rows one at
a time through the controller (`moveRows`), which keeps the selection and scroll
position instead of resetting the list.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QDropEvent,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.models import format_duration
from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackController
from ytm_lite.ui.theme import ACCENT, HOVER, SELECTED, TEXT, TEXT_DIM, TEXT_FAINT, font
from ytm_lite.ui.widgets import ToggleSwitch, draw_cover

ROW_HEIGHT = 56
COVER = 40
MIME_TYPE = "application/x-ytm-lite-queue"
ENTRY_ROLE = Qt.ItemDataRole.UserRole

AnyIndex = QModelIndex | QPersistentModelIndex


class QueueModel(QAbstractListModel):
    def __init__(self, controller: PlaybackController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._entries: Sequence[QueueEntry] = ()
        self._cursor = -1
        self._moving = False
        self._snapshot()
        controller.queue_changed.connect(self._on_queue_changed)
        controller.current_changed.connect(self._on_current_changed)

    @property
    def cursor(self) -> int:
        return self._cursor

    def entries(self) -> Sequence[QueueEntry]:
        return self._entries

    def entry(self, row: int) -> QueueEntry | None:
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def _snapshot(self) -> None:
        queue = self._controller.queue
        self._entries = queue.items
        self._cursor = queue.cursor

    def _row_of(self, uid: int) -> int:
        for row, entry in enumerate(self._entries):
            if entry.uid == uid:
                return row
        return -1

    def _on_queue_changed(self) -> None:
        if self._moving:
            return
        self.beginResetModel()
        self._snapshot()
        self.endResetModel()

    def _on_current_changed(self, entry: QueueEntry | None) -> None:
        new = self._row_of(entry.uid) if entry is not None else -1
        old, self._cursor = self._cursor, new
        if new < 0 or old == new:
            return  # a queue_changed (and a reset) follows when the snapshot is stale
        first, last = sorted((max(old, 0), new))
        self.dataChanged.emit(self.index(first), self.index(last))

    # -- Qt model API -------------------------------------------------------------------

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role == ENTRY_ROLE:
            return entry
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.track.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return "\n".join(p for p in (entry.track.title, entry.track.artist_text) if p)
        return None

    def flags(self, index: AnyIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled  # drops go between rows, never onto one
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        )

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [MIME_TYPE]

    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        data = QMimeData()
        uids = [self._entries[i.row()].uid for i in indexes if i.isValid()]
        data.setData(MIME_TYPE, json.dumps(uids).encode())
        return data

    def move_rows(self, rows: Sequence[int], row: int) -> None:
        """Move `rows` (a drag's selection) so they sit together, in order, before `row`."""
        uids = [self._entries[r].uid for r in sorted(set(rows)) if 0 <= r < len(self._entries)]
        target = row
        for uid in uids:
            source = self._row_of(uid)
            if target not in (source, source + 1):
                self.moveRows(QModelIndex(), source, 1, QModelIndex(), target)
            target = self._row_of(uid) + 1

    def moveRows(
        self,
        source_parent: AnyIndex,
        source_row: int,
        count: int,
        destination_parent: AnyIndex,
        destination_child: int,
    ) -> bool:
        """Move one row to before `destination_child` (counted before the move)."""
        if source_parent.isValid() or destination_parent.isValid() or count != 1:
            return False
        if not 0 <= source_row < len(self._entries):
            return False
        if destination_child in (source_row, source_row + 1):
            return False  # dropped where it already is
        uid = self._entries[source_row].uid
        # destination_child counts rows before the move; the queue wants the final index.
        target = destination_child - 1 if destination_child > source_row else destination_child
        if not self.beginMoveRows(
            QModelIndex(), source_row, source_row, QModelIndex(), destination_child
        ):
            return False
        self._moving = True
        try:
            self._controller.move(uid, target)
        finally:
            self._moving = False
        self._snapshot()
        self.endMoveRows()
        # Rows between the two places may have changed sides of the current entry.
        first, last = sorted((source_row, target))
        self.dataChanged.emit(self.index(first), self.index(last))
        return True


class QueueDelegate(QStyledItemDelegate):
    """Cover, title and artist, duration; played entries faded, the current one violet."""

    def __init__(
        self, images: ImageLoader, model: QueueModel, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._images = images
        self._model = model
        self._current_font = font(weight=QFont.Weight.DemiBold)
        self._title_font = font(weight=QFont.Weight.Medium)
        self._small = font(9)

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        entry: QueueEntry | None = index.data(ENTRY_ROLE)
        if entry is None:
            return
        rect: QRect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SELECTED if selected else HOVER))
            painter.drawRoundedRect(QRectF(rect.adjusted(0, 1, 0, -1)), 6, 6)

        cover = QRect(rect.left() + 8, rect.top() + (rect.height() - COVER) // 2, COVER, COVER)
        px = round(COVER * painter.device().devicePixelRatioF())
        draw_cover(painter, cover, self._images.get(entry.track.thumbnail, px))

        row, cursor = index.row(), self._model.cursor
        played, current = row < cursor, row == cursor
        title_color = QColor(ACCENT if current else TEXT_FAINT if played else TEXT)
        sub_color = QColor(TEXT_FAINT if played else TEXT_DIM)

        painter.setFont(self._small)  # measure with the painter: see track_table._text
        small_metrics = painter.fontMetrics()
        duration = format_duration(entry.track.duration) if entry.track.duration else ""
        time_width = small_metrics.horizontalAdvance(duration) + 12 if duration else 0
        text = QRect(cover.right() + 12, rect.top() + 9, 0, rect.height() - 18)
        text.setRight(rect.right() - 10 - time_width)
        half = text.height() // 2
        top, bottom = QRect(text), QRect(text)
        top.setHeight(half)
        bottom.setTop(text.top() + half)

        align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        elide = Qt.TextElideMode.ElideRight
        title_font = self._current_font if current else self._title_font
        painter.setFont(title_font)
        painter.setPen(title_color)
        title = painter.fontMetrics().elidedText(entry.track.title, elide, top.width())
        painter.drawText(top, align, title)
        painter.setFont(self._small)
        painter.setPen(sub_color)
        artist = small_metrics.elidedText(entry.track.artist_text, elide, bottom.width())
        painter.drawText(bottom, align, artist)
        if duration:
            right = QRect(rect.right() - 10 - time_width, rect.top(), time_width, rect.height())
            painter.drawText(
                right, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), duration
            )
        painter.restore()


class QueueView(QListView):
    def __init__(
        self,
        model: QueueModel,
        controller: PlaybackController,
        images: ImageLoader,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._model = model
        self.setModel(model)
        self.setItemDelegate(QueueDelegate(images, model, self))
        self.setUniformItemSizes(True)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.doubleClicked.connect(self._activated)

        self._repaint = QTimer(self)  # once per burst of finished cover downloads
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(40)
        self._repaint.timeout.connect(self.viewport().update)
        images.loaded.connect(self._image_loaded)

    def _image_loaded(self, _key: str) -> None:
        if self.isVisible():
            self._repaint.start()

    def _activated(self, index: QModelIndex) -> None:
        entry = self._model.entry(index.row())
        if entry is not None and entry.track.available:
            self._controller.play_entry(entry.uid)

    def _removable(self) -> list[int]:
        """Selected entries except the one playing (it stays until something replaces it)."""
        current = self._model.entry(self._model.cursor)
        uids = [
            e.uid for i in self.selectedIndexes() if (e := self._model.entry(i.row())) is not None
        ]
        return [uid for uid in uids if current is None or uid != current.uid]

    def dropEvent(self, event: QDropEvent) -> None:
        """Move the dragged rows (the selection) to the drop indicator."""
        if event.source() is not self:
            event.ignore()
            return
        index = self.indexAt(event.position().toPoint())
        where = self.dropIndicatorPosition()
        indicator = QAbstractItemView.DropIndicatorPosition
        if not index.isValid() or where == indicator.OnViewport:
            row = self._model.rowCount()
        elif where == indicator.BelowItem:
            row = index.row() + 1
        else:
            row = index.row()
        self._model.move_rows([i.row() for i in self.selectedIndexes()], row)
        # After a move the view asks the model to remove the dragged rows (as it would
        # between two views); QueueModel has no removeRows, so that does nothing.
        event.acceptProposedAction()
        self.stopAutoScroll()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self._controller.remove(self._removable())
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self._activated(self.currentIndex())
            return
        super().keyPressEvent(event)

    def _context_menu(self, pos: QPoint) -> None:
        index = self.indexAt(pos)
        entry = self._model.entry(index.row())
        if entry is None:
            return
        is_current = index.row() == self._model.cursor
        menu = QMenu(self)
        play = menu.addAction("Play")
        play.setEnabled(entry.track.available and not is_current)
        play.triggered.connect(lambda: self._controller.play_entry(entry.uid))
        play_next = menu.addAction("Play next")
        play_next.setEnabled(not is_current)
        play_next.triggered.connect(lambda: self._controller.move_next(entry.uid))
        removable = self._removable()
        remove = menu.addAction(
            "Remove from queue" if len(removable) <= 1 else f"Remove {len(removable)} songs"
        )
        remove.setEnabled(bool(removable))
        remove.triggered.connect(lambda: self._controller.remove(removable))
        menu.addSeparator()
        menu.addAction("Copy link").triggered.connect(
            lambda: QGuiApplication.clipboard().setText(entry.track.link)
        )
        menu.exec(self.viewport().mapToGlobal(pos))

    def current_visible(self) -> bool:
        index = self._model.index(self._model.cursor)
        return index.isValid() and self.viewport().rect().intersects(self.visualRect(index))

    def show_current(self) -> None:
        """Scroll so the previous song is at the top and the current one below it."""
        if self._model.cursor >= 0:
            index = self._model.index(max(0, self._model.cursor - 1))
            self.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtTop)


class QueuePanel(QWidget):
    """Header (count, Clear, Autoplay) above the queue list."""

    def __init__(
        self, controller: PlaybackController, images: ImageLoader, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("QueuePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._controller = controller
        self.model = QueueModel(controller, self)
        self.view = QueueView(self.model, controller, images)

        title = QLabel("Queue")
        title.setFont(font(13, QFont.Weight.Bold))
        self.summary = QLabel("")
        self.summary.setObjectName("Dim")
        self.summary.setFont(font(9))
        clear = QPushButton("Clear")
        clear.setObjectName("Flat")
        clear.setToolTip("Remove everything except the current song")
        clear.clicked.connect(controller.clear_queue)
        self.autoplay = ToggleSwitch("Autoplay")
        self.autoplay.setToolTip("When the queue runs out, keep playing similar songs")
        self.autoplay.toggled.connect(controller.set_radio)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.autoplay)
        top.addWidget(clear)
        header = QVBoxLayout()
        header.setContentsMargins(16, 14, 10, 8)
        header.setSpacing(2)
        header.addLayout(top)
        header.addWidget(self.summary)
        body = QVBoxLayout()
        body.setContentsMargins(8, 0, 4, 8)
        body.addWidget(self.view)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addLayout(body, 1)

        # Follow the current song while it is in view; once you scroll it out of view,
        # the list stays where you put it.
        self._follow = True
        self.view.verticalScrollBar().actionTriggered.connect(self._scrolled)
        self.model.modelReset.connect(self._reset)
        controller.queue_changed.connect(self._update_summary)
        controller.current_changed.connect(self._current_changed)
        controller.modes_changed.connect(self._update_modes)
        self._update_summary()
        self._update_modes()

    def _scrolled(self, _action: int) -> None:
        QTimer.singleShot(0, self._update_follow)  # the scroll applies after this signal

    def _update_follow(self) -> None:
        self._follow = self.view.current_visible()

    def _reset(self) -> None:
        if self._follow and self.isVisible():
            QTimer.singleShot(0, self.view.show_current)

    def _current_changed(self, _entry: QueueEntry | None) -> None:
        self._update_summary()
        if self._follow and self.isVisible():
            self.view.show_current()

    def _update_modes(self) -> None:
        self.autoplay.blockSignals(True)
        self.autoplay.setChecked(self._controller.radio)
        self.autoplay.blockSignals(False)

    def _update_summary(self) -> None:
        queue = self._controller.queue
        upcoming = queue.upcoming()
        if not upcoming:
            self.summary.setText("Nothing up next" if len(queue) else "")
            return
        seconds = sum(e.track.duration or 0 for e in upcoming)
        count = f"{len(upcoming):,} up next"
        self.summary.setText(f"{count} · {format_duration(seconds)}" if seconds else count)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._follow = True
        QTimer.singleShot(0, self.view.show_current)
