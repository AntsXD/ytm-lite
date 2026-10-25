"""Dark theme: rounded panels on a black canvas, with a violet accent.

Colors are named here once and used by name everywhere else. Big views (track lists,
the queue, the sidebar) are painted by delegates instead of QSS item rules, which keeps
painting fast; the stylesheet only covers chrome (scrollbars, buttons, menus, inputs).
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

CANVAS = "#000000"  # behind the panels
PANEL = "#121212"
HOVER = "#1f1f1f"  # row under the mouse
SELECTED = "#2a2a2a"  # selected row, pressed button
RAISED = "#282828"  # menus, toasts, tooltips
BORDER = "#2a2a2a"
TEXT = "#f1f1f1"
TEXT_DIM = "#a7a7a7"
TEXT_FAINT = "#6a6a6a"  # played queue entries, unavailable tracks
ACCENT = "#a78bfa"  # violet, for text, icons and progress on dark backgrounds
ACCENT_FILL = "#8b5cf6"  # violet, for filled buttons with white text
ACCENT_FILL_HOVER = "#9d76f8"
PLACEHOLDER = "#2a2a2a"  # a cover that hasn't loaded
WARNING_BG = "#3a2a10"

RADIUS = 8  # panels and cards
GAP = 8  # between panels

_QSS = f"""
#Canvas {{ background: {CANVAS}; }}
#Panel {{ background: {PANEL}; border-radius: {RADIUS}px; }}
#Panel QAbstractItemView {{ background: transparent; border: none; outline: 0; }}
QSplitter::handle {{ background: transparent; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: transparent; color: {TEXT_DIM}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 0 8px;
}}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: #4a4a4a; border-radius: 3px; }}
QScrollBar::handle:hover {{ background: #6a6a6a; }}
QScrollBar::handle:vertical {{ min-height: 32px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ min-width: 32px; margin: 3px 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolButton {{ border: none; padding: 4px; border-radius: 6px; background: transparent; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:pressed {{ background: {SELECTED}; }}
QPushButton {{
    background: {SELECTED}; color: {TEXT}; border: none; border-radius: 16px;
    padding: 7px 18px;
}}
QPushButton:hover {{ background: #343434; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; }}
QPushButton#Primary {{ background: {ACCENT_FILL}; color: #ffffff; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_FILL_HOVER}; }}
QPushButton#Account {{
    background: transparent; text-align: left; padding: 6px 8px; border-radius: 6px;
    font-weight: 600;
}}
QPushButton#Account:hover {{ background: {HOVER}; }}
QPushButton#Account::menu-indicator {{ width: 0; image: none; }}
QPushButton#Flat {{ background: transparent; color: {TEXT_DIM}; padding: 4px 10px; }}
QPushButton#Flat:hover {{ color: {TEXT}; background: {HOVER}; }}
QLineEdit, QPlainTextEdit, QComboBox {{
    background: {HOVER}; border: 1px solid #333333; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {ACCENT_FILL};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM}; padding: 6px 14px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QMenu {{ background: {RAISED}; border: 1px solid #3a3a3a; padding: 4px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: #3e3e3e; }}
QMenu::item:disabled {{ color: {TEXT_FAINT}; }}
QMenu::separator {{ height: 1px; background: #3a3a3a; margin: 4px 6px; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid #3a3a3a; padding: 4px 8px; }}
#Dim {{ color: {TEXT_DIM}; }}
#Toast {{
    background: {RAISED}; color: {TEXT}; border: 1px solid #3a3a3a; border-radius: {RADIUS}px;
    padding: 10px 14px;
}}
#Banner {{ background: {WARNING_BG}; border-radius: {RADIUS}px; }}
#DebugOverlay {{ background: #000000; border: 1px solid #444444; }}
"""


def font(point_size: float | None = None, weight: QFont.Weight | None = None) -> QFont:
    """The app font at another size and/or weight."""
    result = QFont(QApplication.font())
    if point_size is not None:
        result.setPointSizeF(point_size)
    if weight is not None:
        result.setWeight(weight)
    return result


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: PANEL,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: "#181818",
        QPalette.ColorRole.AlternateBase: "#1a1a1a",
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SELECTED,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.ToolTipBase: RAISED,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT_FILL,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Link: ACCENT,
        QPalette.ColorRole.PlaceholderText: TEXT_FAINT,
        QPalette.ColorRole.Mid: BORDER,
        QPalette.ColorRole.Dark: "#0a0a0a",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_FAINT))
    app.setPalette(palette)
    app.setStyleSheet(_QSS)
