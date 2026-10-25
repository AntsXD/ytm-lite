"""The current song's lyrics, beside the page (where the queue goes; the bar's buttons
switch between them).

Synced lyrics highlight the line being sung and keep it in the middle; clicking a line
jumps there. Scrolling yourself pauses the following for a few seconds. Lyrics are
fetched only while the panel is open, and the last few are kept.
"""

from __future__ import annotations

import bisect
import time
from collections import OrderedDict
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListView,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.api import Api
from ytm_lite.app.models import LyricLine, Lyrics, Track
from ytm_lite.app.workers import Channel, Workers
from ytm_lite.ui.theme import TEXT, TEXT_DIM, TEXT_FAINT, font
from ytm_lite.ui.widgets import ElidedLabel

AnyIndex = QModelIndex | QPersistentModelIndex
KEPT = 12
FOLLOW_PAUSE = 4.0  # seconds after you scroll yourself


class LyricsModel(QAbstractListModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lines: tuple[LyricLine, ...] = ()
        self.synced = False
        self.current = -1

    def set_lyrics(self, lines: tuple[LyricLine, ...], synced: bool) -> None:
        self.beginResetModel()
        self.lines, self.synced, self.current = lines, synced, -1
        self.endResetModel()

    def set_current(self, row: int) -> None:
        if row == self.current:
            return
        changed = [r for r in (self.current, row) if 0 <= r < len(self.lines)]
        self.current = row
        for r in changed:
            index = self.index(r)
            self.dataChanged.emit(index, index)

    def rowCount(self, parent: AnyIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.lines)

    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and 0 <= index.row() < len(self.lines):
            return self.lines[index.row()].text
        return None


class LyricsDelegate(QStyledItemDelegate):
    PAD_H, PAD_V = 16, 5

    def __init__(self, view: LyricsView) -> None:
        super().__init__(view)
        self._view = view
        self.font = font(13, QFont.Weight.DemiBold)
        self._metrics = QFontMetrics(self.font)

    def sizeHint(self, option: QStyleOptionViewItem, index: AnyIndex) -> QSize:
        width = self._view.viewport().width()
        bounds = QRect(0, 0, max(40, width - 2 * self.PAD_H), 100_000)
        text = str(index.data() or "") or " "
        height = self._metrics.boundingRect(bounds, int(Qt.TextFlag.TextWordWrap), text).height()
        return QSize(width, height + 2 * self.PAD_V)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: AnyIndex) -> None:
        model = self._view.lyrics_model
        row = index.row()
        if not model.synced or row == model.current:
            color = TEXT
        elif row < model.current:
            color = TEXT_FAINT
        else:
            color = TEXT_DIM
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver) and model.synced
        if hovered and row != model.current:
            color = "#d0d0d0"
        rect: QRect = option.rect
        painter.save()
        painter.setFont(self.font)
        painter.setPen(QColor(color))
        text_rect = rect.adjusted(self.PAD_H, self.PAD_V, -self.PAD_H, -self.PAD_V)
        painter.drawText(text_rect, int(Qt.TextFlag.TextWordWrap), str(index.data() or ""))
        painter.restore()


class LyricsView(QListView):
    seek_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lyrics_model = LyricsModel(self)
        self.setModel(self.lyrics_model)
        self.setItemDelegate(LyricsDelegate(self))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setFrameShape(QListView.Shape.NoFrame)
        self._scrolled_at = 0.0
        self.verticalScrollBar().actionTriggered.connect(self._user_scrolled)
        self.clicked.connect(self._clicked)

    def _user_scrolled(self, _action: int) -> None:
        self._scrolled_at = time.monotonic()

    def wheelEvent(self, event: Any) -> None:
        self._scrolled_at = time.monotonic()
        super().wheelEvent(event)

    def follow(self) -> None:
        model = self.lyrics_model
        if model.current < 0 or time.monotonic() - self._scrolled_at < FOLLOW_PAUSE:
            return
        self.scrollTo(model.index(model.current), QAbstractItemView.ScrollHint.PositionAtCenter)

    def _clicked(self, index: QModelIndex) -> None:
        model = self.lyrics_model
        if model.synced and 0 <= index.row() < len(model.lines):
            self._scrolled_at = 0.0  # follow again from the line you picked
            self.seek_requested.emit(model.lines[index.row()].start_ms / 1000)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        on_line = self.indexAt(event.position().toPoint()).isValid()
        clickable = on_line and self.lyrics_model.synced
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )


class LyricsPanel(QWidget):
    seek_requested = Signal(float)

    def __init__(self, workers: Workers, api: Api, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("QueuePanel")  # the same panel styling as the queue
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._api = api
        self._channel = Channel(workers)
        self.destroyed.connect(self._channel.cancel)
        self._cache: OrderedDict[str, Lyrics | None] = OrderedDict()
        self._track: Track | None = None
        self._shown_for: str | None = None  # video id whose lyrics are on screen (or asked)
        self._starts: list[int] = []

        title = QLabel("Lyrics")
        title.setFont(font(13, QFont.Weight.Bold))
        self.song = ElidedLabel("")
        self.song.setObjectName("Dim")
        self.song.setFont(font(9))
        self.message = QLabel("")
        self.message.setObjectName("Dim")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.view = LyricsView()
        self.view.seek_requested.connect(self.seek_requested)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.message)
        self.stack.addWidget(self.view)
        self.source = QLabel("")
        self.source.setObjectName("Dim")
        self.source.setFont(font(8.5))

        header = QVBoxLayout()
        header.setContentsMargins(16, 14, 10, 8)
        header.setSpacing(2)
        header.addWidget(title)
        header.addWidget(self.song)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.stack, 1)
        footer = QVBoxLayout()
        footer.setContentsMargins(16, 6, 10, 10)
        footer.addWidget(self.source)
        layout.addLayout(footer)
        self._show_message("Nothing playing.")

    # -- what to show ----------------------------------------------------------------------

    def set_track(self, track: Track | None) -> None:
        self._track = track
        if self.isVisible():
            self._load()

    def set_position(self, seconds: float | None) -> None:
        model = self.view.lyrics_model
        if not model.synced or seconds is None or not self.isVisible():
            return
        row = bisect.bisect_right(self._starts, int(seconds * 1000)) - 1
        if row != model.current:
            model.set_current(row)
            self.view.follow()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._load()

    # -- loading ------------------------------------------------------------------------------

    def _load(self) -> None:
        track = self._track
        if track is None:
            self._shown_for = None
            self.song.setText("")
            self._show_message("Nothing playing.")
            return
        if track.video_id == self._shown_for:
            return
        self._shown_for = track.video_id
        self.song.setText(
            f"{track.title} — {track.artist_text}" if track.artist_text else track.title
        )
        if track.video_id in self._cache:
            self._show(self._cache[track.video_id])
            return
        self._show_message("Loading lyrics…")
        video_id, api = track.video_id, self._api

        def fetch() -> tuple[str, Lyrics | None]:
            return video_id, api.lyrics(video_id)

        self._channel.submit(fetch, on_done=self._got, on_error=self._failed)

    def _got(self, result: tuple[str, Lyrics | None]) -> None:
        video_id, lyrics = result
        self._cache[video_id] = lyrics
        self._cache.move_to_end(video_id)
        while len(self._cache) > KEPT:
            self._cache.popitem(last=False)
        if video_id == self._shown_for:
            self._show(lyrics)

    def _failed(self, error: Exception) -> None:
        self._shown_for = None  # try again next time
        self._show_message("Couldn't load the lyrics. Check your connection.")

    def _show(self, lyrics: Lyrics | None) -> None:
        if lyrics is None:
            self._show_message("No lyrics for this song.")
            return
        if lyrics.synced:
            lines = lyrics.lines
        else:
            lines = tuple(LyricLine(-1, -1, text) for text in lyrics.text.splitlines())
        self._starts = [line.start_ms for line in lines] if lyrics.synced else []
        self.view.lyrics_model.set_lyrics(lines, lyrics.synced)
        self.view.scrollToTop()
        self.source.setText(lyrics.source or "")
        self.stack.setCurrentWidget(self.view)

    def _show_message(self, text: str) -> None:
        self.message.setText(text)
        self.source.setText("")
        self.stack.setCurrentWidget(self.message)
