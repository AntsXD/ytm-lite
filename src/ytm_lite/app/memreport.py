"""Developer memory report (--memory-report SECONDS): where this process's memory goes.

Logs, after a delay:
- the Python heap traced by tracemalloc (started at launch) and its top allocation sites,
- the mapped files (DLLs) with the largest share of the working set,
- the working set and private bytes of the whole process.
"""

from __future__ import annotations

import logging
import tracemalloc

log = logging.getLogger(__name__)


def start() -> None:
    tracemalloc.start(1)


def report() -> None:
    import psutil

    current, peak = tracemalloc.get_traced_memory()
    log.info("memory: python heap %.1f MB now, %.1f MB peak", current / 1e6, peak / 1e6)
    snapshot = tracemalloc.take_snapshot()
    for stat in snapshot.statistics("filename")[:12]:
        frame = stat.traceback[0]
        log.info("memory:   %7.2f MB  %s", stat.size / 1e6, frame.filename)
    proc = psutil.Process()
    mem = proc.memory_info()
    log.info(
        "memory: working set %.1f MB, private %.1f MB",
        mem.rss / 1e6,
        getattr(mem, "private", 0) / 1e6,
    )
    maps = sorted(proc.memory_maps(grouped=True), key=lambda m: m.rss, reverse=True)
    mapped = sum(m.rss for m in maps)
    log.info("memory: %.1f MB of the working set is mapped files:", mapped / 1e6)
    for entry in maps[:20]:
        log.info("memory:   %7.2f MB  %s", entry.rss / 1e6, entry.path)
