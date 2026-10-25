# Development

[← Back to the README](../README.md)

## Setup

Needs Python 3.11+ (developed on 3.14) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python tools/fetch_libmpv.py
uv run python -m ytm_lite
```

`fetch_libmpv.py` downloads the pinned libmpv build (shinchiro/mpv-winbuild-cmake), checks its
sha256 and puts `libmpv-2.dll` in `vendor/`. On Linux, install libmpv from your package manager.

## Checks

Run tools as modules: Smart App Control blocks the unsigned `.exe` launchers that pip creates
(`mypy.exe`, `pytest.exe`).

```bash
uv run python -m pytest
uv run python -m mypy
uv run ruff check . && uv run ruff format --check .
```

Diagnostics:

- `--debug` writes a verbose log.
- `--memory-report SECONDS` logs the Python heap's top allocation sites and the process's
  working set / private bytes after that many seconds (tracemalloc makes the app slower).
- `YTM_WORKER_TRACE=1` logs each yt-dlp step of a resolve with its timestamp.
- `YTM_WORKER_EXTRACTOR_ARGS='{"player_skip": ["configs"]}'` adds yt-dlp YouTube extractor
  arguments, for experiments.

Logs go to `%LOCALAPPDATA%\ytm-lite\Logs\ytm-lite.log` (1 MB × 3, secrets redacted).
`YTM_LITE_HOME=<folder>` puts config, cache and logs in one folder instead. Ctrl+Shift+D in the
app toggles the debug overlay (memory per process, threads, worker and libmpv state, the
stream's format, the normalization gain, the output device, queue, cover caches, last
track-transition gap).

## Build the Windows package

```bash
uv sync --group build
uv run python tools/fetch_libmpv.py
uv run python tools/build.py
```

`tools/build.py` writes `dist/ytm-lite/` (about 360 MB; zip the folder to share it):

- `ytm-lite.exe` with Qt and Python: the app, compiled by Nuitka (about 70 MB). yt-dlp,
  ytmusicapi and `requests` are not in it; only the worker uses them.
- `lib/libmpv-2.dll` (121 MB).
- `worker/`: python.org's embeddable Python 3.14 (pinned, SHA-256 checked) that runs the worker,
  with yt-dlp, yt-dlp-ejs, ytmusicapi and their dependencies (the files installed in `.venv`,
  precompiled) and `deno.exe` (98 MB) for Premium streams.

A build takes about two minutes with Visual Studio 2026's C++ compiler. The packaged app keeps
its settings, library cache and logs where the source run does, so the two share your sign-in
(run only one at a time: the second just brings up the first one's window). The first time it
runs, it adds **ytm-lite** to the Start menu, pointing at wherever the folder is. The entry
carries the app's AppUserModelID (`AntsXD.ytm-lite`), which is how Windows knows the name and
icon to show in the media overlay. To remove it, delete
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\ytm-lite.lnk`. The build isn't code-signed,
so the first start after a build takes a few extra seconds while Windows checks the new files.

## Measure

See [MEASUREMENTS.md](MEASUREMENTS.md) for how the app is measured and every number so far.
