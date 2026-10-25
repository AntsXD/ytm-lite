"""Entry point: ``python -I -m ytm_worker``. See ytm_worker.protocol for the wire format."""

from __future__ import annotations

import logging
import sys

from ytm_worker.redact import redact
from ytm_worker.worker import Worker, lower_priority


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def main() -> int:
    handler = logging.StreamHandler(sys.stderr)
    # The app parses "LEVEL name: message" lines from stderr into its own log.
    handler.setFormatter(_RedactingFormatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    lower_priority()
    # yt-dlp and ytmusicapi are imported lazily, by the first request that needs them.
    worker = Worker(sys.stdout.buffer)
    worker.serve(sys.stdin.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
