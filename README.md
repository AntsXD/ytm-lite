# ytm-lite

A lightweight native YouTube Music desktop client, built to sit next to RAM-hungry games.
Qt Widgets UI, libmpv for audio, no embedded browser of any kind.

Unofficial: not affiliated with or endorsed by Google or YouTube. Use it with your own account.

**Status:** milestone 4 of 5 (search, album and artist pages, the tray, media keys). Next: lyrics,
the home feed, a mini player, a settings dialog and the packaged build (milestone 5).

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
  current one starts and handed to mpv, so track changes are gapless. Any queue edit that changes
  what comes next (play next, a drag, shuffle, repeat) swaps the prepared track.
- **Signed in, the track you start yourself starts fast, everything after it is Premium.** A
  double-click (or Next/Previous with nothing prepared) resolves the quick signed-out way:
  ~1.5 s, standard quality (Opus ~134 kbps). The tracks that follow are resolved in Premium
  (Opus ~256 kbps) in the background while the current one plays, where the ~4 s it takes
  doesn't matter. The quality label in the bar shows which one is playing.
- **Signed out**, streams come from YouTube's `visionos` client, which needs no JavaScript
  challenge solving (no Deno/Node process at all). **Signed in**, the `web_music` client serves
  the Premium formats (Opus ~256 kbps) and needs a JS runtime: Deno ships as a dependency; Node ≥ 22
  also works.
- Library lists are cached on disk and shown at startup, then refreshed in the background.
  Long playlists show their first 100 tracks at once and fill in the rest.
- **Closing the window frees what the window costs.** The music keeps playing from the tray,
  but the window itself (widgets, open pages, decoded covers) is destroyed and the working set
  trimmed, so a game gets that RAM back. Clicking the tray icon rebuilds the window, on the page
  you left, in a fraction of a second.
- **The Windows media overlay is set up the first time something plays.** Loading pywinrt costs
  ~5 MB of private memory (twice that if it were allowed to pull in `asyncio` and OpenSSL, which
  it only needs for `await`), so an app idling in the tray never loads it. Covers are handed to
  Windows as bytes from the app's own cover cache, not as URLs for WinRT to download.

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

- The window is three rounded panels on black (your library, the page you're on, the queue)
  with the player along the bottom; the accent color is violet.
- **Search** with the box at the top (Ctrl+F). Suggestions appear as you type; Enter or a
  suggestion runs the search. "All" shows the top result and the best songs, albums, artists
  and playlists; the Songs, Albums, Artists and Playlists tabs load when you open them. A song
  played from search is followed by its radio.
- Albums and artists open as pages, from search, from your library or from a song's
  right-click menu. The arrows above the page, Alt+← / Alt+→ and the mouse's back and forward
  buttons move through the pages you've visited.
- Double-click a track to play it; the rest of the list becomes the queue. Right-click a track
  (or a selection) for Play, Play next, Add to queue, Start radio, Go to album, Go to artist,
  Like and Copy link.
- The list button at the right of the bar opens the queue: drag songs to reorder them, Delete
  removes the selection, double-click jumps to a song. With **Autoplay** on, radio suggestions
  for the last song are added when two songs are left.
- **Closing the window keeps the music playing in the tray.** Click the tray icon to bring the
  window back (middle-click: play/pause); its menu has Play/Pause, Next, Previous and Quit. To
  make closing quit instead, set `"close_to_tray": false` in `settings.json` (a settings dialog
  comes in milestone 5). `--start-minimized` starts in the tray, without a window.
- **Media keys and the Windows media overlay** (the volume flyout) work: the overlay shows the
  song, its cover and the position, with play/pause, next, previous and seeking. On Linux the
  app is an MPRIS player, so desktop media widgets and `playerctl` control it.
- **One copy per profile.** Starting the app again brings up the running copy's window;
  `--play-pause`, `--next` and `--previous` (or `--prev`) are passed to the running copy, e.g.
  from a hotkey tool: `uv run python -m ytm_lite --next`.
- **Plays count.** Signed in, a song you've played for 10 s (pauses don't count) is added to your
  YouTube Music history, as YouTube Music's own player does, so it shows up in your history,
  your recommendations and your yearly Recap. Set `"report_history": false` in `settings.json`
  to keep plays out of your history.
- Shortcuts: Space play/pause, Ctrl+→ / Ctrl+← next/previous, Ctrl+L like, Ctrl+F search,
  Alt+← / Alt+→ back/forward, Ctrl+Q quit.
- The queue, your place in it (paused), shuffle and repeat, volume and the window layout come back
  at the next start. They are saved in `queue.json` and `state.json` next to `settings.json`.

Ctrl+Shift+D toggles the debug overlay (memory per process, threads, worker and libmpv state,
queue, cover cache, last track-transition gap). Options: `--debug` (verbose log),
`--start-minimized` (start in the tray).

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
still lets you search, open albums and artists and play anything (standard quality); your
library and likes need a sign-in.

## Privacy, security and your account

- The app talks only to YouTube and Google servers (the YouTube Music API, the audio servers,
  cover images). It has no telemetry and listens on no network port; the single-instance
  channel is a local pipe only your Windows user can open, and it accepts four commands.
- Everything goes over HTTPS with certificate checks, including the audio streams: mpv doesn't
  check certificates unless told to, so the app turns that on and gives it certifi's CA bundle
  (the Windows libmpv build's OpenSSL has no store of its own). The stream requests carry no
  cookies.
- Premium streams need YouTube's JavaScript challenge solved. yt-dlp runs it in Deno with no
  file, network or environment permissions.
- Your cookies are as good as being signed in to YouTube. DPAPI keeps them from other Windows
  users and from anyone who takes the disk, not from malware running as you (Firefox keeps the
  same cookies unencrypted in its profile).
- This is an unofficial client: YouTube's terms cover its own apps, and yt-dlp's maintainers warn
  that an account used with yt-dlp can be banned, depending on request rate and download volume.
  The app asks for one stream per song you play (plus the next one ahead of time) and downloads
  nothing to disk; the song you start yourself is resolved without your account.

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
uv run python tools/measure.py         # startup, tray, tray-after, idle, playing, window, queue
uv run python tools/measure.py tray tray-after playing window queue --real-profile --playlist LM
uv run python tools/spikes/s2_libmpv.py --variant small
uv run python tools/spikes/s3_qt_tray.py
uv run python tools/spikes/s5_smtc.py interop
```

`measure.py` launches the app (by default in a throwaway signed-out profile under `.measure/`;
with `--real-profile`, your own signed-in one) and samples the whole process tree once a second
with psutil. **RSS** is the Windows working set; **private** is private bytes. "Steady" is
measured after the worker's idle exit; "peak" includes the worker and its JS runtime. The venv's
`python.exe` redirector process is excluded because a packaged build doesn't have it. `tray`
starts the app in the tray with nothing playing; `tray-after` shows a large playlist in the
window for 30 s, then closes the window to the tray; both sample for 60 s once the worker has
exited. `playing` plays the test tracks from the tray (until milestone 3: a minimized window).
The `window` scenario opens a large playlist (a public 462-track one, or `LM` = your Liked Songs)
while the test tracks play; `queue` plays that playlist itself with the queue panel open.
Measurement runs never save the session, so `--real-profile` leaves your queue alone.

## Measured numbers

Windows 11, Python 3.14.7, PySide6 6.12.0, mpv 0.41 (shinchiro 2026-10-21), yt-dlp 2026.10.21,
ytmusicapi 1.12.3, pywinrt 4.0.3, running from source.

### Milestone 4 (2026-10-25)

| Spec target | Signed in (677-song library, Premium) | Signed out | |
|---|---|---|---|
| Idle in the tray < 80 MB | 13.8 MB RSS / 38.9 MB private, started in the tray (Liked Songs loads ~5 s in, after the working set was trimmed); 5.0 MB / 52.9 MB after Liked Songs was shown in the window and the window closed | 3.3 MB / 37.4 MB started in the tray; 4.7 MB / 51.9 MB after a 462-track playlist was shown and the window closed | ✅ |
| CPU idle in the tray | 0.03% of one core | 0.00% (no timers, no polling) | ✅ |
| Playing, window open, 200+ track list < 150 MB | 146.0 MB / 92.6 MB steady (Liked Songs on screen); 228 MB peak while the worker is up | 140.2 MB / 90.1 MB steady (public 462-track playlist); 211 MB peak | ✅ steady |
| Same, that list playing as a queue with the queue panel open | 147.4 MB / 93.8 MB steady (677-song queue); 232 MB peak | 145.0 MB / 91.6 MB steady (462-song queue); 220 MB peak | ✅ steady |
| Playing, in the tray | 72.4 MB / 73.2 MB steady; 151 MB peak | 70.2 MB / 71.1 MB steady; 129 MB peak | ✅ |
| CPU playing in the tray < 1% | 2.2% of one core *including two resolves*; 0.11% of 20 logical CPUs | 1.7% of one core including two resolves; 0.08% of 20 logical CPUs | ✅ |
| Cold start < 2 s | 0.77 s median (0.30 s to READY when starting in the tray) | 0.77 s median | ✅ |

Playing from the tray takes 70–72 MB, where milestone 3's minimized window took 108–131 MB: in
the tray none of the window's memory is left. With the window open, milestone 4 costs more than
milestone 3, measured side by side on the same day: idle, 92.6 MB / 53.5 MB against
87.2 MB / 49.0 MB (the new pages' code, the top bar, the tray icon). Once something has played,
the media overlay adds ~5 MB private (pywinrt) and ~6 MB of working set in Windows DLLs, plus
~3 MB of working set once a cover is shown. That is why the playing-with-the-window-open rows are
11–17 MB above milestone 3's and closer to the 150 MB line. The cold start differs from
milestone 3's table because of the machine, not the code: the milestone 3 code starts in 0.70 s
today, and milestone 4 adds ~70 ms.

After milestone 4, mpv checks the audio servers' certificates and plays are reported to your
history. Measured the same way, signed out: playing from the tray 72.1 MB / 73.5 MB, the big
playlist as the queue 144.2 MB / 93.1 MB. The CA bundle OpenSSL loads for each stream
connection costs ~2 MB private; history reporting runs in the worker and costs the player
nothing.

Findings that shaped milestone 4:

| Finding | Decision |
|---|---|
| pywinrt imports `asyncio` (and with it `ssl` and OpenSSL) as it loads, only so WinRT calls can be awaited: 6 of the ~11 MB private it cost | It's imported with a stand-in for `asyncio` that loads the real one only if pywinrt ever uses it (the app never awaits): pywinrt now costs ~5 MB private, from the first song played |
| `hashlib`, used to name the single-instance socket, loads OpenSSL into the player process too | A CRC names the socket |
| Spike S5, two ways to drive the overlay (measured before the `asyncio` fix): `get_for_window` on a hidden window +27 MB working set / +13–14 MB private, 14 threads; a `MediaPlayer`'s controls +32 MB / +13 MB, 18–19 threads | `get_for_window`, on a hidden window of its own, so tray mode (which destroys the main window) doesn't touch it |
| Given as a URL, the cover is downloaded by WinRT inside the app's process (+10 MB working set, 4 threads); given as bytes it costs +3 MB working set (Windows' image codecs) and +0.7 MB private | Covers go over as JPEG bytes from the app's own cover cache |
| A gapless track change never changes the play state, so an overlay updated only on play/pause/seek kept the previous song's length | The timeline is also sent whenever mpv restarts playback (a new track or a seek) |
| A second launch that writes its command and exits at once could lose it: Qt drops unsent bytes when the socket object goes away, and the running copy may see the hang-up before reading | The sender waits until the line is out and the disconnect is done; the receiver also reads what arrived before the connection was accepted (tested across two processes) |
| PySide6's QtDBus sends a Python int as int32 when it fits; MPRIS wants int64 positions and lengths | Position and Seeked are declared `qlonglong`; empty string lists go out as typed arrays. Checked over a real session bus in WSL (Ubuntu) with `busctl`. A length inside Metadata still goes out as int32 under ~35 minutes (GNOME Shell, Plasma and playerctl read either) |
| ytmusicapi files the number on a search's top-result artist card as "subscribers"; YouTube labels it "monthly audience" (the artist's page has both) | Shown as monthly audience; the artist page shows subscribers and monthly audience |

### Milestone 3 (2026-10-25)

| Spec target | Signed in (677-song library, Premium) | Signed out | |
|---|---|---|---|
| Idle < 80 MB | measured in M4, with tray mode | 88.7 MB RSS / 49.4 MB private with the window open | M4 |
| Playing, window open, 200+ track list < 150 MB | 129.4 MB RSS / 84.0 MB private steady (Liked Songs on screen); 214 MB peak while the worker is up | 126.6 MB / 82.2 MB steady (public 462-track playlist); 202 MB peak | ✅ steady |
| Same, that list playing as a queue with the queue panel open | 132.9 MB / 85.4 MB steady (677-song queue); 219 MB peak | 131.4 MB / 85.1 MB steady (462-song queue); 200 MB peak | ✅ steady |
| Playing, minimized | 131.0 MB / 84.1 MB steady; 217 MB peak | 107.7 MB / 71.3 MB steady; 167 MB peak | ✅ steady |
| CPU minimized < 1% | 3.6% of one core *including two resolves*; 0.18% of 20 logical CPUs | 2.4% of one core including two resolves; 0.12% of 20 logical CPUs | ✅ |
| Cold start < 2 s | 1.09 s median (cached library shown at once) | 1.09 s median | ✅ |
| Gapless track change | 3 ms from the standard-quality first track into the Premium one | 1 ms (M1) | ✅ |
| Premium audio | the clicked track: format 251, Opus 127–134 kbps in 1.4–1.7 s; every track after it: format 774, Opus 260–275 kbps, resolved in 3.6–4.4 s while the previous one plays | format 251, Opus ~134 kbps | ✅ |

Compared with milestone 2, the queue, the new bar and session saving cost 2–3 MB; a queue of a few
hundred songs with the queue panel open adds another 4–5 MB.

After milestone 3 the UI was restyled (panels, violet accent, taller rows with bigger covers,
playlist headers tinted from their cover). Measured the same way, signed out: idle 86.7 MB /
48.5 MB, big playlist on screen 129.3 MB / 85.4 MB, that playlist as the queue with the panel
open 129.0 MB / 84.8 MB, cold start 1.09 s: within 3 MB of the numbers above.

Findings that shaped milestone 3:

| Finding | Decision |
|---|---|
| Signed in, a click took ~4 s to start: the Premium path waits for the watch page, the player API and Deno's JS challenge | The track you start resolves the signed-out way (no cookies, no JS: ~1.5 s, standard quality); the tracks after it are prefetched in Premium. The URL cache keeps the two kinds apart, so a standard URL is never prepared as the next track |
| QtSvg (module + image plugin) just to draw a dozen toolbar icons | Icons are Material Design path data drawn with QPainter (a 60-line path parser) |
| A 5,000-song queue is ~1 MB of JSON; rewriting it every 30 s while playing would be wasted work | Two files: `queue.json` only when the queue itself changes, `state.json` (position, cursor, modes, layout) often |
| YouTube once answered the Liked Songs request right after a Firefox cookie refresh with a page ytmusicapi can't read; the next try worked | Full playlist loads are retried once after 5 s, and load errors are shown in plain words instead of ytmusicapi's message |

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

## Credits

Toolbar icons are path data from Google's [Material Icons](https://github.com/google/material-design-icons)
(Apache License 2.0).
