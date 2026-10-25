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
    parser.add_argument("--start-minimized", action="store_true")
    parser.add_argument(
        "--autoplay", action="store_true", help="(testing) play the built-in test tracks at startup"
    )
    parser.add_argument(
        "--open-playlist", metavar="ID", help="(testing) open this playlist's page at startup"
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
    from ytm_lite.app.images import ImageLoader
    from ytm_lite.app.library import Library
    from ytm_lite.app.library_cache import LibraryCache
    from ytm_lite.app.liked import LikedIndex
    from ytm_lite.app.play_queue import PlayQueue
    from ytm_lite.app.playback import PlaybackController
    from ytm_lite.app.resolver import ResolverClient
    from ytm_lite.app.secrets import SecretStore
    from ytm_lite.app.services import Services
    from ytm_lite.app.session import Session
    from ytm_lite.app.worker_client import WorkerProcess
    from ytm_lite.app.workers import Workers
    from ytm_lite.ui.main_window import MainWindow
    from ytm_lite.ui.theme import apply_theme

    qt_args = sys.argv[:1]
    if sys.platform == "win32" and "QT_QPA_PLATFORM" not in os.environ:
        # Qt's default DirectWrite engine maps a dozen large CJK/emoji fonts the first time
        # a non-Latin title is drawn (~50 MB working set, ~20 MB private). GDI font
        # linking draws the same glyphs (emoji in monochrome) for almost nothing.
        qt_args += ["-platform", "windows:fontengine=gdi"]
    app = QApplication(qt_args)
    app.setApplicationName(APP_NAME)
    apply_theme(app)

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
    services = Services(
        paths, settings, workers, api, resolver, session, library, liked, images, controller
    )
    window = MainWindow(services)
    install_excepthooks(window.report_error)

    if args.start_minimized:
        window.showMinimized()
    else:
        window.show()

    def first_paint() -> None:
        log.info("READY after %.0f ms", (time.perf_counter() - _T0) * 1000)
        if args.measure_ready:
            Path(args.measure_ready).write_text(f"{time.time():.4f}", encoding="utf-8")
        library.load_cached()
        # A Firefox session is re-read first: Firefox may have rotated the cookies.
        session.refresh_from_firefox(lambda refreshed: None if refreshed else library.refresh())
        if args.open_playlist:
            window.navigate(("playlist", args.open_playlist, "Playlist"))
        elif not args.autoplay:
            window.maybe_prompt_sign_in()
        if args.autoplay:
            controller.play_tracks(TEST_TRACKS)

    QTimer.singleShot(0, first_paint)
    if args.memory_report:
        from ytm_lite.app import memreport

        QTimer.singleShot(int(args.memory_report * 1000), memreport.report)
    exit_code = app.exec()
    controller.shutdown()
    worker.shutdown()
    workers.shutdown()
    log.info("exiting (%s)", exit_code)
    return exit_code
