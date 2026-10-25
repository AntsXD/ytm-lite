"""Library albums or artists: a simple list with small covers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.models import AlbumSummary, ArtistSummary
from ytm_lite.ui.theme import HOVER, SELECTED, TEXT, TEXT_DIM, font
from ytm_lite.ui.widgets import draw_cover

ROW_HEIGHT = 68
COVER = 52
ITEM_ROLE = Qt.ItemDataRole.UserRole

Item = AlbumSummary | ArtistSummary
AnyIndex = QModelIndex | QPersistentModelIndex


def _texts(item: Item) -> tuple[str, str]:
    if isinstance(item, AlbumSummary):
        sub = " • ".join(p for p in (item.kind, item.artist_text, item.year or "") if p)
        return item.title, sub
    return item.name, f"{item.subscribers} subscribers" if item.subscribers else "Artist"


class CollectionModel(QAbstractListModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[Item] = []

    def set_items(self, items: Sequence[Item]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        item = self._items[index.row()]
        if role == ITEM_ROLE:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return _texts(item)[0]
        return None


class CollectionDelegate(QStyledItemDelegate):
    def __init__(
        self, images: ImageLoader, round_covers: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._images = images
        self._round = round_covers
        self._title_font = font(weight=QFont.Weight.Medium)

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        return QSize(200, ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        item: Item = index.data(ITEM_ROLE)
        rect: QRect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SELECTED if selected else HOVER))
            painter.drawRoundedRect(QRectF(rect.adjusted(0, 2, 0, -2)), 6, 6)
        cover = QRect(rect.left() + 10, rect.top() + (rect.height() - COVER) // 2, COVER, COVER)
        pixmap = self._images.get(
            item.thumbnail, round(COVER * painter.device().devicePixelRatioF())
        )
        draw_cover(painter, cover, pixmap, 4, round_=self._round)
        title, subtitle = _texts(item)
        left = cover.right() + 14
        width = rect.right() - left - 8
        half = rect.height() // 2
        align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.setFont(self._title_font)
        painter.setPen(QColor(TEXT))
        title_text = painter.fontMetrics().elidedText(  # see track_table._text
            title, Qt.TextElideMode.ElideRight, width
        )
        painter.drawText(QRect(left, rect.top() + 14, width, half - 14), align, title_text)
        painter.setFont(option.font)
        painter.setPen(QColor(TEXT_DIM))
        sub_text = painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRect(left, rect.top() + half, width, half - 14), align, sub_text)
        painter.restore()


class CollectionPage(QWidget):
    activated = Signal(object)  # AlbumSummary | ArtistSummary

    def __init__(
        self, title: str, images: ImageLoader, round_covers: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        heading = QLabel(title)
        heading.setFont(font(26, QFont.Weight.Bold))
        self.status = QLabel("Loading…")
        self.status.setObjectName("Dim")
        self.model = CollectionModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(CollectionDelegate(images, round_covers, self.view))
        self.view.setUniformItemSizes(True)
        self.view.setFrameShape(QListView.Shape.NoFrame)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setMouseTracking(True)
        self.view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.view.activated.connect(self._activated)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 12, 0)
        layout.setSpacing(6)
        layout.addWidget(heading)
        layout.addWidget(self.status)
        layout.addSpacing(8)
        layout.addWidget(self.view, 1)

        # Bound method, not a lambda: Qt drops the connection when the page is deleted.
        self._repaint = QTimer(self)
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(40)
        self._repaint.timeout.connect(self.view.viewport().update)
        images.loaded.connect(self._image_loaded)

    def _image_loaded(self, _key: str) -> None:
        self._repaint.start()

    def _activated(self, index: QModelIndex) -> None:
        self.activated.emit(index.data(ITEM_ROLE))

    def set_items(self, items: Sequence[Item] | None) -> None:
        if items is None:
            self.status.setText("Loading…")
            return
        self.status.setText("" if items else "Nothing here yet")
        self.status.setVisible(not items)
        self.model.set_items(items)
