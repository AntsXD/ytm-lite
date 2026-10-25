"""Process-tree memory and thread counts, for the debug overlay and measurement tools."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessStats:
    pid: int
    name: str
    rss: int  # working set on Windows
    private: int  # private bytes (Windows) / unique set size (elsewhere)
    threads: int


@dataclass(frozen=True, slots=True)
class TreeStats:
    processes: tuple[ProcessStats, ...]

    @property
    def rss(self) -> int:
        return sum(p.rss for p in self.processes)

    @property
    def private(self) -> int:
        return sum(p.private for p in self.processes)

    @property
    def threads(self) -> int:
        return sum(p.threads for p in self.processes)


def tree_stats(pid: int | None = None) -> TreeStats:
    """Stats for a process (default: this one) and all of its descendants."""
    import psutil  # imported lazily: only the overlay and tools need it

    root = psutil.Process(pid)
    rows: list[ProcessStats] = []
    for proc in (root, *root.children(recursive=True)):
        try:
            with proc.oneshot():
                mem = proc.memory_info()
                private = getattr(mem, "private", 0) or proc.memory_full_info().uss
                rows.append(
                    ProcessStats(proc.pid, proc.name(), mem.rss, private, proc.num_threads())
                )
        except psutil.Error:
            continue
    return TreeStats(tuple(rows))
