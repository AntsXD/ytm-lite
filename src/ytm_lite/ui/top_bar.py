"""The content panel's top: back/forward and the search box, over a tint from the page.

Suggestions appear after a short pause in typing; the full search runs on Enter or when
a suggestion is picked, so nothing heavy runs mid-word.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFocusEvent, QLinearGradient, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.api import Api
from ytm_lite.app.workers import Channel, Workers
from ytm_lite.ui import icons
from ytm_lite.ui.theme import PANEL, RADIUS

TINT_HEIGHT = 340  # px over which a page's tint fades into the panel


class SearchBox(QLineEdit):
    search_requested = Signal(str)

    SUGGEST_AFTER_MS = 250
    MIN_CHARS = 2

    def __init__(self, workers: Workers, api: Api, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._channel = Channel(workers)
        self._last_submitted = ""
        self.setObjectName("Search")
        self.setPlaceholderText("What do you want to play?")
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(240)
        self.setMaximumWidth(440)
        self.addAction(
            icons.icon("search", 18, icons.DIM), QLineEdit.ActionPosition.LeadingPosition
        )

        self._suggestions = QStringListModel(self)
        self._completer = QCompleter(self._suggestions, self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._popup = QListView()
        self._popup.setObjectName("Suggestions")
        self._completer.setPopup(self._popup)
        self._completer.activated.connect(self._picked)
        self.setCompleter(self._completer)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.SUGGEST_AFTER_MS)
        self._timer.timeout.connect(self._suggest)
        self.textEdited.connect(self._edited)
        self.returnPressed.connect(self._submit)

    def focus_and_select(self) -> None:
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.selectAll()

    def _edited(self, _text: str) -> None:
        self._last_submitted = ""
        self._timer.start()

    def _suggest(self) -> None:
        query = self.text().strip()
        if len(query) < self.MIN_CHARS:
            self._channel.cancel()
            self._popup.hide()
            return
        api = self._api
        self._channel.submit(lambda: api.search_suggestions(query), on_done=self._show)

    def _show(self, suggestions: list[str]) -> None:
        if not self.hasFocus() or not suggestions:
            return
        self._suggestions.setStringList(suggestions)
        self._completer.complete()

    def _picked(self, text: str) -> None:
        self.setText(text)
        self._submit()

    def _submit(self) -> None:
        query = self.text().strip()
        self._timer.stop()
        self._channel.cancel()
        self._popup.hide()
        if query and query != self._last_submitted:  # Enter can also activate the popup
            self._last_submitted = query
            self.search_requested.emit(query)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self._timer.stop()


class TopBar(QWidget):
    back_clicked = Signal()
    forward_clicked = Signal()

    def __init__(self, search: SearchBox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.search = search

        def nav_button(name: str, tip: str) -> QToolButton:
            button = QToolButton()
            button.setIcon(icons.icon(name, 24, icons.NORMAL))
            button.setIconSize(QSize(24, 24))
            button.setToolTip(tip)
            button.setObjectName("Round")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            return button

        self.back = nav_button("back", "Back (Alt+←)")
        self.forward = nav_button("forward", "Forward (Alt+→)")
        self.back.clicked.connect(self.back_clicked)
        self.forward.clicked.connect(self.forward_clicked)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 4)
        row.setSpacing(8)
        row.addWidget(self.back)
        row.addWidget(self.forward)
        row.addSpacing(8)
        row.addWidget(search, 1)
        row.addStretch(1)

    def set_history(self, can_go_back: bool, can_go_forward: bool) -> None:
        self.back.setEnabled(can_go_back)
        self.forward.setEnabled(can_go_forward)


class ContentPanel(QFrame):
    """The middle panel. It paints itself: rounded, with the current page's tint (its
    cover's color) fading out from the top, behind the top bar and the page header."""

    def __init__(self, top_bar: TopBar, pages: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContentPanel")
        self._tint: QColor | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(top_bar)
        layout.addWidget(pages, 1)

    def set_tint(self, color: QColor | None) -> None:
        if color != self._tint:
            self._tint = color
            self.update(0, 0, self.width(), TINT_HEIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)
        painter.fillPath(shape, QColor(PANEL))
        if self._tint is not None:
            gradient = QLinearGradient(0, 0, 0, TINT_HEIGHT)
            gradient.setColorAt(0.0, self._tint)
            gradient.setColorAt(1.0, QColor(PANEL))
            top = QPainterPath()
            top.addRect(QRectF(0, 0, self.width(), TINT_HEIGHT))
            painter.fillPath(shape.intersected(top), gradient)
