"""Application entry point: ``python -m ytm_lite`` (or the ``ytm-lite`` script)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from ytm_lite import APP_NAME
from ytm_lite.app.config import Settings, app_paths, app_root
from ytm_lite.app.log import install_excepthooks, setup_logging

_T0 = time.perf_counter()
log = logging.getLogger("ytm_lite")

LIBMPV_DLL = "libmpv-2.dll"


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument(
        "--start-minimized", action="store_true", help="start in the tray, without a window"
    )
    commands = parser.add_mutually_exclusive_group()
    for flag, command, text in (
        ("--show", "show", "show the running app's window"),
        ("--play-pause", "play-pause", "play or pause in the running app"),
        ("--next", "next", "skip to the next song in the running app"),
        ("--previous", "previous", "go back a song in the running app"),
    ):
        commands.add_argument(flag, dest="command", action="store_const", const=command, help=text)
    commands.add_argument("--prev", dest="command", action="store_const", const="previous")
    parser.add_argument(
        "--autoplay", action="store_true", help="(testing) play the built-in test tracks at startup"
    )
    parser.add_argument(
        "--open-playlist", metavar="ID", help="(testing) open this playlist's page at startup"
    )
    parser.add_argument(
        "--autoplay-playlist",
        metavar="ID",
        help="(testing) open this playlist and play all of it once it has loaded",
    )
    parser.add_argument("--show-queue", action="store_true", help="(testing) open the queue panel")
    parser.add_argument(
        "--tray-after",
        metavar="SECONDS",
        type=float,
        help="(testing) close the window to the tray after SECONDS",
    )
    parser.add_argument(
        "--measure-ready",
        metavar="FILE",
        help="(testing) write the wall-clock time of the first paint to FILE",
    )
    parser.add_argument(
        "--memory-report",
        metavar="SECONDS",
        type=float,
        help="(testing) log where memory goes after SECONDS (slows the app down)",
    )
    return parser.parse_args(argv)


def _libmpv_dir() -> Path | None:
    root = app_root()
    for candidate in (root / "lib", root / "vendor"):
        if (candidate / LIBMPV_DLL).exists():
            return candidate
    return None


def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.memory_report:
        from ytm_lite.app import memreport

        memreport.start()
    paths = app_paths()
    setup_logging(paths.logs, debug=args.debug)

    from ytm_lite.app.player import Player, add_libmpv_dir

    if sys.platform == "win32":
        libmpv = _libmpv_dir()
        if libmpv is not None:
            add_libmpv_dir(libmpv)
        else:
            log.error("%s not found; run tools/fetch_libmpv.py", LIBMPV_DLL)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ytm_lite.app.api import Api
    from ytm_lite.app.dev import TEST_TRACKS
    from ytm_lite.app.history import HistoryReporter
    from ytm_lite.app.images import ImageLoader
    from ytm_lite.app.library import Library
    from ytm_lite.app.library_cache import LibraryCache
    from ytm_lite.app.liked import LikedIndex
    from ytm_lite.app.models import Playlist
    from ytm_lite.app.play_queue import PlayQueue
    from ytm_lite.app.playback import PlaybackController
    from ytm_lite.app.resolver import ResolverClient
    from ytm_lite.app.secrets import SecretStore
    from ytm_lite.app.services import Services
    from ytm_lite.app.session import Session
    from ytm_lite.app.single_instance import SingleInstance, server_name
    from ytm_lite.app.state import SavedQueue, StateKeeper
    from ytm_lite.app.worker_client import WorkerProcess
    from ytm_lite.app.workers import Priority as WorkPriority
    from ytm_lite.app.workers import Workers
    from ytm_lite.integrations.media_controls import MediaBridge, create_media_controls
    from ytm_lite.ui.shell import Shell, apply_window_icon
    from ytm_lite.ui.theme import apply_theme

    qt_args = sys.argv[:1]
    if sys.platform == "win32" and "QT_QPA_PLATFORM" not in os.environ:
        # Qt's default DirectWrite engine maps a dozen large CJK/emoji fonts the first time
        # a non-Latin title is drawn (~50 MB working set, ~20 MB private). GDI font
        # linking draws the same glyphs (emoji in monochrome) for almost nothing.
        qt_args += ["-platform", "windows:fontengine=gdi"]
    app = QApplication(qt_args)
    app.setApplicationName(APP_NAME)

    # One copy per profile: hand this launch's command to a running one and stop here.
    instance = SingleInstance(server_name(paths.config))
    if instance.forward(args.command or "show"):
        log.info("handed %r to the running copy", args.command or "show")
        return 0
    instance.listen()

    apply_theme(app)
    apply_window_icon(app)

    settings = Settings.load(paths.settings_file)
    workers = Workers()
    worker = WorkerProcess(paths.cache)
    api = Api(worker)
    resolver = ResolverClient(worker)
    session = Session(SecretStore(paths.config / "auth.bin"), api, resolver, workers, settings)
    session.load()
    liked = LikedIndex(api, workers)
    library = Library(api, workers, LibraryCache(paths.cache / "library"), liked, session)
    images = ImageLoader(paths.cache)
    player = Player()
    controller = PlaybackController(PlayQueue(), resolver, player)
    controller.radio = settings.radio_autoplay
    keeper = StateKeeper(paths, controller, workers)
    saved = keeper.load_state()
    if saved.volume is not None:
        player.set_volume(saved.volume)
    services = Services(
        paths, settings, workers, api, resolver, session, library, liked, images, controller
    )
    shell = Shell(services, keeper, saved.window)
    instance.command_received.connect(shell.handle_command)
    install_excepthooks(shell.report_error)
    media = create_media_controls(app)
    media_bridge = MediaBridge(media, controller, images, app)
    media.raise_window.connect(shell.show_window)
    log.info("media controls: %s", media.name)

    def fetch_radio(seed: str) -> None:
        def failed(error: Exception) -> None:
            log.warning("radio suggestions failed: %s", error)
            controller.radio_failed(seed)

        workers.submit(
            lambda: api.radio(seed),
            on_done=lambda tracks: controller.add_radio(seed, tracks),
            on_error=failed,
            priority=WorkPriority.BACKGROUND,
        )

    controller.radio_wanted.connect(fetch_radio)

    def play_when_loaded(playlist: Playlist) -> None:
        if playlist.id == args.autoplay_playlist and playlist.complete:
            library.playlist_updated.disconnect(play_when_loaded)
            controller.play_tracks([t for t in playlist.tracks if t.available])

    if args.autoplay_playlist:
        args.open_playlist = args.autoplay_playlist
        library.playlist_updated.connect(play_when_loaded)
    testing_playback = args.autoplay or args.autoplay_playlist
    # Test and measurement runs (e.g. measure.py --real-profile) never save the session
    # or add plays to the account's history.
    testing_run = bool(testing_playback or args.measure_ready)
    keeper.read_only = testing_run

    def report_play(video_id: str) -> None:
        if testing_run or not settings.report_history or not api.signed_in:
            return
        workers.submit(
            lambda: api.add_history(video_id),
            on_done=lambda _: log.info("added %s to the account's history", video_id),
            on_error=lambda error: log.warning("history for %s failed: %s", video_id, error),
            priority=WorkPriority.BACKGROUND,
        )

    history = HistoryReporter(controller, app)
    history.played.connect(report_play)

    def restore_queue(queue: SavedQueue | None) -> None:
        if controller.queue.current() is not None:
            return  # something was started already
        restored = keeper.restore(saved, queue)
        window = shell.window
        if restored and window is not None and window.isVisible() and not window.isMinimized():
            controller.preload()

    if args.start_minimized:
        shell.start_in_tray()
    else:
        window = shell.show_window()
        if args.show_queue:
            window.set_queue_visible(True)

    def first_paint() -> None:
        log.info("READY after %.0f ms", (time.perf_counter() - _T0) * 1000)
        if args.measure_ready:
            Path(args.measure_ready).write_text(f"{time.time():.4f}", encoding="utf-8")
        library.load_cached()
        if not testing_playback:
            workers.submit(keeper.load_queue, on_done=restore_queue, pool="io")
        # A Firefox session is re-read first: Firefox may have rotated the cookies.
        session.refresh_from_firefox(lambda refreshed: None if refreshed else library.refresh())
        window = shell.window
        if window is not None and args.open_playlist:
            window.navigate(("playlist", args.open_playlist, "Playlist"))
        elif window is not None and not testing_playback:
            window.maybe_prompt_sign_in()
        if args.autoplay:
            controller.play_tracks(TEST_TRACKS)

    QTimer.singleShot(0, first_paint)
    if args.tray_after is not None:
        QTimer.singleShot(int(args.tray_after * 1000), shell.hide_to_tray)
    if args.memory_report:
        from ytm_lite.app import memreport

        QTimer.singleShot(int(args.memory_report * 1000), memreport.report)
    exit_code = app.exec()
    del media_bridge
    media.close()
    final = keeper.stop()  # before mpv goes away, so the position is known
    controller.shutdown()
    worker.shutdown()
    workers.shutdown()  # waits for an io-thread save already running
    keeper.save_now(final)
    log.info("exiting (%s)", exit_code)
    return exit_code
