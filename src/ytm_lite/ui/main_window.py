"""Main window: rounded panels (sidebar | pages | queue) on a black canvas, the
now-playing bar along the bottom, and a banner on top when something needs you.

In tray mode the window is destroyed and later rebuilt, so everything it connects to a
long-lived object (controller, library, session...) is a bound method, which Qt drops
when the window goes; a lambda would outlive it.
"""

from __future__ import annotations

import random
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QHideEvent,
    QKeySequence,
    QMouseEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.api import ApiError, ApiErrorKind
from ytm_lite.app.models import (
    LIKED_SONGS_ID,
    Account,
    AlbumSummary,
    ArtistSummary,
    Playlist,
    PlaylistSummary,
    SearchItem,
    Track,
)
from ytm_lite.app.play_queue import QueueEntry, RepeatMode
from ytm_lite.app.services import Services
from ytm_lite.ui.debug_overlay import DebugOverlay
from ytm_lite.ui.login_dialog import LoginDialog
from ytm_lite.ui.notify import Banner, Toast
from ytm_lite.ui.now_playing_bar import NowPlayingBar
from ytm_lite.ui.pages.artist_page import ArtistPage
from ytm_lite.ui.pages.collection_page import CollectionPage
from ytm_lite.ui.pages.playlist_page import PlaylistPage
from ytm_lite.ui.pages.search_page import SearchPage
from ytm_lite.ui.pages.signed_out_page import SignedOutPage
from ytm_lite.ui.queue_panel import QueuePanel
from ytm_lite.ui.sidebar import Route, Sidebar
from ytm_lite.ui.theme import GAP
from ytm_lite.ui.top_bar import ContentPanel, SearchBox, TopBar
from ytm_lite.ui.widgets import Panel

_REPEAT_ORDER = (RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE)
_LOAD_ERRORS = {
    ApiErrorKind.NETWORK: "Couldn't reach YouTube. Check your connection and try again.",
    ApiErrorKind.PARSE: "YouTube sent a page this version can't read.",
}


class MainWindow(QMainWindow):
    error_reported = Signal(str)  # thread-safe way to show an error toast
    closed = Signal()  # the user closed the window: the shell decides between tray and quit

    POLL_MS = 250
    MAX_PAGES = 3  # pages kept alive besides the current one are rebuilt on demand
    MAX_HISTORY = 50
    QUEUE_WIDTH = 340

    def __init__(self, services: Services, initial_route: Route | None = None) -> None:
        super().__init__()
        self.s = services
        self._last_gap: float | None = None
        self._pages: OrderedDict[Route, QWidget] = OrderedDict()
        self._route: Route | None = None
        self._history: list[Route] = []
        self._history_index = -1
        self._queue_panel: QueuePanel | None = None  # created when first shown
        self._queue_frame: Panel | None = None
        self._queue_width = self.QUEUE_WIDTH
        self.setWindowTitle("ytm-lite")
        self.resize(1240, 800)

        central = QWidget()
        central.setObjectName("Canvas")
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(GAP, GAP, GAP, 0)
        layout.setSpacing(GAP)
        self.banner = Banner()
        self.banner.action_clicked.connect(self.show_sign_in)
        self.sidebar = Sidebar(services.images)
        self.stack = QStackedWidget()
        self.top_bar = TopBar(SearchBox(services.workers, services.api))
        self.top_bar.back_clicked.connect(self.go_back)
        self.top_bar.forward_clicked.connect(self.go_forward)
        self.top_bar.search.search_requested.connect(self._search)
        self.content = ContentPanel(self.top_bar, self.stack)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(GAP)
        self.splitter.addWidget(Panel(self.sidebar))
        self.splitter.addWidget(self.content)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([250, 990])
        self.splitter.setChildrenCollapsible(False)
        controller = services.controller
        self.bar = NowPlayingBar(services.images)
        self.bar.set_volume(controller.player.volume())
        layout.addWidget(self.banner)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(self.bar)
        self.setCentralWidget(central)

        self.overlay = DebugOverlay(self._overlay_info, central)
        self.overlay.move(20, 20)
        self.toast = Toast(self.content)

        # Playback
        self.bar.play_pause_clicked.connect(controller.toggle_pause)
        self.bar.next_clicked.connect(controller.next)
        self.bar.previous_clicked.connect(controller.previous)
        self.bar.seek_requested.connect(controller.seek)
        self.bar.volume_changed.connect(controller.set_volume)
        self.bar.shuffle_toggled.connect(controller.set_shuffle)
        self.bar.repeat_clicked.connect(self._cycle_repeat)
        self.bar.like_clicked.connect(self._toggle_like)
        self.bar.queue_toggled.connect(self.set_queue_visible)
        controller.current_changed.connect(self._on_current_changed)
        controller.modes_changed.connect(self._on_modes_changed)
        controller.state_changed.connect(self.bar.set_state)
        controller.stream_changed.connect(self.bar.set_stream)
        controller.notice.connect(self.toast.show_message)
        controller.extraction_failing.connect(self._extraction_failing)
        controller.transition_measured.connect(self._on_transition)

        # Library and session
        library = services.library
        self.sidebar.navigate.connect(self.navigate)
        self.sidebar.sign_in_requested.connect(self.show_sign_in)
        self.sidebar.sign_out_requested.connect(self._sign_out)
        self.sidebar.refresh_requested.connect(library.refresh)
        library.playlists_changed.connect(self.sidebar.set_playlists)
        library.playlist_updated.connect(self._on_playlist)
        library.albums_changed.connect(self._albums_changed)
        library.artists_changed.connect(self._artists_changed)
        library.account_changed.connect(self._on_account)
        library.message.connect(self.toast.show_message)
        services.liked.failed.connect(self.toast.show_message)
        services.liked.changed.connect(self._liked_changed)
        services.liked.reset.connect(self._update_like)
        services.session.changed.connect(self._on_session_changed)
        services.session.expired.connect(self._session_expired)
        self.error_reported.connect(self.toast.show_message)
        if library.playlists is not None:
            self.sidebar.set_playlists(library.playlists)

        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._tick)

        shortcuts: tuple[tuple[str, Callable[[], object]], ...] = (
            ("Space", controller.toggle_pause),
            ("Ctrl+Right", controller.next),
            ("Ctrl+Left", controller.previous),
            ("Ctrl+L", self._toggle_like),
            ("Ctrl+F", self.top_bar.search.focus_and_select),
            ("Alt+Left", self.go_back),
            ("Alt+Right", self.go_forward),
            ("Ctrl+Q", QApplication.quit),
            ("Ctrl+Shift+D", self._toggle_overlay),
        )
        for keys, slot in shortcuts:
            QShortcut(QKeySequence(keys), self).activated.connect(slot)
        # The mouse's back and forward buttons, wherever the pointer is in the window.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._on_session_changed(initial_route)
        self._on_modes_changed()
        self._on_current_changed(controller.queue.current())
        self.bar.set_state(controller.state.value)

    def report_error(self, message: str) -> None:
        """Callable from any thread."""
        self.error_reported.emit(message)

    def current_route(self) -> Route | None:
        return self._route

    # -- navigation --------------------------------------------------------------------

    def navigate(self, route: Route) -> None:
        """Show a page, and remember it for Back."""
        if route == self._route:
            return
        del self._history[self._history_index + 1 :]
        self._history.append(route)
        del self._history[: -self.MAX_HISTORY]
        self._history_index = len(self._history) - 1
        self._show(route)

    def go_back(self) -> None:
        if self._history_index > 0:
            self._history_index -= 1
            self._show(self._history[self._history_index])

    def go_forward(self) -> None:
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._show(self._history[self._history_index])

    def _show(self, route: Route) -> None:
        page = self._pages.get(route)
        if page is None:
            page = self._create_page(route)
            self._pages[route] = page
            self.stack.addWidget(page)
        self._pages.move_to_end(route)
        self.stack.setCurrentWidget(page)
        self._route = route
        self.sidebar.select(route)
        tint = getattr(page, "tint", None)
        self.content.set_tint(tint() if callable(tint) else None)
        if route[0] == "search":
            self.top_bar.search.setText(route[1])
        self.top_bar.set_history(
            self._history_index > 0, self._history_index < len(self._history) - 1
        )
        while len(self._pages) > self.MAX_PAGES + 1:
            _, old = self._pages.popitem(last=False)
            self.stack.removeWidget(old)
            old.deleteLater()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and self.isActiveWindow()
        ):
            if event.button() == Qt.MouseButton.BackButton:
                self.go_back()
                return True
            if event.button() == Qt.MouseButton.ForwardButton:
                self.go_forward()
                return True
        return False

    def _search(self, query: str) -> None:
        self.navigate(("search", query))

    def _create_page(self, route: Route) -> QWidget:
        s = self.s
        kind = route[0]
        if kind in ("liked", "playlist"):
            playlist_id = LIKED_SONGS_ID if kind == "liked" else route[1]
            title = "Liked music" if kind == "liked" else route[2]
            page = PlaylistPage(playlist_id, title, s.images, s.liked, s.controller, self.navigate)
            self._connect_tracks(page)
            known = s.library.open_playlist(playlist_id, title)
            if known is not None:
                page.set_playlist(known)
            return page
        if kind == "album":
            browse_id = route[1]
            album_page = PlaylistPage(
                browse_id, route[2], s.images, s.liked, s.controller, self.navigate, "Album"
            )
            self._connect_tracks(album_page)
            api = s.api
            self._load(album_page, lambda: api.album(browse_id), album_page.set_album)
            return album_page
        if kind == "artist":
            channel_id = route[1]
            artist_page = ArtistPage(
                channel_id, route[2], s.images, s.liked, s.controller, self.navigate
            )
            self._connect_tracks(artist_page)
            artist_page.item_activated.connect(self._open_item)
            api = s.api
            self._load(artist_page, lambda: api.artist(channel_id), artist_page.set_artist)
            return artist_page
        if kind == "search":
            search = SearchPage(
                route[1], s.workers, s.api, s.images, s.liked, s.controller, self.navigate
            )
            search.song_requested.connect(s.controller.start_radio)
            search.item_activated.connect(self._open_item)
            return search
        if kind in ("albums", "artists"):
            albums = kind == "albums"
            collection = CollectionPage(
                "Albums" if albums else "Artists", s.images, round_covers=not albums
            )
            collection.set_items(s.library.albums if albums else s.library.artists)
            collection.activated.connect(self._open_item)
            return collection
        signed_out = SignedOutPage(s.images, s.liked, s.controller)
        signed_out.sign_in_clicked.connect(self.show_sign_in)
        signed_out.play_requested.connect(self._play)
        return signed_out

    def _connect_tracks(self, page: PlaylistPage | ArtistPage) -> None:
        page.play_requested.connect(self._play)
        page.shuffle_requested.connect(self._shuffle_play)
        page.tint_changed.connect(self._page_tint)

    def _load(
        self,
        page: PlaylistPage | ArtistPage,
        fetch: Callable[[], Any],
        on_done: Callable[[Any], None],
    ) -> None:
        """Fetch a page's content on the api pool; dropped if the page goes first."""

        def failed(error: Exception) -> None:
            message = "Couldn't load this page."
            if isinstance(error, ApiError):
                message = _LOAD_ERRORS.get(error.kind, message)
            page.show_error(message)

        task = self.s.workers.submit(fetch, on_done=on_done, on_error=failed)
        page.destroyed.connect(task.cancel)

    def _open_item(self, item: SearchItem) -> None:
        if isinstance(item, AlbumSummary):
            self.navigate(("album", item.id, item.title))
        elif isinstance(item, ArtistSummary):
            self.navigate(("artist", item.id, item.name))
        elif isinstance(item, PlaylistSummary):
            self.navigate(("playlist", item.id, item.title))
        elif isinstance(item, Track):
            self.s.controller.start_radio(item)

    def _page_tint(self, color: QColor) -> None:
        if self.sender() is self.stack.currentWidget():
            self.content.set_tint(color)

    def _drop_pages(self) -> None:
        for page in self._pages.values():
            self.stack.removeWidget(page)
            page.deleteLater()
        self._pages.clear()
        self._route = None
        self._history.clear()
        self._history_index = -1

    def _play(self, tracks: Sequence[Any], row: int) -> None:
        self.s.controller.play_tracks(list(tracks), row)

    def _shuffle_play(self, tracks: Sequence[Track]) -> None:
        playable = [row for row, track in enumerate(tracks) if track.available]
        if playable:
            self.s.controller.play_tracks(list(tracks), random.choice(playable), shuffle=True)

    def _on_playlist(self, playlist: Playlist) -> None:
        for page in self._pages.values():
            if isinstance(page, PlaylistPage) and page.playlist_id == playlist.id:
                page.set_playlist(playlist)

    def _albums_changed(self, items: Sequence[AlbumSummary]) -> None:
        self._update_collection(("albums",), items)

    def _artists_changed(self, items: Sequence[ArtistSummary]) -> None:
        self._update_collection(("artists",), items)

    def _update_collection(
        self, route: Route, items: Sequence[AlbumSummary] | Sequence[ArtistSummary]
    ) -> None:
        page = self._pages.get(route)
        if isinstance(page, CollectionPage):
            page.set_items(items)

    # -- now playing and the queue ------------------------------------------------------

    def _on_current_changed(self, entry: QueueEntry | None) -> None:
        self.bar.set_entry(entry)
        self._update_like()

    def _on_modes_changed(self) -> None:
        controller, settings = self.s.controller, self.s.settings
        self.bar.set_modes(controller.queue.shuffled, controller.queue.repeat)
        if settings.radio_autoplay != controller.radio:
            settings.radio_autoplay = controller.radio
            settings.save(self.s.paths.settings_file)

    def _cycle_repeat(self) -> None:
        current = self.s.controller.queue.repeat
        self.s.controller.set_repeat(_REPEAT_ORDER[(_REPEAT_ORDER.index(current) + 1) % 3])

    def _toggle_like(self) -> None:
        entry = self.s.controller.queue.current()
        if entry is not None:
            self.s.liked.toggle(entry.track)
        self._update_like()  # the toggle may have been refused (signed out)

    def _liked_changed(self, video_id: str) -> None:
        entry = self.s.controller.queue.current()
        if entry is not None and entry.track.video_id == video_id:
            self._update_like()

    def _update_like(self) -> None:
        entry = self.s.controller.queue.current()
        self.bar.set_liked(entry is not None and self.s.liked.is_liked(entry.track.video_id))

    def set_queue_visible(self, visible: bool) -> None:
        frame = self._queue_frame
        if visible and frame is None:
            self._queue_panel = QueuePanel(self.s.controller, self.s.images)
            frame = self._queue_frame = Panel(self._queue_panel)
            frame.setMinimumWidth(260)
            frame.hide()
            self.splitter.addWidget(frame)
            self.splitter.setStretchFactor(2, 0)
        if frame is not None and visible != frame.isVisibleTo(self):
            sizes = self.splitter.sizes()
            if visible:
                frame.show()
                width = min(self._queue_width, max(260, sizes[1] // 2))
                self.splitter.setSizes([sizes[0], max(200, sizes[1] - width), width])
            else:
                self._queue_width = sizes[2]
                frame.hide()
        self.bar.set_queue_visible(visible)

    def queue_visible(self) -> bool:
        return self._queue_frame is not None and self._queue_frame.isVisibleTo(self)

    # -- window layout, saved with the playback session ---------------------------------

    def ui_state(self) -> dict[str, Any]:
        sizes = self.splitter.sizes()
        if self.queue_visible() and len(sizes) > 2:
            self._queue_width = sizes[2]
        return {
            "geometry": bytes(self.saveGeometry().toBase64().data()).decode("ascii"),
            "sidebar": sizes[0],
            "queue": self.queue_visible(),
            "queue_width": self._queue_width,
        }

    def restore_ui_state(self, state: dict[str, Any]) -> None:
        geometry = state.get("geometry")
        if isinstance(geometry, str):
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        sidebar = state.get("sidebar")
        if isinstance(sidebar, int) and 150 <= sidebar <= 600:
            total = sum(self.splitter.sizes())
            self.splitter.setSizes([sidebar, max(200, total - sidebar)])
        width = state.get("queue_width")
        if isinstance(width, int) and 260 <= width <= 1000:
            self._queue_width = width
        if state.get("queue") is True:
            self.set_queue_visible(True)

    # -- session -----------------------------------------------------------------------

    def _on_session_changed(self, route: Route | None = None) -> None:
        session = self.s.session
        if session.signed_in:
            self.banner.hide()
            self.sidebar.set_account(session.account_name or "Signed in")
        else:
            self.sidebar.set_account(None)
        self._drop_pages()
        library_only = ("liked", "albums", "artists")
        if route is None or (not session.signed_in and route[0] in library_only):
            route = ("liked",) if session.signed_in else ("signed_out",)
        self.navigate(route)

    def _on_account(self, account: Account | None) -> None:
        if account is not None:
            self.sidebar.set_account(account.name)

    def _session_expired(self) -> None:
        self.banner.show_message("Your YouTube Music session has expired.", "Sign in again")

    def _extraction_failing(self) -> None:
        self.banner.show_message("Stream extraction keeps failing — try updating yt-dlp.")

    def show_sign_in(self) -> None:
        dialog = LoginDialog(self.s.workers, self.s.api, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def accepted() -> None:
            if dialog.credentials is not None and dialog.account is not None:
                self.s.session.sign_in(dialog.credentials, dialog.account)
                self.toast.show_message(f"Signed in as {dialog.account.name}")

        def rejected() -> None:
            if not self.s.session.signed_in and not self.s.settings.skip_sign_in:
                self.s.settings.skip_sign_in = True
                self.s.settings.save(self.s.paths.settings_file)

        dialog.accepted.connect(accepted)
        dialog.rejected.connect(rejected)
        dialog.open()

    def maybe_prompt_sign_in(self) -> None:
        if not self.s.session.signed_in and not self.s.settings.skip_sign_in:
            self.show_sign_in()

    def _sign_out(self) -> None:
        answer = QMessageBox.question(
            self,
            "Sign out",
            "Sign out of YouTube Music? Your stored session and cached library are removed.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.s.session.sign_out()

    # -- misc --------------------------------------------------------------------------

    def _toggle_overlay(self) -> None:
        self.overlay.setVisible(not self.overlay.isVisible())
        self.overlay.raise_()

    def _on_transition(self, gap_ms: float) -> None:
        self._last_gap = gap_ms

    def _update_polling(self) -> None:
        if self.isVisible() and not self.isMinimized():
            if not self._poll.isActive():
                self._tick()
                self._poll.start()
        else:
            self._poll.stop()

    def _tick(self) -> None:
        controller = self.s.controller
        self.bar.set_position(controller.position(), controller.duration())

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._update_polling()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._update_polling()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_polling()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._poll.stop()
        super().closeEvent(event)
        self.closed.emit()

    def _overlay_info(self) -> dict[str, Any]:
        s = self.s
        player_stats = s.controller.player.stats()
        worker = s.resolver.worker_info
        runtime = worker.get("js_runtime") or {}
        count, size = s.images.memory_usage()
        info: dict[str, Any] = {
            "session": s.session.credentials.method if s.session.credentials else "signed out",
            "playback": s.controller.state.value,
            "queue": (
                f"{len(s.controller.queue):,} songs, {s.controller.queue.remaining():,} up next"
                f"{', autoplay' if s.controller.radio else ''}"
            ),
            "libmpv": "loaded" if player_stats.get("active") else "not loaded",
            "resolver": "running" if s.resolver.is_running() else "stopped",
            "js runtime": f"{runtime.get('name', '–')} {runtime.get('version', '')}".strip(),
            "yt-dlp": (worker.get("versions") or {}).get("yt-dlp", "–"),
            "covers": f"{count} in memory, {size / 1e6:.1f} MB",
            "pages": f"{len(self._pages)} open, {len(self._history)} in history",
            "last gap": "–" if self._last_gap is None else f"{self._last_gap:.0f} ms",
        }
        if player_stats.get("active"):
            cache = player_stats.get("cache_bytes")
            info["demux cache"] = "–" if cache is None else f"{cache / 1e6:.1f} MB"
            info["audio"] = f"{player_stats.get('codec')} @ {player_stats.get('samplerate')} Hz"
        return info
