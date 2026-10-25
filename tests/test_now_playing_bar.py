"""The bar's sliders: a click anywhere along their height jumps there; drags follow."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ytm_lite.app.images import ImageLoader
from ytm_lite.ui.now_playing_bar import ClickSlider, NowPlayingBar
from ytm_lite.ui.theme import apply_theme

LEFT = Qt.MouseButton.LeftButton
NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture
def slider(qapp: QApplication, qtbot: QtBot) -> ClickSlider:
    apply_theme(qapp)  # the 4 px groove from the theme is what made clicks miss
    slider = ClickSlider()
    slider.setRange(0, 100)
    slider.resize(200, 24)
    qtbot.addWidget(slider)
    slider.show()
    qtbot.waitExposed(slider)
    return slider


@pytest.mark.parametrize("y", [0, 5, 12, 18, 23])
def test_a_click_at_any_height_jumps_to_the_far_end(
    slider: ClickSlider, qtbot: QtBot, y: int
) -> None:
    slider.setValue(100)
    QTest.mouseClick(slider, LEFT, NONE, QPoint(1, y))
    assert slider.value() == 0
    QTest.mouseClick(slider, LEFT, NONE, QPoint(slider.width() - 1, y))
    assert slider.value() == 100


def test_a_click_in_the_middle_lands_in_the_middle(slider: ClickSlider, qtbot: QtBot) -> None:
    QTest.mouseClick(slider, LEFT, NONE, QPoint(slider.width() // 2, 3))
    assert 45 <= slider.value() <= 55


def test_dragging_follows_the_mouse(slider: ClickSlider, qtbot: QtBot) -> None:
    values: list[int] = []
    slider.valueChanged.connect(values.append)
    slider.setValue(100)
    QTest.mousePress(slider, LEFT, NONE, QPoint(slider.width() - 1, 12))
    QTest.mouseMove(slider, QPoint(slider.width() // 4, 12))
    QTest.mouseRelease(slider, LEFT, NONE, QPoint(slider.width() // 4, 12))
    assert 20 <= slider.value() <= 30
    assert not slider.isSliderDown()


def test_bar_volume_and_seek(qapp: QApplication, qtbot: QtBot, tmp_path: Path) -> None:
    apply_theme(qapp)
    bar = NowPlayingBar(ImageLoader(tmp_path))
    qtbot.addWidget(bar)
    bar.resize(1200, 64)
    bar.show()
    qtbot.waitExposed(bar)
    volumes: list[float] = []
    seeks: list[float] = []
    bar.volume_changed.connect(volumes.append)
    bar.seek_requested.connect(seeks.append)

    bar.set_volume(100)
    QTest.mouseClick(bar.volume, LEFT, NONE, QPoint(1, 1))
    assert volumes == [0.0]

    bar.set_position(30.0, 200.0)
    seek = bar.seek
    QTest.mousePress(seek, LEFT, NONE, QPoint(seek.width() - 1, seek.height() // 2))
    bar.set_position(31.0, 200.0)  # playback ticks on while the mouse is down...
    assert bar.elapsed.text() == "3:20"  # ...but the time shows where you're dragging
    QTest.mouseRelease(seek, LEFT, NONE, QPoint(seek.width() - 1, seek.height() // 2))
    assert seeks == [200.0]  # one seek, on release
