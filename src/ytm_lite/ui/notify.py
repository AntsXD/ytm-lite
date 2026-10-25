"""Small non-blocking notifications: a toast in the bottom-right corner, and a banner
across the top for problems that need an action (like signing in again)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QWidget


class Banner(QFrame):
    action_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._text = QLabel()
        self._text.setWordWrap(True)
        self._action = QPushButton()
        self._action.setObjectName("Primary")
        self._action.clicked.connect(self.action_clicked)
        close = QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setToolTip("Dismiss")
        close.clicked.connect(self.hide)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 10, 8)
        row.addWidget(self._text, 1)
        row.addWidget(self._action)
        row.addWidget(close)
        self.hide()

    def show_message(self, text: str, action: str | None = None) -> None:
        self._text.setText(text)
        self._action.setText(action or "")
        self._action.setVisible(bool(action))
        self.show()


class Toast(QLabel):
    """Shows a message over the bottom-right of its parent for a few seconds."""

    MARGIN = 16

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setWordWrap(True)
        self.setMaximumWidth(420)
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
