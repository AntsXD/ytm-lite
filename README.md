# ytm-lite

A lightweight native YouTube Music desktop client, built to sit next to RAM-hungry games.
Qt Widgets UI, libmpv for audio, no embedded browser of any kind.

**Status:** milestone 2 of 5 (sign-in and library). Next: the full play queue and
now-playing controls (milestone 3).

## How it works

```
ytm-lite (player process)                      worker (started on demand)
├─ Qt Widgets UI                                python -I -m ytm_worker
├─ library, liked songs, play queue   ◄─JSON─►    yt-dlp (YouTube extractor only): stream URLs
├─ libmpv (created on first Play,                 ytmusicapi: library, playlists, likes
│   released after 10 min paused)                 exits after 15 s idle, below-normal priority
└─ cover art (Qt network + disk cache)
```

- **Everything that talks to YouTube runs in the worker**, which exits when idle. yt-dlp,
  ytmusicapi and `requests` cost 60–85 MB, and parsing big API responses leaves Python's heap
  inflated for good; in a short-lived process that memory goes back to Windows.
- **mpv's playlist only ever holds `[current, next]`.** The next track is resolved as soon as the
  current one starts and handed to mpv, so track changes are gapless.
- **Signed out**, streams come from YouTube's `visionos` client, which needs no JavaScript
  challenge solving (no Deno/Node process at all). **Signed in**, the `web_music` client serves
  the Premium formats (Opus ~256 kbps) and needs a JS runtime: Deno ships as a dependency; Node ≥ 22
  also works.
- Library lists are cached on disk and shown at startup, then refreshed in the background.
  Long playlists show their first 100 tracks at once and fill in the rest.

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

Ctrl+Shift+D toggles the debug overlay (memory per process, threads, worker and libmpv state,
cover cache, last track-transition gap). Options: `--debug` (verbose log), `--start-minimized`.

Logs go to `%LOCALAPPDATA%\ytm-lite\Logs\ytm-lite.log` (1 MB × 3, secrets redacted).
`YTM_LITE_HOME=<folder>` puts config, cache and logs in one folder instead.

## Signing in

The first start asks you to sign in. Three ways:

- **Import from Firefox** (simplest). Reads only the youtube.com cookies of your Firefox profile's
  default container, from a temporary copy of its cookie database that is deleted right after.
  If YouTube later rejects the session (Firefox rotates its cookies), the app quietly re-reads
  them before asking you to sign in again.
- **Paste headers.** Open a private window, sign in at music.youtube.com, press F12 → Network,
  filter on `browse`, and copy a request's headers (Firefox: Request Headers → Raw; Chrome/Edge:
  right-click → Copy as cURL (bash)). Close the private window afterwards so the browser can't
  rotate the copied cookies.
- **cookies.txt** (advanced): a Netscape-format export of a signed-in YouTube session.

Whichever you use, the Premium account should be the browser's default Google account (yt-dlp
always uses account index 0). Credentials are checked with YouTube before anything is saved,
stored encrypted with Windows DPAPI (owner-only file elsewhere), and handed to the worker in
memory; they are never written to disk in plain text or logged. "Continue without signing in"
keeps search-free playback of the test tracks available (search arrives in milestone 4).

## Develop

Run tools as modules: Smart App Control blocks the unsigned `.exe` launchers that pip creates
(`mypy.exe`, `pytest.exe`).

```bash
uv run python -m pytest
uv run python -m mypy
uv run ruff check . && uv run ruff format --check .
```

Diagnostics:

- `--memory-report SECONDS` logs the Python heap's top allocation sites and the process's
  working set / private bytes after that many seconds (tracemalloc makes the app slower).
- `YTM_WORKER_TRACE=1` logs each yt-dlp step of a resolve with its timestamp.
- `YTM_WORKER_EXTRACTOR_ARGS='{"player_skip": ["configs"]}'` adds yt-dlp YouTube extractor
  arguments, for experiments.

## Measure

```bash
uv run python tools/measure.py                       # startup, idle, playing, window
uv run python tools/measure.py playing window --real-profile --playlist LM
uv run python tools/spikes/s2_libmpv.py --variant small
uv run python tools/spikes/s3_qt_tray.py
```

`measure.py` launches the app (by default in a throwaway signed-out profile under `.measure/`;
with `--real-profile`, your own signed-in one) and samples the whole process tree once a second
with psutil. **RSS** is the Windows working set; **private** is private bytes. "Steady" is
measured after the worker's idle exit; "peak" includes the worker and its JS runtime. The venv's
`python.exe` redirector process is excluded because a packaged build doesn't have it. The
`window` scenario opens a large playlist (a public 462-track one, or `LM` = your Liked Songs).

## Measured numbers

Windows 11, Python 3.14.7, PySide6 6.12.0, mpv 0.41 (shinchiro 2026-10-21), yt-dlp 2026.10.21,
ytmusicapi 1.12.3, running from source.

### Milestone 2 (2026-10-25)

| Spec target | Signed in (677-song library, Premium) | Signed out | |
|---|---|---|---|
| Idle < 80 MB | measured in M4, with tray mode | 86.8 MB RSS / 47.4 MB private with the window open | M4 |
| Playing, window open, 200+ track list < 150 MB | 127.5 MB RSS / 82.4 MB private steady (Liked Songs, 677 tracks); 214 MB peak while the worker is up | 123.4 MB / 81.5 MB steady (public 462-track playlist); 201 MB peak | ✅ steady |
| Playing, minimized | 129.4 MB / 83.5 MB steady; 217 MB peak | 106.2 MB / 70.7 MB steady; 166 MB peak | ✅ steady |
| CPU minimized < 1% | 3.5% of one core *including two signed-in resolves*; 0.18% of 20 logical CPUs | 2.0% of one core including two resolves; 0.10% of 20 logical CPUs | ✅ |
| Cold start < 2 s | 1.02 s (cached library shown at once) | 1.10 s | ✅ |
| Premium audio | format 774, Opus 260–268 kbps | format 251, Opus ~134 kbps | ✅ |

Starting a track you click takes ~4 s signed in (watch page ~1.9 s, player API ~0.4 s, Deno JS
challenge ~1.4 s) and ~1.5–2 s signed out; the next track is always resolved in the background,
so playlist and radio playback changes tracks instantly.

Findings that shaped milestone 2:

| Finding | Decision |
|---|---|
| In the player process, `requests` alone cost 18 MB, and fetching + parsing a 462-track playlist left 15 MB in Python's heap for good | ytmusicapi moved into the worker, next to yt-dlp; only compact JSON results come back |
| Drawing Chinese/Japanese/Korean/Thai/emoji titles with Qt's default DirectWrite font engine mapped a dozen large fallback fonts: +45–50 MB working set, +16–20 MB private. Qt's GDI engine draws the same glyphs with the same fallback fonts and no missing characters for almost nothing (emoji in monochrome) | Windows uses `-platform windows:fontengine=gdi` (unless `QT_QPA_PLATFORM` is set) |
| ytmusicapi calls `setlocale(LC_ALL, ...)` when it creates a client; libmpv refuses to start (mpv_create returns NULL) unless numbers use the "C" locale | ytmusicapi's locale call is neutralised; `LC_NUMERIC=C` is set before creating mpv |
| Signed in, yt-dlp downloads web_music's config page on every resolve (~1.2 s) | `player_skip=configs` for `web_music`: same Premium formats, ~1 s faster |

### Milestone 1 (2026-10-25, signed out)

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
