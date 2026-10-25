"""Dark theme: Fusion style with a dark palette and a deliberately small stylesheet.

Big views are painted by delegates instead of QSS item rules, which keeps table
painting fast.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#ff3a46"

_QSS = f"""
QToolButton {{ border: none; padding: 4px; border-radius: 4px; }}
QToolButton:hover {{ background: #2c2c2c; }}
QToolButton:checked {{ color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: #3a3a3a; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 12px; margin: -4px 0; border-radius: 6px; background: #f0f0f0;
}}
#NowPlayingBar {{ background: #141414; border-top: 1px solid #262626; }}
#TrackTitle {{ font-weight: 600; }}
#Dim {{ color: #9a9a9a; }}
#DebugOverlay {{ background: #000000; border: 1px solid #444444; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#1b1b1b",
        QPalette.ColorRole.WindowText: "#e8e8e8",
        QPalette.ColorRole.Base: "#121212",
        QPalette.ColorRole.AlternateBase: "#181818",
        QPalette.ColorRole.Text: "#e8e8e8",
        QPalette.ColorRole.Button: "#242424",
        QPalette.ColorRole.ButtonText: "#e8e8e8",
        QPalette.ColorRole.ToolTipBase: "#2a2a2a",
        QPalette.ColorRole.ToolTipText: "#e8e8e8",
        QPalette.ColorRole.Highlight: "#3d3d3d",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: ACCENT,
        QPalette.ColorRole.PlaceholderText: "#7a7a7a",
        QPalette.ColorRole.Mid: "#2a2a2a",
        QPalette.ColorRole.Dark: "#101010",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#666666"))
    app.setPalette(palette)
    app.setStyleSheet(_QSS)
