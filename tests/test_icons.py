from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from ytm_lite.ui import icons


def test_relative_commands_and_implicit_repeats() -> None:
    # "m" then two pairs: the second pair is an implicit relative line-to.
    path = icons.parse_path("m2 2 10 0v10h-10z")
    assert path.boundingRect() == QRectF(2, 2, 10, 10)
    assert path.elementCount() == 5


def test_smooth_curves_mirror_the_previous_control_point() -> None:
    path = icons.parse_path("M0 0C0 10 10 10 10 0S20 -10 20 0")
    rect = path.boundingRect()
    assert rect.left() == 0 and rect.right() == 20
    assert rect.top() < -5 and rect.bottom() > 5  # the S half bulges the other way


def test_numbers_without_separators() -> None:
    path = icons.parse_path("M1.5.5l-.1.1-.1-.1z")
    assert path.elementAt(0).x == 1.5 and path.elementAt(0).y == 0.5


def test_unsupported_commands_are_rejected() -> None:
    with pytest.raises(ValueError):
        icons.parse_path("M0 0A5 5 0 1 0 10 10")


@pytest.mark.parametrize("name", sorted(icons.PATHS))
def test_every_icon_parses_inside_the_grid(qapp: QApplication, name: str) -> None:
    rect = icons.parse_path(icons.PATHS[name]).boundingRect()
    assert QRectF(0, 0, 24, 24).contains(rect)
    assert not icons.icon(name).isNull()
