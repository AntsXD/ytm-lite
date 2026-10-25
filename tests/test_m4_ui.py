"""Milestone 4 UI flows with real app objects and no network: page history, the tray
round trip, search result sections."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ytm_lite.app.api import Api
from ytm_lite.app.config import Paths, Settings
from ytm_lite.app.images import ImageLoader
from ytm_lite.app.library import Library
from ytm_lite.app.library_cache import LibraryCache
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import (
    AlbumSummary,
    ArtistRef,
    ArtistSummary,
    Playlist,
    PlaylistSummary,
    SearchResults,
    Track,
)
from ytm_lite.app.play_queue import PlayQueue
from ytm_lite.app.playback import PlaybackController
from ytm_lite.app.player import Player
from ytm_lite.app.resolver import ResolverClient
from ytm_lite.app.secrets import SecretStore
from ytm_lite.app.services import Services
from ytm_lite.app.session import Session
from ytm_lite.app.state import StateKeeper
from ytm_lite.app.worker_client import WorkerProcess
from ytm_lite.app.workers import Workers
from ytm_lite.ui.main_window import MainWindow
from ytm_lite.ui.pages.search_page import result_rows
from ytm_lite.ui.shell import Shell


@pytest.fixture
def services(qapp: QApplication, tmp_path: Path) -> Services:
    """Signed out, nothing started: no worker process, no mpv, no network."""
    paths = Paths(tmp_path / "config", tmp_path / "cache", tmp_path / "logs")
    for folder in (paths.config, paths.cache, paths.logs):
        folder.mkdir()
    settings = Settings()
    workers = Workers()
    worker = WorkerProcess(paths.cache)
    api = Api(worker)
    resolver = ResolverClient(worker)
    session = Session(SecretStore(paths.config / "auth.bin"), api, resolver, workers, settings)
    liked = LikedIndex(api, workers)
    library = Library(api, workers, LibraryCache(paths.cache / "library"), liked, session)
    controller = PlaybackController(PlayQueue(), resolver, Player())
    return Services(
        paths,
        settings,
        workers,
        api,
        resolver,
        session,
        library,
        liked,
        ImageLoader(paths.cache),
        controller,
    )


def test_back_and_forward(services: Services, qtbot: QtBot) -> None:
    window = MainWindow(services)
    qtbot.addWidget(window)
    assert window.current_route() == ("signed_out",)
    window.navigate(("albums",))
    window.navigate(("artists",))
    window.go_back()
    assert window.current_route() == ("albums",)
    window.go_back()
    assert window.current_route() == ("signed_out",)
    window.go_back()  # nothing further back
    assert window.current_route() == ("signed_out",)
    assert not window.top_bar.back.isEnabled() and window.top_bar.forward.isEnabled()
    window.go_forward()
    assert window.current_route() == ("albums",)
    window.navigate(("artists",))  # going somewhere new drops the forward history
    window.go_forward()
    assert window.current_route() == ("artists",)
    assert not window.top_bar.forward.isEnabled()


def test_tray_round_trip_rebuilds_the_window_on_the_same_page(
    services: Services, qtbot: QtBot
) -> None:
    keeper = StateKeeper(services.paths, services.controller, services.workers)
    shell = Shell(services, keeper, {}, tray=False)

    # A playlist page that doesn't go to the network for its tracks.
    def open_playlist(playlist_id: str, title: str = "playlist") -> Playlist | None:
        return Playlist(playlist_id, title, (), True)

    services.library.open_playlist = open_playlist  # type: ignore[method-assign]
    route = ("playlist", "PL1", "Mix")
    window = shell.show_window()
    window.navigate(route)
    window.set_queue_visible(True)
    shell.hide_to_tray()
    assert shell.window is None
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=2000)
    state = keeper.snapshot()[0]
    assert state.window["queue"] is True  # the layout is kept while there's no window
    again = shell.show_window()
    qtbot.addWidget(again)
    assert again is not window
    assert again.current_route() == route
    assert again.queue_visible()


def test_commands_from_another_launch(services: Services, qtbot: QtBot) -> None:
    keeper = StateKeeper(services.paths, services.controller, services.workers)
    shell = Shell(services, keeper, {}, tray=False)
    shell.handle_command("show")
    assert shell.window is not None and shell.window.isVisible()
    qtbot.addWidget(shell.window)
    shell.handle_command("next")  # an empty queue: nothing happens, nothing breaks
    shell.handle_command("bogus")


def test_search_sections() -> None:
    track = Track("t", "Top song", (ArtistRef("A"),))
    results = SearchResults(
        "q",
        None,
        top=ArtistSummary("UC1", "Artist"),
        songs=tuple(Track(f"s{i}", f"S{i}") for i in range(7)),
        albums=(AlbumSummary("MPRE1", "Album"),),
        playlists=(PlaylistSummary("PL1", "List"),),
    )
    rows = result_rows(results)
    kinds = [r.kind for r in rows]
    assert kinds[:2] == ["header", "artist"]
    songs_header = rows[2]
    assert songs_header.text == "Songs" and songs_header.action == "songs"  # 7 > 5 shown
    assert kinds.count("song") == 5
    albums_header = next(r for r in rows if r.text == "Albums")
    assert albums_header.action == ""  # all of them fit
    assert "Artists" not in [r.text for r in rows]  # empty sections are left out
    only_songs = result_rows(SearchResults("q", "songs", songs=(track,)))
    assert [r.kind for r in only_songs] == ["song"]
