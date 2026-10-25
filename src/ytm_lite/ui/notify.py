"""Small non-blocking notifications: a toast in the bottom-right corner."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class Toast(QLabel):
    """Shows a message over the bottom-right of its parent for a few seconds."""

    MARGIN = 16

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setWordWrap(True)
        self.setMaximumWidth(420)
        self.setStyleSheet(
            "#Toast { background: #2b2b2b; color: #f0f0f0; border: 1px solid #444;"
            " border-radius: 6px; padding: 8px 12px; }"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, msecs: int = 4000) -> None:
        self.setText(text)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(msecs)

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move(
                parent.width() - self.width() - self.MARGIN,
                parent.height() - self.height() - self.MARGIN,
            )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
