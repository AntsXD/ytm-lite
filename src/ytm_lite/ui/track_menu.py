"""The right-click menu for a track, shared by every list that shows tracks."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu, QWidget

from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.models import Track
from ytm_lite.app.playback import PlaybackController

Route = tuple[str, ...]
Navigate = Callable[[Route], None]


def show_track_menu(
    parent: QWidget,
    at: QPoint,
    track: Track,
    selection: Sequence[Track],
    *,
    controller: PlaybackController,
    liked: LikedIndex,
    navigate: Navigate | None,
    play: Callable[[], None],
) -> None:
    """`selection`: the tracks Play next / Add to queue act on (the selected rows when
    `track` is among them, else just `track`). `at` is in global coordinates."""
    tracks = [t for t in selection if t.available]
    menu = QMenu(parent)
    action = menu.addAction("Play")
    action.setEnabled(track.available)
    action.triggered.connect(play)
    many = f" ({len(tracks)} songs)" if len(tracks) > 1 else ""
    action = menu.addAction(f"Play next{many}")
    action.setEnabled(bool(tracks))
    action.triggered.connect(lambda: controller.play_next(tracks))
    action = menu.addAction(f"Add to queue{many}")
    action.setEnabled(bool(tracks))
    action.triggered.connect(lambda: controller.add_to_queue(tracks))
    action = menu.addAction("Start radio")
    action.setEnabled(track.available)
    action.triggered.connect(lambda: controller.start_radio(track))
    if navigate is not None:
        menu.addSeparator()
        album = track.album
        if album is not None and album.id:
            route = ("album", album.id, album.name)
            menu.addAction("Go to album").triggered.connect(lambda: navigate(route))
        artists = [a for a in track.artists if a.id]
        if len(artists) == 1:
            artist_route = ("artist", artists[0].id or "", artists[0].name)
            menu.addAction("Go to artist").triggered.connect(lambda: navigate(artist_route))
        elif artists:
            submenu = menu.addMenu("Go to artist")
            for artist in artists:
                route_of = ("artist", artist.id or "", artist.name)
                submenu.addAction(artist.name).triggered.connect(
                    lambda _=False, r=route_of: navigate(r)
                )
    menu.addSeparator()
    like_text = "Remove from liked songs" if liked.is_liked(track.video_id) else "Like"
    menu.addAction(like_text).triggered.connect(lambda: liked.toggle(track))
    menu.addAction("Copy link").triggered.connect(
        lambda: QGuiApplication.clipboard().setText(track.link)
    )
    menu.exec(at)
