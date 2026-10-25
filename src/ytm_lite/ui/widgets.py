"""Small shared widgets and painting helpers for the panel look."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QEnterEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.ui import icons
from ytm_lite.ui.theme import ACCENT_FILL, PLACEHOLDER, TEXT, TEXT_DIM


class Panel(QFrame):
    """A rounded dark panel on the black canvas."""

    def __init__(self, child: QWidget, margins: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(margins, margins, margins, margins)
        layout.setSpacing(0)
        layout.addWidget(child)


class ElidedLabel(QLabel):
    """One line of text that ends in "…" when it doesn't fit. It asks for its full width
    but can shrink to nothing."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._elide()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        width = self.fontMetrics().horizontalAdvance(self._full) + 2
        return QSize(width, hint.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text: str) -> None:
        self._full = text
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, self.width()))


class ToggleSwitch(QAbstractButton):
    """A label followed by a small on/off switch."""

    TRACK = QSize(30, 16)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def sizeHint(self) -> QSize:
        text = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(text + 8 + self.TRACK.width(), max(20, self.fontMetrics().height()))

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        painter.setPen(QColor(TEXT if on else TEXT_DIM))
        text_rect = QRect(0, 0, self.width() - self.TRACK.width() - 8, self.height())
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter), self.text())
        track = QRectF(
            self.width() - self.TRACK.width(),
            (self.height() - self.TRACK.height()) / 2,
            self.TRACK.width(),
            self.TRACK.height(),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT_FILL if on else "#4a4a4a"))
        radius = track.height() / 2
        painter.drawRoundedRect(track, radius, radius)
        knob = track.height() - 4
        x = track.right() - knob - 2 if on else track.left() + 2
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(x, track.top() + 2, knob, knob))


class CircleButton(QAbstractButton):
    """A round button with an icon: the big Play buttons."""

    def __init__(
        self,
        icon_name: str,
        diameter: int,
        fill: str,
        glyph: str,
        hover_fill: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon = icon_name
        self._fill = QColor(fill)
        self._hover_fill = QColor(hover_fill or fill)
        self._glyph = QColor(glyph)
        self._hovered = False
        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_icon_name(self, name: str) -> None:
        if name != self._icon:
            self._icon = name
            self.update()

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:
        self._hovered = False
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        enabled = self.isEnabled()
        fill = self._hover_fill if self._hovered and enabled else self._fill
        if not enabled:
            fill = QColor(fill)
            fill.setAlphaF(0.4)
        painter.setBrush(fill)
        painter.drawEllipse(QRectF(self.rect()))
        side = self.width() * 0.5
        offset = (self.width() - side) / 2
        icons.paint(painter, self._icon, QRectF(offset, offset, side, side), self._glyph)


def draw_cover(
    painter: QPainter,
    rect: QRect | QRectF,
    pixmap: QPixmap | None,
    radius: float = 4.0,
    *,
    round_: bool = False,
) -> None:
    """A cover in `rect`: centre-cropped to a square, with rounded corners (or a circle),
    or a placeholder while it loads. Drawn as a textured shape, so the edges stay smooth."""
    target = QRectF(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setPen(Qt.PenStyle.NoPen)
    if pixmap is None or pixmap.isNull():
        painter.setBrush(QColor(PLACEHOLDER))
    else:
        side = min(pixmap.width(), pixmap.height())
        scale = target.width() / side
        transform = QTransform()
        transform.translate(
            target.x() - (pixmap.width() - side) / 2 * scale,
            target.y() - (pixmap.height() - side) / 2 * scale,
        )
        transform.scale(scale, scale)
        brush = QBrush(pixmap)
        brush.setTransform(transform)
        painter.setBrush(brush)
    if round_:
        painter.drawEllipse(target)
    else:
        painter.drawRoundedRect(target, radius, radius)
    painter.restore()


def tint_of(pixmap: QPixmap) -> QColor:
    """A dark, still colorful version of a cover's average color (for header tints)."""
    average = pixmap.toImage().scaled(
        1,
        1,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    color = average.pixelColor(0, 0)
    hue = max(color.hsvHueF(), 0.0)  # -1 for greys
    return QColor.fromHsvF(hue, min(color.hsvSaturationF(), 0.65), 0.34)
