"""Application entry point: ``python -m ytm_lite`` (or the ``ytm-lite`` script)."""

from __future__ import annotations

import argparse
import logging
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
        "--measure-ready",
        metavar="FILE",
        help="(testing) write the wall-clock time of the first paint to FILE",
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

    from ytm_lite.app.play_queue import PlayQueue
    from ytm_lite.app.playback import PlaybackController
    from ytm_lite.app.resolver import ResolverClient
    from ytm_lite.ui.main_window import TEST_TRACKS, MainWindow
    from ytm_lite.ui.theme import apply_theme

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    apply_theme(app)

    settings = Settings.load(paths.settings_file)
    resolver = ResolverClient(paths.cache)
    resolver.configure(
        cookies=None, js_runtime=settings.js_runtime, player_clients=settings.player_clients
    )
    player = Player()
    controller = PlaybackController(PlayQueue(), resolver, player)
    window = MainWindow(controller, resolver)
    install_excepthooks(window.report_error)

    if args.start_minimized:
        window.showMinimized()
    else:
        window.show()

    def first_paint() -> None:
        log.info("READY after %.0f ms", (time.perf_counter() - _T0) * 1000)
        if args.measure_ready:
            Path(args.measure_ready).write_text(f"{time.time():.4f}", encoding="utf-8")

    QTimer.singleShot(0, first_paint)
    if args.autoplay:
        QTimer.singleShot(0, lambda: controller.play_tracks(TEST_TRACKS))

    exit_code = app.exec()
    controller.shutdown()
    resolver.shutdown()
    log.info("exiting (%s)", exit_code)
    return exit_code
