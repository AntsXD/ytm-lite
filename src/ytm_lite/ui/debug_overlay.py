"""Hidden debug overlay (Ctrl+Shift+D): memory, threads, caches, current stream."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase, QHideEvent, QShowEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ytm_lite.app.stats import tree_stats


def _mb(value: float | None) -> str:
    return "–" if value is None else f"{value / 1e6:.1f} MB"


class DebugOverlay(QFrame):
    """Refreshes once a second, and only while visible."""

    def __init__(self, info: Callable[[], dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DebugOverlay")
        self._info = info
        self._label = QLabel()
        self._label.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self._label)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self.hide()

    def showEvent(self, event: QShowEvent) -> None:
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        stats = tree_stats()
        lines = [
            f"process tree   RSS {_mb(stats.rss)}   private {_mb(stats.private)}   "
            f"threads {stats.threads}",
        ]
        for proc in stats.processes:
            lines.append(
                f"  {proc.pid:>6} {proc.name[:18]:<18} RSS {_mb(proc.rss):>9}  "
                f"private {_mb(proc.private):>9}  threads {proc.threads}"
            )
        for key, value in self._info().items():
            lines.append(f"{key:<14} {value}")
        self._label.setText("\n".join(lines))
        self.adjustSize()
