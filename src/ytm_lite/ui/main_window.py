"""Main window. Milestone 1: a list of built-in test tracks plus the now-playing bar."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.models import AlbumRef, ArtistRef, Track, format_duration
from ytm_lite.app.playback import PlaybackController
from ytm_lite.app.resolver import ResolverClient
from ytm_lite.ui.debug_overlay import DebugOverlay
from ytm_lite.ui.notify import Toast
from ytm_lite.ui.now_playing_bar import NowPlayingBar

TEST_TRACKS = (
    Track(
        "4D7u5KF7SP8",
        "Get Lucky (feat. Pharrell Williams and Nile Rodgers)",
        (ArtistRef("Daft Punk"), ArtistRef("Pharrell Williams"), ArtistRef("Nile Rodgers")),
        AlbumRef("Random Access Memories"),
        370,
    ),
    Track(
        "BSTsnWoslP4",
        "Bohemian Rhapsody",
        (ArtistRef("Queen"),),
        AlbumRef("A Night At The Opera"),
        355,
    ),
    Track("NMRhx71bGo4", "Let It Happen", (ArtistRef("Tame Impala"),), AlbumRef("Currents"), 468),
    Track("OF4rw3LOfqY", "Echoes", (ArtistRef("Pink Floyd"),), AlbumRef("Meddle"), 1413),
)


class MainWindow(QMainWindow):
    error_reported = Signal(str)  # thread-safe way to show an error toast

    POLL_MS = 250

    def __init__(self, controller: PlaybackController, resolver: ResolverClient) -> None:
        super().__init__()
        self.controller = controller
        self.resolver = resolver
        self._last_gap: float | None = None
        self.setWindowTitle("ytm-lite")
        self.resize(1100, 700)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 20, 24, 12)
        heading = QLabel("Test tracks — double-click to play. The library arrives in milestone 2.")
        heading.setObjectName("Dim")
        self.track_list = QListWidget()
        for track in TEST_TRACKS:
            item = QListWidgetItem(
                f"{track.title}  —  {track.artist_text}  ({format_duration(track.duration)})"
            )
            self.track_list.addItem(item)
        self.track_list.itemDoubleClicked.connect(self._play_row)
        content_layout.addWidget(heading)
        content_layout.addWidget(self.track_list)

        self.bar = NowPlayingBar()
        self.bar.set_volume(controller.player.volume())
        layout.addWidget(content, 1)
        layout.addWidget(self.bar)
        self.setCentralWidget(central)

        self.overlay = DebugOverlay(self._overlay_info, central)
        self.overlay.move(12, 12)
        self.toast = Toast(central)

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
            lambda: self.toast.show_message("Stream extraction failed — try updating yt-dlp", 8000)
        )
        controller.transition_measured.connect(self._on_transition)
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

    def report_error(self, message: str) -> None:
        """Callable from any thread."""
        self.error_reported.emit(message)

    def _play_row(self, item: QListWidgetItem) -> None:
        self.controller.play_tracks(TEST_TRACKS, self.track_list.row(item))

    def _toggle_overlay(self) -> None:
        self.overlay.setVisible(not self.overlay.isVisible())
        self.overlay.raise_()

    def _on_transition(self, gap_ms: float) -> None:
        self._last_gap = gap_ms

    # -- position polling, only while the window is on screen ------------------------------

    def _update_polling(self) -> None:
        if self.isVisible() and not self.isMinimized():
            if not self._poll.isActive():
                self._tick()
                self._poll.start()
        else:
            self._poll.stop()

    def _tick(self) -> None:
        self.bar.set_position(self.controller.position(), self.controller.duration())

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
        player_stats = self.controller.player.stats()
        worker = self.resolver.worker_info or {}
        runtime = worker.get("js_runtime") or {}
        info: dict[str, Any] = {
            "playback": self.controller.state.value,
            "libmpv": "loaded" if player_stats.get("active") else "not loaded",
            "resolver": "running" if self.resolver.is_running() else "stopped",
            "js runtime": f"{runtime.get('name', '–')} {runtime.get('version', '')}".strip(),
            "yt-dlp": (worker.get("versions") or {}).get("yt-dlp", "–"),
            "last gap": "–" if self._last_gap is None else f"{self._last_gap:.0f} ms",
        }
        if player_stats.get("active"):
            cache = player_stats.get("cache_bytes")
            info["demux cache"] = "–" if cache is None else f"{cache / 1e6:.1f} MB"
            info["audio"] = f"{player_stats.get('codec')} @ {player_stats.get('samplerate')} Hz"
        return info
