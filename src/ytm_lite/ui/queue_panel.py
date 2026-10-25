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
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QDropEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QPalette,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.models import format_duration
from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackController
from ytm_lite.ui.theme import ACCENT

ROW_HEIGHT = 48
COVER = 36
MIME_TYPE = "application/x-ytm-lite-queue"
ENTRY_ROLE = Qt.ItemDataRole.UserRole
PLAYED = QColor("#7a7a7a")

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
    """Cover, title and artist, duration; played entries dimmed, the current one red."""

    def __init__(
        self, images: ImageLoader, model: QueueModel, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._images = images
        self._model = model

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        entry: QueueEntry | None = index.data(ENTRY_ROLE)
        if entry is None:
            return
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        opt = QStyleOptionViewItem(option)
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        rect: QRect = option.rect
        cover = QRect(rect.left() + 8, rect.top() + (rect.height() - COVER) // 2, COVER, COVER)
        px = round(COVER * painter.device().devicePixelRatioF())
        pixmap = self._images.get(entry.track.thumbnail, px)
        if pixmap is None:
            painter.fillRect(cover, QColor("#2a2a2a"))
        else:
            side = min(pixmap.width(), pixmap.height())
            source = QRect((pixmap.width() - side) // 2, (pixmap.height() - side) // 2, side, side)
            painter.drawPixmap(cover, pixmap, source)

        row, cursor = index.row(), self._model.cursor
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        normal = option.palette.color(
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        title_color = QColor(ACCENT) if row == cursor else PLAYED if row < cursor else normal
        sub_color = PLAYED if row < cursor else QColor("#9a9a9a")

        metrics = option.fontMetrics
        duration = format_duration(entry.track.duration) if entry.track.duration else ""
        time_width = metrics.horizontalAdvance(duration) + 12 if duration else 0
        text = QRect(cover.right() + 10, rect.top() + 6, 0, rect.height() - 12)
        text.setRight(rect.right() - 8 - time_width)
        half = text.height() // 2
        top, bottom = QRect(text), QRect(text)
        top.setHeight(half)
        bottom.setTop(text.top() + half)

        painter.save()
        align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        elide = Qt.TextElideMode.ElideRight
        font = QFont(option.font)
        font.setBold(row == cursor)
        painter.setFont(font)
        painter.setPen(title_color)
        title = QFontMetrics(font).elidedText(entry.track.title, elide, top.width())
        painter.drawText(top, align, title)
        painter.setFont(option.font)
        painter.setPen(sub_color)
        painter.drawText(
            bottom, align, metrics.elidedText(entry.track.artist_text, elide, bottom.width())
        )
        if duration:
            right = QRect(rect.right() - 8 - time_width, rect.top(), time_width, rect.height())
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
        title.setObjectName("TrackTitle")
        self.summary = QLabel("")
        self.summary.setObjectName("Dim")
        clear = QToolButton()
        clear.setText("Clear")
        clear.setToolTip("Remove everything except the current song")
        clear.clicked.connect(controller.clear_queue)
        self.autoplay = QCheckBox("Autoplay")
        self.autoplay.setToolTip("When the queue runs out, keep playing similar songs")
        self.autoplay.toggled.connect(controller.set_radio)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.autoplay)
        top.addWidget(clear)
        header = QVBoxLayout()
        header.setContentsMargins(12, 8, 8, 6)
        header.setSpacing(0)
        header.addLayout(top)
        header.addWidget(self.summary)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.view, 1)

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
