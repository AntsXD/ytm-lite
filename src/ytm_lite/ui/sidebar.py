"""Left sidebar: Liked Songs, playlists, albums, artists, and the account button."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QMenu,
    QSizePolicy,
    QStyle,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.models import LIKED_SONGS_ID, PlaylistSummary

Route = tuple[str, ...]
ROUTE_ROLE = Qt.ItemDataRole.UserRole


def _route_of(item: QTreeWidgetItem | None) -> Route | None:
    # Qt hands stored tuples back as lists.
    value = item.data(0, ROUTE_ROLE) if item is not None else None
    return tuple(value) if value else None


class Sidebar(QWidget):
    navigate = Signal(object)  # Route
    sign_in_requested = Signal()
    sign_out_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(180)
        style = self.style()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.tree.setExpandsOnDoubleClick(False)

        def item(text: str, route: Route | None, icon: QStyle.StandardPixmap) -> QTreeWidgetItem:
            entry = QTreeWidgetItem([text])
            entry.setData(0, ROUTE_ROLE, route)
            entry.setIcon(0, style.standardIcon(icon))
            self.tree.addTopLevelItem(entry)
            return entry

        self._liked = item("Liked Songs", ("liked",), QStyle.StandardPixmap.SP_DialogYesButton)
        self._playlists = item("Playlists", None, QStyle.StandardPixmap.SP_DirIcon)
        self._albums = item("Albums", ("albums",), QStyle.StandardPixmap.SP_DriveCDIcon)
        self._artists = item("Artists", ("artists",), QStyle.StandardPixmap.SP_DirHomeIcon)
        self._playlists.setExpanded(True)
        self.tree.currentItemChanged.connect(self._on_current)
        self.tree.itemClicked.connect(self._on_clicked)

        self.account = QToolButton()
        self.account.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.account.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.account.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        self._account_label = self._menu.addAction("")
        self._account_label.setEnabled(False)
        self._menu.addSeparator()
        self._menu.addAction("Refresh library").triggered.connect(self.refresh_requested)
        self._menu.addAction("Sign out").triggered.connect(self.sign_out_requested)
        self._signed_in = False
        self.account.clicked.connect(self._account_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.account)
        self.set_account(None)

    def set_account(self, name: str | None) -> None:
        self._signed_in = name is not None
        for entry in (self._liked, self._playlists, self._albums, self._artists):
            entry.setHidden(not self._signed_in)
        if self._signed_in:
            self.account.setText(f"{name}  ▾")
            self._account_label.setText(f"Signed in as {name}")
            self.account.setMenu(self._menu)
        else:
            self.account.setText("Sign in…")
            self.account.setMenu(None)  # type: ignore[arg-type]

    def _account_clicked(self) -> None:
        if not self._signed_in:  # signed in, the button opens its menu instead
            self.sign_in_requested.emit()

    def set_playlists(self, playlists: Sequence[PlaylistSummary]) -> None:
        current_route = _route_of(self.tree.currentItem())
        self.tree.blockSignals(True)
        self._playlists.takeChildren()
        for playlist in playlists:
            if playlist.id in (LIKED_SONGS_ID, "SE"):  # Liked Songs has its own entry
                continue
            child = QTreeWidgetItem([playlist.title])
            child.setData(0, ROUTE_ROLE, ("playlist", playlist.id, playlist.title))
            child.setToolTip(0, playlist.title)
            self._playlists.addChild(child)
            if current_route == ("playlist", playlist.id, playlist.title):
                self.tree.setCurrentItem(child)
        self.tree.blockSignals(False)

    def select(self, route: Route) -> None:
        """Highlight the entry for `route` without navigating."""
        self.tree.blockSignals(True)
        try:
            for entry in self._iter_items():
                if _route_of(entry) == route:
                    self.tree.setCurrentItem(entry)
                    return
            self.tree.setCurrentIndex(QModelIndex())
        finally:
            self.tree.blockSignals(False)

    def _iter_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top is None:
                continue
            items.append(top)
            for j in range(top.childCount()):
                child = top.child(j)
                if child is not None:
                    items.append(child)
        return items

    def _on_current(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        route = _route_of(current)
        if route is not None:
            self.navigate.emit(route)

    def _on_clicked(self, entry: QTreeWidgetItem, _column: int) -> None:
        if entry is self._playlists:
            entry.setExpanded(not entry.isExpanded())
