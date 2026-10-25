"""Left sidebar: Liked Songs, albums, artists, your playlists (with covers), and the
account button."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.models import LIKED_SONGS_ID, PlaylistSummary
from ytm_lite.ui import icons
from ytm_lite.ui.theme import ACCENT_FILL, HOVER, SELECTED, TEXT, TEXT_DIM, font
from ytm_lite.ui.widgets import draw_cover

Route = tuple[str, ...]
ITEM_ROLE = Qt.ItemDataRole.UserRole
COVER = 36

AnyIndex = QModelIndex | QPersistentModelIndex


@dataclass(frozen=True, slots=True)
class SidebarItem:
    kind: str  # "nav", "header", "playlist" or "hint"
    text: str
    route: Route | None = None
    icon: str | None = None
    subtitle: str = ""
    thumbnail: str | None = None

    @property
    def selectable(self) -> bool:
        return self.route is not None


_HEIGHTS = {"nav": 40, "header": 40, "playlist": 52, "hint": 64}
_NAV = (
    SidebarItem("nav", "Liked music", ("liked",), "heart"),
    SidebarItem("nav", "Albums", ("albums",), "album"),
    SidebarItem("nav", "Artists", ("artists",), "person"),
)


class SidebarModel(QAbstractListModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.items: list[SidebarItem] = []

    def set_items(self, items: Sequence[SidebarItem]) -> None:
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def row_of(self, route: Route) -> int:
        for row, item in enumerate(self.items):
            if item.route == route:
                return row
        return -1

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        item = self.items[index.row()]
        if role == ITEM_ROLE:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return item.text
        if role == Qt.ItemDataRole.ToolTipRole and item.kind == "playlist":
            return item.text
        return None

    def flags(self, index: AnyIndex) -> Qt.ItemFlag:
        if index.isValid() and self.items[index.row()].selectable:
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return Qt.ItemFlag.NoItemFlags


class SidebarDelegate(QStyledItemDelegate):
    def __init__(self, images: ImageLoader, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._images = images
        self._bold = font(weight=QFont.Weight.DemiBold)
        self._small = font(9)

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        item: SidebarItem = index.data(ITEM_ROLE)
        return QSize(option.rect.width(), _HEIGHTS[item.kind])

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        item: SidebarItem = index.data(ITEM_ROLE)
        rect: QRect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver) and item.selectable
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SELECTED if selected else HOVER))
            painter.drawRoundedRect(QRectF(rect.adjusted(4, 2, -4, -2)), 6, 6)
        bright = QColor(TEXT if selected or hovered else TEXT_DIM)
        align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if item.kind == "nav":
            icon_rect = QRectF(rect.left() + 16, rect.center().y() - 11, 22, 22)
            icons.paint(painter, item.icon or "queue", icon_rect, bright)
            painter.setFont(self._bold)
            painter.setPen(bright)
            painter.drawText(rect.adjusted(52, 0, -8, 0), align, item.text)
        elif item.kind == "header":
            painter.setFont(self._bold)
            painter.setPen(QColor(TEXT))
            painter.drawText(rect.adjusted(16, 8, -8, 0), align, item.text)
        elif item.kind == "hint":
            painter.setPen(QColor(TEXT_DIM))
            flags = int(Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap)
            painter.drawText(rect.adjusted(16, 0, -12, 0), flags, item.text)
        else:
            cover = QRect(rect.left() + 12, rect.top() + (rect.height() - COVER) // 2, COVER, COVER)
            px = round(COVER * painter.device().devicePixelRatioF())
            draw_cover(painter, cover, self._images.get(item.thumbnail, px))
            left = cover.right() + 12
            width = rect.right() - left - 8
            top = QRect(left, rect.top() + 8, width, rect.height() // 2 - 8)
            bottom = QRect(left, rect.top() + rect.height() // 2, width, rect.height() // 2 - 8)
            painter.setPen(QColor(TEXT))
            painter.setFont(option.font)  # measure with the painter: see track_table._text
            title = painter.fontMetrics().elidedText(item.text, Qt.TextElideMode.ElideRight, width)
            painter.drawText(top, align, title)
            painter.setFont(self._small)
            painter.setPen(QColor(TEXT_DIM))
            sub = painter.fontMetrics().elidedText(
                item.subtitle, Qt.TextElideMode.ElideRight, width
            )
            painter.drawText(bottom, align, sub)
        painter.restore()


def avatar_icon(name: str, side: int = 28) -> QIcon:
    """A violet circle with the name's first letter."""
    ratio = 2.0
    pixmap = QPixmap(round(side * ratio), round(side * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ACCENT_FILL))
    painter.drawEllipse(pixmap.rect())
    letter = font(weight=QFont.Weight.Bold)
    letter.setPixelSize(round(side * ratio * 0.5))
    painter.setFont(letter)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter), (name[:1] or "?").upper())
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


class Sidebar(QWidget):
    navigate = Signal(object)  # Route
    sign_in_requested = Signal()
    sign_out_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, images: ImageLoader, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(200)
        self._playlists: list[SidebarItem] = []
        self._signed_in = False
        self.model = SidebarModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(SidebarDelegate(images, self.view))
        self.view.setFrameShape(QListView.Shape.NoFrame)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setMouseTracking(True)
        self.view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.view.selectionModel().currentChanged.connect(self._on_current)

        self._repaint = QTimer(self)  # once per burst of finished cover downloads
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(40)
        self._repaint.timeout.connect(self.view.viewport().update)
        images.loaded.connect(self._image_loaded)

        self.account = QPushButton()
        self.account.setObjectName("Account")
        self.account.setIconSize(QSize(28, 28))
        self.account.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._menu = QMenu(self)
        self._account_label = self._menu.addAction("")
        self._account_label.setEnabled(False)
        self._menu.addSeparator()
        self._menu.addAction("Refresh library").triggered.connect(self.refresh_requested)
        self._menu.addAction("Sign out").triggered.connect(self.sign_out_requested)
        self.account.setMenu(self._menu)
        self.sign_in = QPushButton("Sign in")
        self.sign_in.setObjectName("Primary")
        self.sign_in.clicked.connect(self.sign_in_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(6)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.account)
        layout.addWidget(self.sign_in)
        self.set_account(None)

    def _image_loaded(self, _key: str) -> None:
        if self.isVisible():
            self._repaint.start()

    def _rebuild(self) -> None:
        current = self._current_route()
        if self._signed_in:
            items = [*_NAV, SidebarItem("header", "Playlists"), *self._playlists]
        else:
            items = [SidebarItem("hint", "Sign in to see your playlists, albums and artists here.")]
        self.view.selectionModel().blockSignals(True)
        self.model.set_items(items)
        if current is not None:
            self._select_row(self.model.row_of(current))
        self.view.selectionModel().blockSignals(False)

    def set_account(self, name: str | None) -> None:
        self._signed_in = name is not None
        self.account.setVisible(self._signed_in)
        self.sign_in.setVisible(not self._signed_in)
        if name is not None:
            self.account.setText(f"  {name}")
            self.account.setIcon(avatar_icon(name))
            self._account_label.setText(f"Signed in as {name}")
        self._rebuild()

    def set_playlists(self, playlists: Sequence[PlaylistSummary]) -> None:
        self._playlists = [
            SidebarItem(
                "playlist",
                p.title,
                ("playlist", p.id, p.title),
                subtitle=" · ".join(x for x in ("Playlist", p.author or "") if x),
                thumbnail=p.thumbnail,
            )
            for p in playlists
            if p.id not in (LIKED_SONGS_ID, "SE")  # Liked Songs has its own entry
        ]
        self._rebuild()

    def select(self, route: Route) -> None:
        """Highlight the entry for `route` without navigating."""
        selection = self.view.selectionModel()
        selection.blockSignals(True)
        try:
            self._select_row(self.model.row_of(route))
        finally:
            selection.blockSignals(False)
        self.view.viewport().update()

    def _select_row(self, row: int) -> None:
        if row < 0:
            self.view.clearSelection()
            self.view.setCurrentIndex(QModelIndex())
        else:
            self.view.setCurrentIndex(self.model.index(row))

    def _current_route(self) -> Route | None:
        index = self.view.currentIndex()
        item: SidebarItem | None = index.data(ITEM_ROLE) if index.isValid() else None
        return item.route if item is not None else None

    def _on_current(self, current: QModelIndex, _previous: QModelIndex) -> None:
        item: SidebarItem | None = current.data(ITEM_ROLE) if current.isValid() else None
        if item is not None and item.route is not None:
            self.navigate.emit(item.route)
