"""Spike S1: resolver worker timing and memory, per JS runtime.

Spawns the worker exactly like the app does, resolves a few tracks cold and warm,
and samples the worker's process tree (including the short-lived JS runtime).

Usage: uv run python tools/spikes/s1_resolve.py --runtime deno [--runtime node]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[2]
TRACKS = {
    "4D7u5KF7SP8": "Daft Punk - Get Lucky",
    "BSTsnWoslP4": "Queen - Bohemian Rhapsody",
    "OF4rw3LOfqY": "Pink Floyd - Echoes (23 min)",
}


class TreeSampler(threading.Thread):
    """Samples total working set of a process and its children every 25 ms."""

    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True)
        self.root = psutil.Process(pid)
        self.peak = 0
        self.last = 0
        self.stop_flag = threading.Event()

    def run(self) -> None:
        while not self.stop_flag.is_set():
            total = 0
            try:
                procs = [self.root, *self.root.children(recursive=True)]
            except psutil.Error:
                break
            for proc in procs:
                with contextlib.suppress(psutil.Error):
                    total += proc.memory_info().rss
            self.last = total
            self.peak = max(self.peak, total)
            time.sleep(0.025)


def read_message(proc: subprocess.Popen[bytes]) -> dict[str, Any]:
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("worker exited")
    message: dict[str, Any] = json.loads(line)
    return message


def send(proc: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()


def run(runtime: str, rounds: int) -> None:
    cache = ROOT / ".measure" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    stderr_log = ROOT / ".measure" / f"s1-{runtime}.stderr.log"
    started = time.perf_counter()
    with stderr_log.open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-m", "ytm_resolver"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=err,
        )
        sampler = TreeSampler(proc.pid)
        sampler.start()
        send(proc, {"op": "configure", "cachedir": str(cache), "js_runtime": runtime})
        ready = read_message(proc)
        print(f"\n== runtime={runtime}: ready in {time.perf_counter() - started:.2f}s")
        print(f"   {json.dumps(ready.get('js_runtime'))}  {json.dumps(ready.get('versions'))}")
        worker = psutil.Process(proc.pid)
        idle_rss = sampler.last
        for round_no in range(1, rounds + 1):
            for video_id, name in TRACKS.items():
                sampler.peak = sampler.last
                cpu_before = sum(worker.cpu_times()[:2])
                t0 = time.perf_counter()
                send(proc, {"op": "resolve", "id": 1, "video_id": video_id, "priority": 0})
                reply = read_message(proc)
                wall = time.perf_counter() - t0
                cpu = sum(worker.cpu_times()[:2]) - cpu_before
                if reply["op"] == "result":
                    s = reply["stream"]
                    avail = s["available_at"]
                    wait = f"{avail - time.time():+.1f}s" if avail else "none"
                    expires_h = (s["expires_at"] - time.time()) / 3600
                    detail = (
                        f"fmt {s['format_id']} {s['codec']} {s['abr'] or 0:.0f}k "
                        f"premium={s['premium']} expires_in={expires_h:.1f}h "
                        f"available_at={wait} headers={sorted(s['http_headers'])}"
                    )
                else:
                    detail = f"ERROR {reply['kind']}: {reply['message'][:120]}"
                print(
                    f"   round {round_no} {name:30s} wall {wall:5.2f}s  worker-cpu {cpu:4.2f}s  "
                    f"peak-tree {sampler.peak / 1e6:6.1f} MB  {detail}"
                )
        time.sleep(0.5)
        print(
            f"   worker RSS: idle-after-ready {idle_rss / 1e6:.1f} MB, "
            f"after resolves {sampler.last / 1e6:.1f} MB"
        )
        send(proc, {"op": "shutdown"})
        proc.wait(timeout=10)
        sampler.stop_flag.set()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", action="append", default=[])
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    for runtime in args.runtime or ["auto"]:
        run(runtime, args.rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
