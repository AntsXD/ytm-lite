"""Library albums or artists: a simple list with small covers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
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

ROW_HEIGHT = 64
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

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        return QSize(200, ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        item: Item = index.data(ITEM_ROLE)
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        opt = QStyleOptionViewItem(option)
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        rect: QRect = option.rect
        cover = QRect(rect.left() + 12, rect.top() + (rect.height() - COVER) // 2, COVER, COVER)
        pixmap = self._images.get(
            item.thumbnail, round(COVER * painter.device().devicePixelRatioF())
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if pixmap is None:
            painter.setBrush(QColor("#2a2a2a"))
            painter.setPen(Qt.PenStyle.NoPen)
            if self._round:
                painter.drawEllipse(cover)
            else:
                painter.drawRect(cover)
        else:
            side = min(pixmap.width(), pixmap.height())
            source = QRect((pixmap.width() - side) // 2, (pixmap.height() - side) // 2, side, side)
            painter.drawPixmap(cover, pixmap, source)
        title, subtitle = _texts(item)
        text_left = cover.right() + 14
        width = rect.right() - text_left - 8
        metrics = option.fontMetrics
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(option.palette.text().color())
        title_rect = QRect(text_left, rect.top() + 12, width, metrics.height())
        painter.drawText(
            title_rect, 0, metrics.elidedText(title, Qt.TextElideMode.ElideRight, width)
        )
        painter.setFont(option.font)
        painter.setPen(QColor("#9a9a9a"))
        sub_rect = QRect(text_left, title_rect.bottom() + 4, width, metrics.height())
        painter.drawText(
            sub_rect, 0, metrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, width)
        )
        painter.restore()


class CollectionPage(QWidget):
    activated = Signal(object)  # AlbumSummary | ArtistSummary

    def __init__(
        self, title: str, images: ImageLoader, round_covers: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        heading = QLabel(title)
        font = QFont(heading.font())
        font.setPointSizeF(font.pointSizeF() * 1.8)
        font.setBold(True)
        heading.setFont(font)
        self.status = QLabel("Loading…")
        self.status.setObjectName("Dim")
        self.model = CollectionModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(CollectionDelegate(images, round_covers, self.view))
        self.view.setUniformItemSizes(True)
        self.view.setFrameShape(QListView.Shape.NoFrame)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.view.activated.connect(lambda index: self.activated.emit(index.data(ITEM_ROLE)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 0)
        layout.addWidget(heading)
        layout.addWidget(self.status)
        layout.addWidget(self.view, 1)

        # Bound method, not a lambda: Qt drops the connection when the page is deleted.
        self._repaint = QTimer(self)
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(40)
        self._repaint.timeout.connect(self.view.viewport().update)
        images.loaded.connect(self._image_loaded)

    def _image_loaded(self, _key: str) -> None:
        self._repaint.start()

    def set_items(self, items: Sequence[Item] | None) -> None:
        if items is None:
            self.status.setText("Loading…")
            return
        self.status.setText("" if items else "Nothing here yet")
        self.status.setVisible(not items)
        self.model.set_items(items)
