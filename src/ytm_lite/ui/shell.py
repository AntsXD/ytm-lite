"""The app around the window: tray icon, the window's lifecycle, and commands from other
launches (``ytm-lite --next`` etc.).

Closing the window sends the app to the tray (unless that's turned off): the window is
destroyed, cached covers are dropped and the working set is trimmed, so a game gets
that RAM back. Playback, the queue and the library stay; showing the window again
rebuilds it in well under a second, on the page you left.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ytm_lite.app.play_queue import QueueEntry
from ytm_lite.app.playback import PlaybackState
from ytm_lite.app.services import Services
from ytm_lite.app.state import StateKeeper
from ytm_lite.integrations.windows_memory import trim_working_set
from ytm_lite.ui import icons
from ytm_lite.ui.main_window import MainWindow
from ytm_lite.ui.sidebar import Route

log = logging.getLogger(__name__)


class Shell(QObject):
    TRIM_DELAY_MS = 1500  # after the window's teardown has settled

    def __init__(
        self,
        services: Services,
        keeper: StateKeeper,
        window_state: dict[str, Any],
        parent: QObject | None = None,
        *,
        tray: bool = True,
    ) -> None:
        super().__init__(parent)
        self.s = services
        self._keeper = keeper
        self._window_state = dict(window_state)  # the window's layout, while it's gone
        self._route: Route | None = None
        self._told_about_tray = False
        self.window: MainWindow | None = None
        keeper.window_state = self._ui_state

        self.tray: QSystemTrayIcon | None = None
        if tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(icons.app_icon(), self)
            menu = QMenu()
            self._play_action = QAction("Play", menu)
            self._play_action.triggered.connect(services.controller.toggle_pause)
            menu.addAction(self._play_action)
            menu.addAction("Next").triggered.connect(services.controller.next)
            menu.addAction("Previous").triggered.connect(services.controller.previous)
            menu.addSeparator()
            self._window_action = QAction("Show window", menu)
            self._window_action.triggered.connect(self.toggle_window)
            menu.addAction(self._window_action)
            menu.addSeparator()
            menu.addAction("Quit").triggered.connect(QApplication.quit)
            self._menu = menu  # QSystemTrayIcon doesn't take ownership
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.setToolTip("ytm-lite")
            self.tray.show()
            services.controller.current_changed.connect(self._current_changed)
            services.controller.state_changed.connect(self._state_changed)

    # -- the window ---------------------------------------------------------------------------

    def show_window(self) -> MainWindow:
        window = self.window
        if window is None:
            window = self.window = MainWindow(self.s, self._route)
            window.restore_ui_state(self._window_state)
            window.closed.connect(self._window_closed)
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()
        self._update_window_action()
        return window

    def hide_to_tray(self) -> None:
        window = self.window
        if window is None:
            return
        self._window_state = window.ui_state()
        self._route = window.current_route()
        self.window = None
        window.hide()
        window.deleteLater()
        self.s.images.clear_memory()
        QTimer.singleShot(self.TRIM_DELAY_MS, trim_working_set)
        self._update_window_action()
        log.info("window closed; running in the tray")
        if self.tray is not None and not self._told_about_tray:
            self._told_about_tray = True
            self.tray.showMessage(
                "ytm-lite is in the tray",
                "Music keeps playing. Click the icon to bring the window back.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

    def toggle_window(self) -> None:
        if self.window is not None and self.window.isVisible() and not self.window.isMinimized():
            self.hide_to_tray()
        else:
            self.show_window()

    def start_in_tray(self) -> None:
        """Start with no window at all (e.g. at login), if there's a tray to live in."""
        if self.tray is None:
            self.show_window().showMinimized()
        else:
            QTimer.singleShot(self.TRIM_DELAY_MS, trim_working_set)
            self._update_window_action()
            log.info("started in the tray")

    def _window_closed(self) -> None:
        if self.s.settings.close_to_tray and self.tray is not None:
            self.hide_to_tray()
        else:
            QApplication.quit()

    def _ui_state(self) -> dict[str, Any]:
        if self.window is not None:
            self._window_state = self.window.ui_state()
        return dict(self._window_state)

    def report_error(self, message: str) -> None:
        """Callable from any thread."""
        window = self.window
        if window is not None:
            window.report_error(message)

    # -- commands from another launch ----------------------------------------------------------

    def handle_command(self, command: str) -> None:
        controller = self.s.controller
        if command == "show":
            self.show_window()
        elif command == "play-pause":
            controller.toggle_pause()
        elif command == "next":
            controller.next()
        elif command == "previous":
            controller.previous()

    # -- tray ------------------------------------------------------------------------------------

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_window()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.s.controller.toggle_pause()

    def _update_window_action(self) -> None:
        if self.tray is not None:
            shown = self.window is not None and self.window.isVisible()
            self._window_action.setText("Hide window" if shown else "Show window")

    def _current_changed(self, entry: QueueEntry | None) -> None:
        if self.tray is None:
            return
        if entry is None:
            self.tray.setToolTip("ytm-lite")
            return
        track = entry.track
        text = f"{track.title} — {track.artist_text}" if track.artist_text else track.title
        self.tray.setToolTip(text[:120])  # Windows cuts tray tooltips at 127 characters

    def _state_changed(self, state: str) -> None:
        playing = state in (PlaybackState.PLAYING.value, PlaybackState.LOADING.value)
        self._play_action.setText("Pause" if playing else "Play")


def apply_window_icon(app: QApplication) -> None:
    app.setWindowIcon(icons.app_icon())
    app.setQuitOnLastWindowClosed(False)  # the tray keeps the app alive; Quit ends it
