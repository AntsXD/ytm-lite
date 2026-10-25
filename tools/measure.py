"""Measure ytm-lite against the spec's performance targets.

Each scenario launches the app in a throwaway profile (YTM_LITE_HOME under .measure/),
samples the whole process tree with psutil, and prints a table for the README.

Usage:
    uv run python tools/measure.py                # all scenarios
    uv run python tools/measure.py playing --seconds 30

Scenarios:
    startup   cold start to first paint, median of 5 runs
    idle      window open, nothing playing
    playing   test tracks playing, window minimized (RAM + CPU)
    window    test tracks playing, window open
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
MEASURE_DIR = ROOT / ".measure"
VENV_SCRIPTS = (ROOT / ".venv" / "Scripts").resolve()
LAST_BREAKDOWN: list[str] = []  # per-process lines from the last sample of a scenario


@dataclass
class Sample:
    rss: int
    private: int
    threads: int
    processes: int
    cpu: float  # cumulative CPU seconds of the tree
    breakdown: list[str] = field(default_factory=list)


@dataclass
class Result:
    scenario: str
    values: dict[str, str] = field(default_factory=dict)


def is_venv_launcher(proc: psutil.Process) -> bool:
    """The venv's python.exe is a small redirector that starts the real interpreter."""
    try:
        return Path(proc.exe()).resolve().parent == VENV_SCRIPTS and proc.name().lower().startswith(
            "python"
        )
    except psutil.Error:
        return False


def sample(root: psutil.Process) -> Sample:
    rss = private = threads = count = 0
    cpu = 0.0
    breakdown: list[str] = []
    try:
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        procs = []
    for proc in procs:
        if is_venv_launcher(proc):
            continue  # not present in a packaged build
        try:
            with proc.oneshot():
                mem = proc.memory_info()
                rss += mem.rss
                private += getattr(mem, "private", 0)
                threads += proc.num_threads()
                times = proc.cpu_times()
                cpu += times.user + times.system
                count += 1
                cmd = " ".join(proc.cmdline()[1:4])
                breakdown.append(
                    f"{proc.name()} [{cmd}] RSS {mb(mem.rss)}, "
                    f"private {mb(getattr(mem, 'private', 0))}, threads {proc.num_threads()}"
                )
        except psutil.Error:
            continue
    return Sample(rss, private, threads, count, cpu, breakdown)


def launch(scenario: str, *extra: str) -> tuple[subprocess.Popen[bytes], Path, float]:
    home = MEASURE_DIR / f"home-{scenario}"
    shutil.rmtree(home / "logs", ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    ready_file = home / "ready.txt"
    ready_file.unlink(missing_ok=True)
    env = {**os.environ, "YTM_LITE_HOME": str(home)}
    started = time.time()
    proc = subprocess.Popen(
        [sys.executable, "-m", "ytm_lite", "--measure-ready", str(ready_file), *extra],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, ready_file, started


def wait_ready(ready_file: Path, timeout: float = 30) -> float:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ready_file.exists() and ready_file.read_text().strip():
            return float(ready_file.read_text())
        time.sleep(0.02)
    raise TimeoutError("app did not become ready")


def stop(proc: subprocess.Popen[bytes]) -> None:
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []
    proc.terminate()
    for child in children:
        with contextlib.suppress(psutil.Error):
            child.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def log_lines(scenario: str, *needles: str) -> list[str]:
    log = MEASURE_DIR / f"home-{scenario}" / "logs" / "ytm-lite.log"
    if not log.exists():
        return []
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line for line in lines if any(n in line for n in needles)]


def wait_for_log(scenario: str, needle: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_lines(scenario, needle):
            return True
        time.sleep(0.25)
    return False


def mb(value: float) -> str:
    return f"{value / 1e6:.1f} MB"


def run_startup(runs: int = 5) -> Result:
    times = []
    for _ in range(runs):
        proc, ready_file, started = launch("startup")
        try:
            times.append(wait_ready(ready_file) - started)
        finally:
            stop(proc)
        time.sleep(0.5)
    return Result(
        "startup",
        {
            "cold start (median)": f"{statistics.median(times):.2f} s",
            "runs": ", ".join(f"{t:.2f}" for t in times),
        },
    )


def run_window_idle(seconds: float) -> Result:
    proc, ready_file, _ = launch("idle")
    try:
        wait_ready(ready_file)
        time.sleep(5)
        root = psutil.Process(proc.pid)
        samples = []
        end = time.time() + seconds
        while time.time() < end:
            samples.append(sample(root))
            time.sleep(1)
    finally:
        stop(proc)
    LAST_BREAKDOWN.extend(samples[-1].breakdown)
    return Result(
        "idle, window open",
        {
            "RSS (median)": mb(statistics.median(s.rss for s in samples)),
            "private (median)": mb(statistics.median(s.private for s in samples)),
            "threads": str(samples[-1].threads),
        },
    )


def run_playing(seconds: float, minimized: bool) -> Result:
    scenario = "playing" if minimized else "window"
    extra = ["--autoplay"] + (["--start-minimized"] if minimized else [])
    proc, ready_file, _ = launch(scenario, *extra)
    try:
        wait_ready(ready_file)
        if not wait_for_log(scenario, "armed next track", 60):
            print("   (warning: next track never armed)")
        time.sleep(3)
        root = psutil.Process(proc.pid)
        samples = []
        first = sample(root)
        t0 = time.time()
        end = t0 + seconds
        while time.time() < end:
            samples.append(sample(root))
            time.sleep(1)
        last = sample(root)
        wall = time.time() - t0
    finally:
        stop(proc)
    LAST_BREAKDOWN.extend(samples[-1].breakdown)
    cpu_core = (last.cpu - first.cpu) / wall * 100
    cpus = psutil.cpu_count() or 1
    label = "playing, minimized" if minimized else "playing, window open"
    return Result(
        label,
        {
            "RSS (median)": mb(statistics.median(s.rss for s in samples)),
            "RSS (max)": mb(max(s.rss for s in samples)),
            "private (median)": mb(statistics.median(s.private for s in samples)),
            "processes (max)": str(max(s.processes for s in samples)),
            "threads": str(samples[-1].threads),
            "CPU": f"{cpu_core:.2f}% of one core = {cpu_core / cpus:.3f}% of {cpus} logical CPUs",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenarios", nargs="*", default=["startup", "idle", "playing", "window"])
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    MEASURE_DIR.mkdir(exist_ok=True)

    results = []
    for scenario in args.scenarios:
        print(f"== {scenario}")
        if scenario == "startup":
            result = run_startup()
        elif scenario == "idle":
            result = run_window_idle(min(args.seconds, 20))
        elif scenario == "playing":
            result = run_playing(args.seconds, minimized=True)
        elif scenario == "window":
            result = run_playing(min(args.seconds, 30), minimized=False)
        else:
            parser.error(f"unknown scenario {scenario}")
        for key, value in result.values.items():
            print(f"   {key:<20} {value}")
        for line in LAST_BREAKDOWN:
            print(f"     · {line}")
        LAST_BREAKDOWN.clear()
        results.append(result)
        if scenario in ("playing", "window"):
            for line in log_lines(scenario, "transition", "resolved", "WARNING", "ERROR")[-8:]:
                print("   log:", line[:160])

    out = MEASURE_DIR / "results.json"
    out.write_text(json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
