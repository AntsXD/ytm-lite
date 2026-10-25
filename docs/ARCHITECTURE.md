# How ytm-lite works

[← Back to the README](../README.md)

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
- **yt-dlp can be updated without a new release of the app.** YouTube breaks stream extraction
  every few weeks. "Check for yt-dlp update" (Settings → Advanced, or the banner that appears
  when songs keep failing) has the worker fetch the newest yt-dlp from PyPI, check it, and
  restart with it; the song playing keeps playing.

## Desktop integration

- **Media keys.** On Windows the media overlay (the System Media Transport Controls) is driven
  from a hidden window of the app's own, so tray mode, which destroys the main window, doesn't
  touch it. If the overlay can't be set up, the media keys are registered as global hotkeys
  instead. On Linux the app is an MPRIS player on the session bus.
- **One copy per profile.** A second launch hands its command (show, play/pause, next,
  previous) to the running copy over a local socket and exits.
- **App identity.** The packaged app sets an AppUserModelID and keeps a Start Menu shortcut
  carrying it, which is how Windows names it in the media overlay.
