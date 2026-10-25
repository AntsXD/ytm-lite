# ytm-lite

A lightweight native YouTube Music desktop client, built to sit next to RAM-hungry games.
Qt Widgets UI, libmpv for audio, no embedded browser of any kind.

**Status:** milestone 1 of 5 (playback core). The window currently plays a few built-in
test tracks; sign-in and the library arrive in milestone 2.

## How it works

```
ytm-lite (main process)                       resolver worker (started on demand)
├─ Qt Widgets UI                               python -I -m ytm_resolver
├─ play queue + playback controller   ◄─JSON─►   yt-dlp, YouTube extractor only
├─ libmpv (created on first Play,               exits after 15 s idle, below-normal priority
│   released after 10 min paused)
└─ ytmusicapi (milestone 2)
```

- yt-dlp runs in a separate, short-lived process. It costs ~45–60 MB while alive, so keeping it
  out of the player is what lets the app idle small.
- mpv's playlist only ever holds `[current, next]`. The next track is resolved as soon as the
  current one starts and handed to mpv, so track changes are gapless.
- Signed out, streams come from YouTube's `visionos` client, which needs no JavaScript
  challenge solving: no Deno/Node process is started at all. Signed in (milestone 2), the
  `web_music` client serves the Premium formats and needs a JS runtime (Deno ships as a
  dependency; Node ≥ 22 also works).

## Setup (Windows)

Needs Python 3.11+ (developed on 3.14) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python tools/fetch_libmpv.py
```

`fetch_libmpv.py` downloads the pinned libmpv build (shinchiro/mpv-winbuild-cmake), checks its
sha256 and puts `libmpv-2.dll` in `vendor/`. On Linux, install libmpv from your package manager.

## Run

```bash
uv run python -m ytm_lite
```

Options: `--debug` (verbose log), `--start-minimized`. Ctrl+Shift+D toggles the debug overlay
(memory per process, threads, libmpv/resolver state, last track-transition gap).

Logs go to `%LOCALAPPDATA%\ytm-lite\Logs\ytm-lite.log` (1 MB × 3, secrets redacted).
`YTM_LITE_HOME=<folder>` puts config, cache and logs in one folder instead.

## Develop

Run tools as modules: Smart App Control blocks the unsigned `.exe` launchers that pip creates
(`mypy.exe`, `pytest.exe`).

```bash
uv run python -m pytest
uv run python -m mypy
uv run ruff check . && uv run ruff format --check .
```

## Measure

```bash
uv run python tools/measure.py                  # startup, idle, playing, window
uv run python tools/spikes/s1_resolve.py --runtime deno --runtime node
uv run python tools/spikes/s2_libmpv.py --variant small
uv run python tools/spikes/s3_qt_tray.py
```

`measure.py` launches the app in a throwaway profile under `.measure/` and samples the whole
process tree (app + resolver worker + JS runtime) with psutil. "RSS" is the Windows working set;
"private" is private bytes. The venv's `python.exe` redirector process is excluded because a
packaged build doesn't have it.

## Measured numbers

Windows 11, Python 3.14.7, PySide6 6.12.0, mpv 0.41 (shinchiro 2026-10-21), yt-dlp 2026.10.21,
running from source, signed out.

### Milestone 1 (2026-10-25)

| Spec target | Measured | |
|---|---|---|
| Idle < 80 MB | 74.5 MB RSS / 36.7 MB private (window open, nothing loaded; tray mode comes in M4) | ✅ |
| Playing < 150 MB | median 97.7 MB minimized, 106.5 MB window open; peak 156–165 MB while the resolver worker is alive (~15 s per track) | ✅ median |
| CPU minimized < 1% | 0.65% of one core (0.03% of 20 logical CPUs) | ✅ |
| Cold start < 2 s | 0.60 s median (5 runs) | ✅ |
| Gapless track change | 1 ms end-of-file → playback restart, Opus → AAC, no audio-device reopen | ✅ |

Spike results that shaped the design:

| Spike | Finding | Decision |
|---|---|---|
| S1 resolve | With the default clients, every resolve starts Deno/Node to solve YouTube's JS challenge: ~3 s and a **~385 MB** transient peak. The `visionos` client needs no challenge: ~1.5–2.2 s, 73 MB peak | Signed out: `visionos` without a JS runtime; JS only as fallback |
| S1 available_at | Default-client URLs answer 403 until ~2 s before `available_at` (about 4 s after resolving); `visionos` URLs are usable at once | The controller waits out `available_at` before loading |
| S2 libmpv | DLL load +18 MB private; spec cache (16/4 MiB) +38 MB per buffered track, +25 MB more once the next track is armed. 4/1 MiB: whole libmpv cost ~35 MB | `demuxer-max-bytes=4MiB`, `demuxer-max-back-bytes=1MiB` |
| S2 gapless | With `gapless-audio=yes` and fixed 48 kHz stereo output, Opus → AAC keeps the device open | Fixed output format |
| S3 Qt + tray | Window + 200-row table + tray: 69 MB RSS / 33 MB private. `EmptyWorkingSet` drops RSS to ~1 MB (private unchanged); destroying the window frees ~5 MB private | Tray mode trims the working set; report private bytes too |
| S4 Nuitka | Standalone build (MSVC 14.5) runs under Smart App Control; first launch 3.9 s (Windows scanning the new exe), then 0.47 s | Keep Nuitka for milestone 5 |
