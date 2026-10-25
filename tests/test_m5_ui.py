"""Milestone 5 UI flows with real app objects and no network: the mini player in place
of the window, the queue/lyrics side panel, settings, home shelves, synced lyrics."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QListView
from pytestqt.qtbot import QtBot

from ytm_lite.app.models import (
    AlbumSummary,
    HomeShelf,
    LyricLine,
    Lyrics,
    PlaylistSummary,
    Track,
)
from ytm_lite.app.services import Services
from ytm_lite.app.state import StateKeeper
from ytm_lite.integrations.hotkeys import Hotkeys
from ytm_lite.ui.lyrics_panel import LyricsPanel
from ytm_lite.ui.pages.home_page import HomePage, greeting, two_lines
from ytm_lite.ui.settings_dialog import SettingsDialog
from ytm_lite.ui.shell import Shell


def make_shell(services: Services, **kwargs: Any) -> Shell:
    return Shell(
        services,
        StateKeeper(services.paths, services.controller, services.workers),
        {},
        tray=False,
        **kwargs,
    )


def test_the_mini_player_replaces_the_window(services: Services, qtbot: QtBot) -> None:
    shell = make_shell(services)
    window = shell.show_window()
    window.set_side_panel("lyrics")
    shell.show_mini_player()
    mini = shell.mini
    assert mini is not None and mini.isVisible()
    assert shell.window is None  # gone, like in tray mode
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=2000)
    mini.move(123, 234)
    assert shell._ui_state()["mini"] == [123, 234]
    again = shell.show_window()
    qtbot.addWidget(again)
    assert shell.mini is None and again.side_panel() == "lyrics"  # the layout came back
    shell.show_mini_player()
    assert shell.mini is not None and shell.mini.pos().x() == 123  # where you left it
    shell.show_window()


def test_side_panel_switches_between_queue_and_lyrics(services: Services, qtbot: QtBot) -> None:
    shell = make_shell(services)
    window = shell.show_window()
    qtbot.addWidget(window)
    window.set_side_panel("queue")
    assert window.queue_visible() and window.bar.queue_button.isChecked()
    window.bar.lyrics_button.click()
    assert window.side_panel() == "lyrics" and not window.bar.queue_button.isChecked()
    window.bar.lyrics_button.click()  # the active one again: closes the panel
    assert window.side_panel() is None
    assert window.ui_state()["panel"] is None


class RecordingHotkeys(Hotkeys):
    def __init__(self) -> None:
        super().__init__()
        self.applied: list[dict[str, str]] = []

    def apply(self, hotkeys: dict[str, str]) -> dict[str, str]:
        self.applied.append(dict(hotkeys))
        return {"like": "Another app already uses this key"} if "like" in hotkeys else {}


def test_settings_apply_and_save(services: Services, qtbot: QtBot) -> None:
    hotkeys = RecordingHotkeys()
    shell = make_shell(services, hotkeys=hotkeys)
    new = services.settings.copy()
    new.normalize = "dynamic"
    new.radio_autoplay = False
    new.hotkeys = {"next": "Ctrl+Alt+N"}
    assert shell.apply_settings(new) == {}
    assert services.controller.normalize == "dynamic" and not services.controller.radio
    saved = json.loads(services.paths.settings_file.read_text(encoding="utf-8"))
    assert saved["normalize"] == "dynamic" and saved["hotkeys"] == {"next": "Ctrl+Alt+N"}
    assert hotkeys.applied[-1] == {"next": "Ctrl+Alt+N"}

    dialog = SettingsDialog(
        services.settings, apply=shell.apply_settings, audio_devices=lambda: [("wasapi/x", "X")]
    )
    qtbot.addWidget(dialog)
    assert dialog.edited().normalize == "dynamic"
    dialog._keys["like"].setKeySequence("Ctrl+Alt+L")
    dialog._ok()  # the like hotkey is refused: shown, and the dialog stays open
    assert dialog.result() == 0 and dialog._problems["like"].text().startswith("Another app")
    assert dialog._tabs.currentIndex() == 2
    dialog._keys["like"].clear()
    dialog._ok()
    assert dialog.result() == 1


def test_home_greets_and_lays_shelves_out_sideways(services: Services, qtbot: QtBot) -> None:
    assert greeting(datetime(2026, 1, 1, 8), "Ants Doe") == "Good morning, Ants"
    assert greeting(datetime(2026, 1, 1, 21)) == "Good evening"
    page = HomePage(
        services.workers, services.api, services.images, services.liked, services.controller
    )
    qtbot.addWidget(page)
    songs = HomeShelf("Quick picks", tuple(Track(f"s{i}", f"S{i}") for i in range(9)))
    album = AlbumSummary("MPRE1", "Album")
    mixed = HomeShelf("New releases", (album, PlaylistSummary("PL1", "Mix")))
    page._got((songs, mixed))
    quick, releases = page.shelves
    assert quick.view.songs and quick.view.flow() == QListView.Flow.TopToBottom  # columns
    assert not releases.view.songs and releases.view.flow() == QListView.Flow.LeftToRight
    opened: list[Any] = []
    played: list[Any] = []
    radio: list[Any] = []
    page.item_activated.connect(opened.append)
    page.play_requested.connect(played.append)
    page.song_requested.connect(radio.append)
    releases.view.opened.emit(album)
    releases.view.play.emit(album)
    quick.view.play.emit(songs.items[0])
    assert opened == [album] and played == [album] and radio == [songs.items[0]]


def test_titles_wrap_onto_two_lines() -> None:
    from PySide6.QtGui import QFontMetrics

    from ytm_lite.ui.theme import font

    metrics = QFontMetrics(font(10))
    width = metrics.horizontalAdvance("Random Access")
    assert two_lines(metrics, "Random Access Memories", width) == ["Random Access", "Memories"]
    lines = two_lines(metrics, "Random Access Memories (Drumless Edition)", width)
    assert len(lines) == 2 and lines[1].endswith("…")
    assert two_lines(metrics, "Short", width) == ["Short"]


class FakeLyricsApi(QObject):
    answered = Signal()

    def lyrics(self, video_id: str) -> Lyrics | None:
        lines = (
            LyricLine(0, 900, "one"),
            LyricLine(1000, 1900, "two"),
            LyricLine(2000, 0, "three"),
        )
        return Lyrics(lines=lines, source="Source: test") if video_id == "sung" else None


def test_synced_lyrics_follow_the_song(services: Services, qtbot: QtBot) -> None:
    panel = LyricsPanel(services.workers, FakeLyricsApi())  # type: ignore[arg-type]
    qtbot.addWidget(panel)
    panel.show()
    panel.set_track(Track("sung", "Sung"))
    qtbot.waitUntil(lambda: panel.view.lyrics_model.synced, timeout=3000)
    assert panel.source.text() == "Source: test"
    panel.set_position(1.2)
    assert panel.view.lyrics_model.current == 1
    panel.set_position(5.0)
    assert panel.view.lyrics_model.current == 2
    seeks: list[float] = []
    panel.seek_requested.connect(seeks.append)
    panel.view.clicked.emit(panel.view.lyrics_model.index(0))
    assert seeks == [0.0]
    panel.set_track(Track("silent", "Silent"))
    qtbot.waitUntil(lambda: panel.message.text() == "No lyrics for this song.", timeout=3000)
