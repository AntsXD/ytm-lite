"""Main window: sidebar | pages, now-playing bar at the bottom, banner on top."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.models import LIKED_SONGS_ID, Account, AlbumSummary, ArtistSummary, Playlist
from ytm_lite.app.services import Services
from ytm_lite.ui.debug_overlay import DebugOverlay
from ytm_lite.ui.login_dialog import LoginDialog
from ytm_lite.ui.notify import Banner, Toast
from ytm_lite.ui.now_playing_bar import NowPlayingBar
from ytm_lite.ui.pages.collection_page import CollectionPage
from ytm_lite.ui.pages.playlist_page import PlaylistPage
from ytm_lite.ui.pages.signed_out_page import SignedOutPage
from ytm_lite.ui.sidebar import Route, Sidebar


class MainWindow(QMainWindow):
    error_reported = Signal(str)  # thread-safe way to show an error toast

    POLL_MS = 250
    MAX_PAGES = 3  # pages kept alive besides the current one are rebuilt on demand

    def __init__(self, services: Services) -> None:
        super().__init__()
        self.s = services
        self._last_gap: float | None = None
        self._pages: OrderedDict[Route, QWidget] = OrderedDict()
        self._route: Route | None = None
        self.setWindowTitle("ytm-lite")
        self.resize(1200, 760)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.banner = Banner()
        self.banner.action_clicked.connect(self.show_sign_in)
        self.sidebar = Sidebar()
        self.stack = QStackedWidget()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 970])
        splitter.setChildrenCollapsible(False)
        controller = services.controller
        self.bar = NowPlayingBar()
        self.bar.set_volume(controller.player.volume())
        layout.addWidget(self.banner)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.bar)
        self.setCentralWidget(central)

        self.overlay = DebugOverlay(self._overlay_info, central)
        self.overlay.move(12, 12)
        self.toast = Toast(central)

        # Playback
        self.bar.play_pause_clicked.connect(controller.toggle_pause)
        self.bar.next_clicked.connect(controller.next)
        self.bar.previous_clicked.connect(controller.previous)
        self.bar.seek_requested.connect(controller.seek)
        self.bar.volume_changed.connect(controller.set_volume)
        controller.current_changed.connect(self.bar.set_entry)
        controller.state_changed.connect(self.bar.set_state)
        controller.stream_changed.connect(self.bar.set_stream)
        controller.notice.connect(self.toast.show_message)
        controller.extraction_failing.connect(
            lambda: self.banner.show_message(
                "Stream extraction keeps failing — try updating yt-dlp."
            )
        )
        controller.transition_measured.connect(self._on_transition)

        # Library and session
        library = services.library
        self.sidebar.navigate.connect(self.navigate)
        self.sidebar.sign_in_requested.connect(self.show_sign_in)
        self.sidebar.sign_out_requested.connect(self._sign_out)
        self.sidebar.refresh_requested.connect(library.refresh)
        library.playlists_changed.connect(self.sidebar.set_playlists)
        library.playlist_updated.connect(self._on_playlist)
        library.albums_changed.connect(lambda items: self._update_collection(("albums",), items))
        library.artists_changed.connect(lambda items: self._update_collection(("artists",), items))
        library.account_changed.connect(self._on_account)
        library.message.connect(self.toast.show_message)
        services.liked.failed.connect(self.toast.show_message)
        services.session.changed.connect(self._on_session_changed)
        services.session.expired.connect(
            lambda: self.banner.show_message(
                "Your YouTube Music session has expired.", "Sign in again"
            )
        )
        self.error_reported.connect(self.toast.show_message)

        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._tick)

        for keys, slot in (
            ("Space", controller.toggle_pause),
            ("Ctrl+Right", controller.next),
            ("Ctrl+Left", controller.previous),
            ("Ctrl+Q", QApplication.quit),
            ("Ctrl+Shift+D", self._toggle_overlay),
        ):
            QShortcut(QKeySequence(keys), self).activated.connect(slot)

        self._on_session_changed()

    def report_error(self, message: str) -> None:
        """Callable from any thread."""
        self.error_reported.emit(message)

    # -- navigation --------------------------------------------------------------------

    def navigate(self, route: Route) -> None:
        if route == self._route:
            return
        page = self._pages.get(route)
        if page is None:
            page = self._create_page(route)
            self._pages[route] = page
            self.stack.addWidget(page)
        self._pages.move_to_end(route)
        self.stack.setCurrentWidget(page)
        self._route = route
        self.sidebar.select(route)
        while len(self._pages) > self.MAX_PAGES + 1:
            _, old = self._pages.popitem(last=False)
            self.stack.removeWidget(old)
            old.deleteLater()

    def _create_page(self, route: Route) -> QWidget:
        s = self.s
        kind = route[0]
        if kind in ("liked", "playlist"):
            playlist_id = LIKED_SONGS_ID if kind == "liked" else route[1]
            title = "Liked Songs" if kind == "liked" else route[2]
            page = PlaylistPage(playlist_id, title, s.images, s.liked)
            page.play_requested.connect(self._play)
            known = s.library.open_playlist(playlist_id, title)
            if known is not None:
                page.set_playlist(known)
            return page
        if kind in ("albums", "artists"):
            albums = kind == "albums"
            collection = CollectionPage(
                "Albums" if albums else "Artists", s.images, round_covers=not albums
            )
            collection.set_items(s.library.albums if albums else s.library.artists)
            collection.activated.connect(
                lambda _item: self.toast.show_message(
                    "Album and artist pages arrive in milestone 4"
                )
            )
            return collection
        signed_out = SignedOutPage(s.images, s.liked)
        signed_out.sign_in_clicked.connect(self.show_sign_in)
        signed_out.play_requested.connect(self._play)
        return signed_out

    def _drop_pages(self) -> None:
        for page in self._pages.values():
            self.stack.removeWidget(page)
            page.deleteLater()
        self._pages.clear()
        self._route = None

    def _play(self, tracks: Sequence[Any], row: int) -> None:
        self.s.controller.play_tracks(list(tracks), row)

    def _on_playlist(self, playlist: Playlist) -> None:
        for page in self._pages.values():
            if isinstance(page, PlaylistPage) and page.playlist_id == playlist.id:
                page.set_playlist(playlist)

    def _update_collection(
        self, route: Route, items: Sequence[AlbumSummary] | Sequence[ArtistSummary]
    ) -> None:
        page = self._pages.get(route)
        if isinstance(page, CollectionPage):
            page.set_items(items)

    # -- session -----------------------------------------------------------------------

    def _on_session_changed(self) -> None:
        session = self.s.session
        if session.signed_in:
            self.banner.hide()
            self.sidebar.set_account(session.account_name or "Signed in")
        else:
            self.sidebar.set_account(None)
        self._drop_pages()
        self.navigate(("liked",) if session.signed_in else ("signed_out",))

    def _on_account(self, account: Account | None) -> None:
        if account is not None:
            self.sidebar.set_account(account.name)

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

    def _overlay_info(self) -> dict[str, Any]:
        s = self.s
        player_stats = s.controller.player.stats()
        worker = s.resolver.worker_info
        runtime = worker.get("js_runtime") or {}
        count, size = s.images.memory_usage()
        info: dict[str, Any] = {
            "session": s.session.credentials.method if s.session.credentials else "signed out",
            "playback": s.controller.state.value,
            "libmpv": "loaded" if player_stats.get("active") else "not loaded",
            "resolver": "running" if s.resolver.is_running() else "stopped",
            "js runtime": f"{runtime.get('name', '–')} {runtime.get('version', '')}".strip(),
            "yt-dlp": (worker.get("versions") or {}).get("yt-dlp", "–"),
            "covers": f"{count} in memory, {size / 1e6:.1f} MB",
            "pages": len(self._pages),
            "last gap": "–" if self._last_gap is None else f"{self._last_gap:.0f} ms",
        }
        if player_stats.get("active"):
            cache = player_stats.get("cache_bytes")
            info["demux cache"] = "–" if cache is None else f"{cache / 1e6:.1f} MB"
            info["audio"] = f"{player_stats.get('codec')} @ {player_stats.get('samplerate')} Hz"
        return info
