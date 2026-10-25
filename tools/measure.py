"""Measure ytm-lite against the spec's performance targets.

Each scenario launches the app in a throwaway profile (YTM_LITE_HOME under .measure/),
samples the whole process tree with psutil, and prints a table for the README.

Usage:
    uv run python tools/measure.py                # all scenarios
    uv run python tools/measure.py playing --seconds 30

Scenarios:
    startup     cold start to first paint, median of 5 runs
    tray        started in the tray (no window), nothing playing (RAM + CPU)
    tray-after  a big playlist shown in the window, then the window closed to the tray
    idle        window open on Home, nothing playing
    playing     test tracks playing, in the tray (RAM + CPU)
    window      test tracks playing, window open, a big playlist on screen
    queue       that big playlist playing as the queue, queue panel open
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
REAL_PROFILE = False  # --real-profile: measure your signed-in profile instead of a throwaway one
EXE: Path | None = None  # --exe: measure a packaged build instead of the source
LOG_OFFSET: dict[str, int] = {}
# Rolling Stone's 500 Greatest Songs (public): stands in for "a 200-track playlist visible".
BIG_PLAYLIST = "PLciALUPf8sIJlvH350O8PWh4UbSbOffSJ"
TRAY_AFTER = 30  # seconds the window stays open in the tray-after scenario


@dataclass
class Sample:
    rss: int
    private: int
    threads: int
    processes: int
    cpu: dict[int, float]  # cumulative CPU seconds per pid
    breakdown: list[str] = field(default_factory=list)


def cpu_used(samples: list[Sample]) -> float:
    """CPU seconds used over the samples, counting processes that started or exited
    in between (a plain sum of the tree would drop an exited worker's time)."""
    lowest: dict[int, float] = {}
    highest: dict[int, float] = {}
    for s in samples:
        for pid, seconds in s.cpu.items():
            lowest[pid] = min(lowest.get(pid, seconds), seconds)
            highest[pid] = max(highest.get(pid, seconds), seconds)
    return sum(highest[pid] - lowest[pid] for pid in highest)


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
    cpu: dict[int, float] = {}
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
                cpu[proc.pid] = times.user + times.system
                count += 1
                cmd = " ".join(proc.cmdline()[1:4])
                breakdown.append(
                    f"{proc.name()} [{cmd}] RSS {mb(mem.rss)}, "
                    f"private {mb(getattr(mem, 'private', 0))}, threads {proc.num_threads()}"
                )
        except psutil.Error:
            continue
    return Sample(rss, private, threads, count, cpu, breakdown)


def clean_path() -> str:
    """PATH without folders that hold an OpenSSL Qt could pick up (Git's mingw64\bin): a
    typical user's machine has none, and Qt's OpenSSL backend would load it (~6 MB)."""
    folders = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(f for f in folders if not (Path(f) / "libcrypto-3-x64.dll").exists())


def launch(scenario: str, *extra: str) -> tuple[subprocess.Popen[bytes], Path, float]:
    env = dict(os.environ, PATH=clean_path())
    if REAL_PROFILE:
        # Your own profile: signed in, real library. Only lines logged from now on count.
        log = real_log()
        LOG_OFFSET[scenario] = log.stat().st_size if log.exists() else 0
        ready_file = MEASURE_DIR / "ready-real.txt"
    else:
        home = MEASURE_DIR / f"home-{scenario}"
        shutil.rmtree(home / "logs", ignore_errors=True)
        (home / "config").mkdir(parents=True, exist_ok=True)
        # Throwaway profiles stay signed out and skip the first-run sign-in dialog.
        (home / "config" / "settings.json").write_text('{"skip_sign_in": true}', encoding="utf-8")
        ready_file = home / "ready.txt"
        env["YTM_LITE_HOME"] = str(home)
    ready_file.unlink(missing_ok=True)
    started = time.time()
    proc = subprocess.Popen(
        [*app_command(), "--measure-ready", str(ready_file), *extra],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, ready_file, started


def app_command() -> list[str]:
    return [str(EXE)] if EXE is not None else [sys.executable, "-m", "ytm_lite"]


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


def real_log() -> Path:
    from platformdirs import user_log_dir

    return Path(user_log_dir("ytm-lite", appauthor=False)) / "ytm-lite.log"


def log_lines(scenario: str, *needles: str) -> list[str]:
    throwaway_log = MEASURE_DIR / f"home-{scenario}" / "logs" / "ytm-lite.log"
    log = real_log() if REAL_PROFILE else throwaway_log
    if not log.exists():
        return []
    with log.open("rb") as fh:
        fh.seek(LOG_OFFSET.get(scenario, 0) if REAL_PROFILE else 0)
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
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


def wait_for_worker_exit(root: psutil.Process, settle: float = 5, limit: float = 60) -> None:
    """Until only the app's own process is left (the worker exits 15 s after its last
    call: Home, a library refresh, a playlist), and at least `settle` seconds."""
    t0 = time.time()
    while time.time() - t0 < limit:
        if time.time() - t0 >= settle and sample(root).processes == 1:
            return
        time.sleep(1)


def run_window_idle(seconds: float) -> Result:
    proc, ready_file, _ = launch("idle")
    try:
        wait_ready(ready_file)
        root = psutil.Process(proc.pid)
        wait_for_worker_exit(root)  # Home loads at startup
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


def run_tray(seconds: float, playlist: str | None = None) -> Result:
    """Idle in the tray: started there, or (with a playlist) after that playlist was shown
    in the window and the window was closed to the tray."""
    scenario = "tray-after" if playlist else "tray"
    if playlist:
        extra = ["--open-playlist", playlist, "--tray-after", str(TRAY_AFTER)]
    else:
        extra = ["--start-minimized"]
    proc, ready_file, _ = launch(scenario, *extra)
    try:
        wait_ready(ready_file)
        if playlist and not wait_for_log(scenario, "playlist loaded", TRAY_AFTER):
            print("   (warning: the playlist didn't load before the window closed)")
        if not wait_for_log(scenario, "in the tray", TRAY_AFTER + 30):
            print("   (warning: never reached the tray)")
        root = psutil.Process(proc.pid)
        # Steady once the working set has been trimmed (1.5 s in) and the worker is gone.
        wait_for_worker_exit(root)
        samples = [sample(root)]
        steady_from = time.time()
        while time.time() < steady_from + seconds:
            time.sleep(1)
            samples.append(sample(root))
        wall = time.time() - steady_from
    finally:
        stop(proc)
    LAST_BREAKDOWN.extend(samples[-1].breakdown)
    cpu_core = cpu_used(samples) / wall * 100
    cpus = psutil.cpu_count() or 1
    return Result(
        "idle in the tray, after showing a big playlist" if playlist else "idle in the tray",
        {
            "RSS (median)": mb(statistics.median(s.rss for s in samples)),
            "RSS (max)": mb(max(s.rss for s in samples)),
            "private (median)": mb(statistics.median(s.private for s in samples)),
            "threads": str(samples[-1].threads),
            "CPU": f"{cpu_core:.2f}% of one core = {cpu_core / cpus:.3f}% of {cpus} logical CPUs",
        },
    )


def run_playing(
    seconds: float, minimized: bool, playlist: str | None = None, as_queue: bool = False
) -> Result:
    scenario = "queue" if as_queue else "playing" if minimized else "window"
    if as_queue and playlist:
        extra = ["--autoplay-playlist", playlist, "--show-queue"]
    else:
        extra = ["--autoplay"] + (["--start-minimized"] if minimized else [])
        if playlist:
            extra += ["--open-playlist", playlist]
    proc, ready_file, _ = launch(scenario, *extra)
    try:
        wait_ready(ready_file)
        if playlist and not wait_for_log(scenario, "playlist loaded", 90):
            print("   (warning: playlist never finished loading)")
        if not wait_for_log(scenario, "armed next track", 60):
            print("   (warning: next track never armed)")
        root = psutil.Process(proc.pid)
        samples = []
        t0 = time.time()
        # Sample through the worker's idle exit (15 s after its last request), then
        # `seconds` more of steady playback.
        exited = False
        while time.time() < t0 + 60 and not exited:
            samples.append(sample(root))
            exited = bool(log_lines(scenario, "worker exited"))
            time.sleep(1)
        steady_from = len(samples)
        end = time.time() + seconds
        while time.time() < end:
            samples.append(sample(root))
            time.sleep(1)
        wall = time.time() - t0
    finally:
        stop(proc)
    steady = samples[steady_from:] or samples
    LAST_BREAKDOWN.extend(steady[-1].breakdown)
    cpu_core = cpu_used(samples) / wall * 100
    cpus = psutil.cpu_count() or 1
    label = "playing, in the tray" if minimized else "playing, window open"
    if as_queue:
        label = "playing a big playlist, queue panel open"
    elif playlist:
        label += ", big playlist visible"
    return Result(
        label,
        {
            "RSS steady (median)": mb(statistics.median(s.rss for s in steady)),
            "private steady": mb(statistics.median(s.private for s in steady)),
            "RSS peak (worker up)": mb(max(s.rss for s in samples)),
            "processes (max)": str(max(s.processes for s in samples)),
            "threads": str(steady[-1].threads),
            "CPU incl. resolves": (
                f"{cpu_core:.2f}% of one core = {cpu_core / cpus:.3f}% of {cpus} logical CPUs"
            ),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenarios",
        nargs="*",
        default=["startup", "tray", "tray-after", "idle", "playing", "window", "queue"],
    )
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--exe", help="a packaged ytm-lite.exe to measure instead of the source")
    parser.add_argument(
        "--playlist",
        default=BIG_PLAYLIST,
        help="playlist shown in the 'window' scenario (default: a public 500-track one; "
        "LM is your Liked Songs with --real-profile)",
    )
    parser.add_argument(
        "--real-profile",
        action="store_true",
        help="use your own (signed-in) profile instead of a throwaway signed-out one",
    )
    args = parser.parse_args()
    global REAL_PROFILE, EXE
    REAL_PROFILE = args.real_profile
    EXE = Path(args.exe).resolve() if args.exe else None
    MEASURE_DIR.mkdir(exist_ok=True)

    results = []
    for scenario in args.scenarios:
        print(f"== {scenario}")
        if scenario == "startup":
            result = run_startup()
        elif scenario == "tray":
            result = run_tray(args.seconds)
        elif scenario == "tray-after":
            result = run_tray(args.seconds, playlist=args.playlist)
        elif scenario == "idle":
            result = run_window_idle(min(args.seconds, 20))
        elif scenario == "playing":
            result = run_playing(args.seconds, minimized=True)
        elif scenario == "window":
            result = run_playing(min(args.seconds, 30), minimized=False, playlist=args.playlist)
        elif scenario == "queue":
            result = run_playing(
                min(args.seconds, 30), minimized=False, playlist=args.playlist, as_queue=True
            )
        else:
            parser.error(f"unknown scenario {scenario}")
        for key, value in result.values.items():
            print(f"   {key:<20} {value}")
        for line in LAST_BREAKDOWN:
            print(f"     · {line}")
        LAST_BREAKDOWN.clear()
        results.append(result)
        if scenario in ("playing", "window", "queue"):
            for line in log_lines(scenario, "transition", "resolved", "WARNING", "ERROR")[-8:]:
                print("   log:", line[:160])

    out = MEASURE_DIR / "results.json"
    out.write_text(json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
