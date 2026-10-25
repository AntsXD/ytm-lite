"""Heavy imports (yt-dlp, ytmusicapi) happen lazily and one at a time.

Two threads importing packages that share submodules (both use requests) can trip
Python's import deadlock detection, so every heavy import takes this lock.
"""

from __future__ import annotations

import threading

IMPORT_LOCK = threading.Lock()
