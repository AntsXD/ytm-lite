"""Logging: rotating file in the app's log folder, every line passed through redaction."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from ytm_resolver.redact import redact

LOG_FILE = "ytm-lite.log"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

log = logging.getLogger("ytm_lite")


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(log_dir: Path, *, debug: bool = False) -> None:
    formatter = RedactingFormatter(_FORMAT)
    file_handler = RotatingFileHandler(
        log_dir / LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [file_handler]
    # A windowed (frozen) build has no stderr.
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        handlers.append(stream)
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO, handlers=handlers, force=True
    )


def install_excepthooks(report: Callable[[str], None] | None = None) -> None:
    """Log uncaught exceptions instead of crashing; `report` shows a short message to the user.

    `report` may be called from any thread.
    """

    def _report(exc: BaseException) -> None:
        if report is not None:
            try:
                report(f"Unexpected error: {type(exc).__name__}: {exc}")
            except Exception:
                log.debug("error reporter failed", exc_info=True)

    def on_exception(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        _report(exc)

    def on_thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None:
            return
        log.critical(
            "Unhandled exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        _report(args.exc_value)

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception
