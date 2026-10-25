"""The sidebar: what it lists signed in and out, and which clicks navigate."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ytm_lite.app.images import ImageLoader
from ytm_lite.app.models import LIKED_SONGS_ID, PlaylistSummary
from ytm_lite.ui.sidebar import Route, Sidebar


def test_signed_in_lists_library_and_playlists(qapp: QApplication, tmp_path: Path) -> None:
    sidebar = Sidebar(ImageLoader(tmp_path))
    routes: list[Route] = []
    sidebar.navigate.connect(routes.append)
    assert [i.kind for i in sidebar.model.items] == ["hint"]  # signed out
    assert not sidebar.account.isVisibleTo(sidebar) and sidebar.sign_in.isVisibleTo(sidebar)

    sidebar.set_account("Ants")
    sidebar.set_playlists(
        [PlaylistSummary(LIKED_SONGS_ID, "Liked"), PlaylistSummary("PL1", "Mix", author="me")]
    )
    items = sidebar.model.items
    assert [i.text for i in items] == ["Liked music", "Albums", "Artists", "Playlists", "Mix"]
    assert items[-1].subtitle == "Playlist · me"
    assert sidebar.model.flags(sidebar.model.index(3)) == Qt.ItemFlag.NoItemFlags  # header

    sidebar.select(("playlist", "PL1", "Mix"))
    assert routes == []  # highlighting doesn't navigate
    sidebar.view.setCurrentIndex(sidebar.model.index(1))
    assert routes == [("albums",)]
    sidebar.set_playlists([PlaylistSummary("PL1", "Mix"), PlaylistSummary("PL2", "Other")])
    assert sidebar.view.currentIndex().row() == 1  # still on Albums after the refresh
    assert routes == [("albums",)]
