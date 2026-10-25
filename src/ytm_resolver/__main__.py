"""Entry point: ``python -I -m ytm_resolver``. See ytm_resolver.protocol for the wire format."""

from __future__ import annotations

import logging
import sys

from ytm_resolver.redact import redact
from ytm_resolver.worker import Worker, lower_priority


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def main() -> int:
    handler = logging.StreamHandler(sys.stderr)
    # The app parses "LEVEL name: message" lines from stderr into its own log.
    handler.setFormatter(_RedactingFormatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    lower_priority()
    # Import yt-dlp once, before the resolver threads start: two threads importing its
    # submodules at the same time can trip Python's import deadlock detection.
    try:
        import yt_dlp  # noqa: F401
        from yt_dlp.extractor.youtube import YoutubeIE  # noqa: F401
    except Exception:
        logging.getLogger("ytm_resolver").exception("yt-dlp import failed")
    worker = Worker(sys.stdout.buffer)
    worker.serve(sys.stdin.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
