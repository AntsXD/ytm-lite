"""Spike S2: libmpv memory, CPU and track-transition gap, measured without Qt.

Streams are resolved by the real worker process so yt-dlp's memory stays out of this
process. Variants compare the demuxer cache settings.

Usage: uv run python tools/spikes/s2_libmpv.py [--variant spec|small|disk] [--cpu-seconds 60]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ["PATH"] = str(ROOT / "vendor") + os.pathsep + os.environ["PATH"]
os.add_dll_directory(str(ROOT / "vendor"))

from ytm_lite.app.player import MPV_OPTIONS, mpv_option_string  # noqa: E402

OPUS_TRACK = "4D7u5KF7SP8"  # Get Lucky, 6:10
AAC_TRACK = "BSTsnWoslP4"  # Bohemian Rhapsody, resolved as format 140

VARIANTS: dict[str, dict[str, str]] = {
    "spec": {},
    "small": {"demuxer-max-bytes": "4MiB", "demuxer-max-back-bytes": "1MiB"},
    "disk": {
        "demuxer-max-bytes": "8MiB",
        "demuxer-max-back-bytes": "1MiB",
        "cache-on-disk": "yes",
        "demuxer-cache-dir": str(ROOT / ".measure" / "mpv-cache"),
    },
}


def snap(label: str) -> None:
    proc = psutil.Process()
    mem = proc.memory_info()
    private = getattr(mem, "private", 0)
    print(
        f"   {label:<34} RSS {mem.rss / 1e6:6.1f} MB  private {private / 1e6:6.1f} MB"
        f"  threads {proc.num_threads()}"
    )


def resolve(video_ids: dict[str, str | None]) -> dict[str, dict[str, Any]]:
    cache = ROOT / ".measure" / "cache"
    proc = subprocess.Popen(
        [sys.executable, "-I", "-m", "ytm_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin and proc.stdout
    proc.stdin.write((json.dumps({"op": "configure", "cachedir": str(cache)}) + "\n").encode())
    for i, (video_id, fmt) in enumerate(video_ids.items()):
        msg = {"op": "resolve", "id": i, "video_id": video_id, "priority": 0, "format": fmt}
        proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()
    streams: dict[str, dict[str, Any]] = {}
    while len(streams) < len(video_ids):
        reply = json.loads(proc.stdout.readline())
        if reply["op"] == "result":
            streams[reply["video_id"]] = reply["stream"]
        elif reply["op"] == "error":
            raise RuntimeError(reply["message"])
    proc.stdin.write(b'{"op":"shutdown"}\n')
    proc.stdin.close()
    proc.wait(10)
    return streams


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="spec")
    parser.add_argument("--cpu-seconds", type=float, default=30)
    parser.add_argument(
        "--gap-lead",
        type=float,
        default=20,
        help="seconds before the end to seek to for the gap test",
    )
    args = parser.parse_args()

    streams = resolve({OPUS_TRACK: None, AAC_TRACK: "140"})
    print(f"== variant {args.variant}")
    snap("baseline (python + psutil)")
    import mpv

    snap("libmpv DLL loaded")
    options = {**MPV_OPTIONS, **VARIANTS[args.variant]}
    events: list[tuple[float, str]] = []
    lock = threading.Lock()

    def on_event(event: Any) -> None:
        name = {6: "start-file", 7: "end-file", 18: "audio-reconfig", 21: "playback-restart"}.get(
            event.event_id.value
        )
        if name:
            with lock:
                events.append((time.perf_counter(), name))

    player = mpv.MPV(loglevel="warn", **{k.replace("-", "_"): v for k, v in options.items()})
    player.register_event_callback(on_event)
    snap("MPV() created")

    def load(stream: dict[str, Any], mode: str) -> None:
        opts = {"user-agent": stream["http_headers"]["User-Agent"]}
        player.command("loadfile", stream["url"], mode, "-1", mpv_option_string(opts))

    load(streams[OPUS_TRACK], "replace")
    time.sleep(12)
    cache = player._get_property("demuxer-cache-state") or {}
    snap("playing Opus track, 12 s in")
    print(
        f"   demuxer cache: fw {cache.get('fw-bytes', 0) / 1e6:.1f} MB, "
        f"total {cache.get('total-bytes', 0) / 1e6:.1f} MB"
    )

    load(streams[AAC_TRACK], "append-play")
    time.sleep(8)
    snap("+ AAC track armed (prefetched)")

    proc = psutil.Process()
    cpu0 = sum(proc.cpu_times()[:2])
    t0 = time.perf_counter()
    time.sleep(args.cpu_seconds)
    cpu = (sum(proc.cpu_times()[:2]) - cpu0) / (time.perf_counter() - t0) * 100
    print(f"   CPU while playing: {cpu:.2f}% of one core")

    # Gap test: jump close to the end of the Opus track and watch the change to AAC.
    duration = float(player._get_property("duration") or 370)
    with lock:
        events.clear()
    player.command("seek", f"{duration - args.gap_lead:.1f}", "absolute")
    time.sleep(args.gap_lead + 4)
    with lock:
        seen = list(events)
    ends = [t for t, n in seen if n == "end-file"]
    starts = [t for t, n in seen if n == "playback-restart"]
    reconfigs = sum(1 for _, n in seen if n == "audio-reconfig")
    if ends and starts and starts[-1] > ends[0]:
        print(
            f"   Opus->AAC: end-file to playback-restart {1000 * (starts[-1] - ends[0]):.0f} ms, "
            f"audio-reconfig events {reconfigs}"
        )
    print("   events:", [n for _, n in seen])
    decoded = (player._get_property("audio-params") or {}).get("samplerate")
    output = (player._get_property("audio-out-params") or {}).get("samplerate")
    print(f"   now playing samplerate {decoded} / output {output}")
    snap("after transition")

    player.terminate()
    snap("after terminate()")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
