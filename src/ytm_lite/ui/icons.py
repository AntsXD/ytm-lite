"""Toolbar icons drawn from SVG path data with QPainter.

Rendering the paths ourselves avoids loading QtSvg and its image plugin for a dozen
glyphs. The paths come from Google's Material Icons (Apache License 2.0), on a 24×24
grid. Only the path commands they use are supported: M L H V C S Z, absolute and
relative.
"""

from __future__ import annotations

import re
from functools import cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPixmap

from ytm_lite.ui.theme import ACCENT, TEXT, TEXT_DIM, TEXT_FAINT

NORMAL = TEXT
DIM = TEXT_DIM
DISABLED = TEXT_FAINT

PATHS = {
    "play": "M8 5v14l11-7z",
    "pause": "M6 19h4V5H6v14zm8-14v14h4V5h-4z",
    "next": "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z",
    "previous": "M6 6h2v12H6zm3.5 6l8.5 6V6z",
    "shuffle": (
        "M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 "
        "17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 "
        "2.04-3.13-3.13z"
    ),
    "repeat": "M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z",
    "repeat_one": (
        "M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4zm-4-2V9h-1l-2 1v1h1.5v4H13z"
    ),
    "queue": (
        "M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18C16.69 14.07 16.35 14 16 "
        "14c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3V8h3V6h-5z"
    ),
    "heart": (
        "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 "
        "4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 "
        "11.54L12 21.35z"
    ),
    "heart_outline": (
        "M16.5 3c-1.74 0-3.41.81-4.5 2.09C10.91 3.81 9.24 3 7.5 3 4.42 3 2 5.42 2 8.5c0 "
        "3.78 3.4 6.86 8.55 11.54L12 21.35l1.45-1.32C18.6 15.36 22 12.28 22 8.5 22 5.42 "
        "19.58 3 16.5 3zm-4.4 15.55l-.1.1-.1-.1C7.14 14.24 4 11.39 4 8.5 4 6.5 5.5 5 7.5 "
        "5c1.54 0 3.04.99 3.57 2.36h1.87C13.46 5.99 14.96 5 16.5 5c2 0 3.5 1.5 3.5 3.5 0 "
        "2.89-3.14 5.74-7.9 10.05z"
    ),
    "volume_off": (
        "M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 "
        ".94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c"
        "2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-"
        "2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 "
        "6.09 12 8.18V4z"
    ),
    "album": (
        "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 14.5c-2.49 0-4.5-"
        "2.01-4.5-4.5S9.51 7.5 12 7.5s4.5 2.01 4.5 4.5-2.01 4.5-4.5 4.5zm0-5.5c-.55 0-1 .45-1 "
        "1s.45 1 1 1 1-.45 1-1-.45-1-1-1z"
    ),
    "person": (
        "M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-"
        "2c0-2.66-5.33-4-8-4z"
    ),
    "volume": (
        "M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 "
        "2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 "
        "7-4.49 7-8.77s-2.99-7.86-7-8.77z"
    ),
}

_TOKEN = re.compile(r"[MLHVCSZmlhvcsz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Z": 0}


def parse_path(data: str) -> QPainterPath:
    """Build a QPainterPath from SVG path data (the subset described above)."""
    tokens = _TOKEN.findall(data)
    path = QPainterPath()
    x = y = 0.0
    start = QPointF()
    last_ctrl: QPointF | None = None  # second control point of the previous curve
    command = ""
    i = 0
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
        upper = command.upper()
        if upper not in _ARITY:
            raise ValueError(f"unsupported path command {command!r}")
        arity = _ARITY[upper]
        if upper == "Z":
            path.closeSubpath()
            x, y = start.x(), start.y()
            last_ctrl = None
            continue
        if i + arity > len(tokens):
            raise ValueError("truncated path data")
        args = [float(t) for t in tokens[i : i + arity]]
        i += arity
        dx, dy = (x, y) if command.islower() else (0.0, 0.0)
        if upper == "M":
            x, y = args[0] + dx, args[1] + dy
            path.moveTo(x, y)
            start = QPointF(x, y)
            command = "l" if command == "m" else "L"  # extra pairs are line-tos
            last_ctrl = None
        elif upper == "L":
            x, y = args[0] + dx, args[1] + dy
            path.lineTo(x, y)
            last_ctrl = None
        elif upper == "H":
            x = args[0] + (x if command == "h" else 0.0)
            path.lineTo(x, y)
            last_ctrl = None
        elif upper == "V":
            y = args[0] + (y if command == "v" else 0.0)
            path.lineTo(x, y)
            last_ctrl = None
        elif upper == "C":
            c1 = QPointF(args[0] + dx, args[1] + dy)
            c2 = QPointF(args[2] + dx, args[3] + dy)
            x, y = args[4] + dx, args[5] + dy
            path.cubicTo(c1, c2, QPointF(x, y))
            last_ctrl = c2
        else:  # S: the first control point mirrors the previous curve's second
            c1 = (
                QPointF(2 * x - last_ctrl.x(), 2 * y - last_ctrl.y())
                if last_ctrl
                else QPointF(x, y)
            )
            c2 = QPointF(args[0] + dx, args[1] + dy)
            x, y = args[2] + dx, args[3] + dy
            path.cubicTo(c1, c2, QPointF(x, y))
            last_ctrl = c2
    return path


@cache
def _path(name: str) -> QPainterPath:
    return parse_path(PATHS[name])


def paint(painter: QPainter, name: str, rect: QRectF, color: QColor | str) -> None:
    """Draw icon `name` scaled into the square `rect`."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 24, rect.height() / 24)
    painter.fillPath(_path(name), QColor(color))
    painter.restore()


def _pixmap(name: str, size: int, color: str, dpr: float) -> QPixmap:
    side = max(1, round(size * dpr))
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(side / 24, side / 24)
    painter.fillPath(_path(name), QColor(color))
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _ratios() -> list[float]:
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen is not None else 1.0
    return sorted({1.0, dpr})


def icon(
    name: str,
    size: int = 20,
    color: str = NORMAL,
    *,
    hover: str | None = None,
    checked: str | None = None,
    checked_name: str | None = None,
) -> QIcon:
    """An icon; `hover` brightens it under the mouse. For checkable buttons, `checked`
    colors the on state, which is drawn from `checked_name` if given (an outline heart
    that fills in)."""
    result = QIcon()
    for dpr in _ratios():
        result.addPixmap(_pixmap(name, size, color, dpr), QIcon.Mode.Normal, QIcon.State.Off)
        if hover is not None:
            result.addPixmap(_pixmap(name, size, hover, dpr), QIcon.Mode.Active, QIcon.State.Off)
        disabled = _pixmap(name, size, DISABLED, dpr)
        result.addPixmap(disabled, QIcon.Mode.Disabled, QIcon.State.Off)
        if checked is not None:
            on = _pixmap(checked_name or name, size, checked, dpr)
            result.addPixmap(on, QIcon.Mode.Normal, QIcon.State.On)
            result.addPixmap(on, QIcon.Mode.Active, QIcon.State.On)
    return result


def toggle_icon(name: str, size: int = 20, *, on_name: str | None = None) -> QIcon:
    """Dim when off (brighter under the mouse), violet when on (shuffle, repeat, like)."""
    return icon(name, size, DIM, hover=NORMAL, checked=ACCENT, checked_name=on_name)
