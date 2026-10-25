# Measurements

[← Back to the README](../README.md)

Every milestone was measured against the spec's targets, and the findings behind each design
decision are recorded with the numbers.

## How it's measured

```bash
uv run python tools/measure.py         # startup, tray, tray-after, idle, playing, window, queue
uv run python tools/measure.py tray tray-after playing window queue --real-profile --playlist LM
uv run python tools/measure.py --exe dist/ytm-lite/ytm-lite.exe     # the packaged build
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
Measurement runs never save the session, so `--real-profile` leaves your queue alone. The app
is started with a PATH that has no folder holding an OpenSSL (Git for Windows adds one), as on
a typical machine: Qt's OpenSSL TLS backend would load it just to check itself. Until
milestone 5 it did, so earlier source measurements of scenarios that downloaded covers include
~5 MB of Git's OpenSSL. `--exe dist/ytm-lite/ytm-lite.exe` measures the packaged build.

## Measured numbers

Windows 11, Python 3.14.7, PySide6 6.12.0, mpv 0.41 (shinchiro 2026-10-21), yt-dlp 2026.10.21,
ytmusicapi 1.12.3, pywinrt 4.0.3, running from source; from milestone 5 also the packaged
build (Nuitka 4.3.1, MSVC 14.5).

### Release 0.1.0 (2026-10-25)

After milestone 5, Home was redesigned: shelves of big covers that scroll sideways. The release
build, packaged and signed out:

| Spec target | Measured | |
|---|---|---|
| Playing, window open, 200+ track list < 150 MB | 149.6 MB RSS / 86.7 MB private steady (public 462-track playlist); 226.5 MB peak while the worker is up | ✅ at the line |
| Cold start < 2 s | 0.58 s median (5.94 s the first time after the build) | ✅ |

Home stays open behind the playlist, and with its bigger covers it costs about 5 MB more in
the window scenario than milestone 5's Home did (149.6 MB against 144.6 MB). Signed in wasn't
measured again; in milestone 5 it read 5.6 MB above signed out, which would put it above the
150 MB line. The tray scenarios don't build the window, so Home doesn't change them.

### Milestone 5 (2026-10-25)

| Spec target | Packaged, signed in (677-song library) | Packaged, signed out | Source, signed out | |
|---|---|---|---|---|
| Idle in the tray < 80 MB | 14.0 MB RSS / 40.9 MB private | 4.0 MB / 38.1 MB | 3.4 MB / 39.8 MB | ✅ |
| CPU idle in the tray | 0.05% of one core | 0.00% | 0.00% | ✅ |
| Playing, window open, 200+ track list < 150 MB | 150.2 MB / 86.2 MB steady (Liked Songs on screen); 236 MB peak while the worker is up | 144.6 MB / 83.8 MB steady (public 462-track playlist); 221 MB peak | 136.8 MB / 89.0 MB steady; 214 MB peak | ⚠️ at the line signed in |
| Same, that list playing as a queue with the queue panel open | 150.9 MB / 86.7 MB steady; 226 MB peak | 148.5 MB / 86.0 MB steady; 224 MB peak | 141.4 MB / 91.6 MB steady; 219 MB peak | ⚠️ at the line signed in |
| Playing, in the tray | 69.8 MB / 69.3 MB steady; 154 MB peak | 58.4 MB / 66.7 MB steady; 120 MB peak | 69.5 MB / 71.4 MB steady; 132 MB peak | ✅ |
| CPU playing in the tray < 1% | 1.8% of one core *including two resolves*; 0.09% of 20 logical CPUs | 1.9% of one core; 0.10% | 1.7% of one core; 0.09% | ✅ |
| Cold start < 2 s | 0.62 s median | 0.62 s median (6.4 s the first time after a build) | 0.87 s median | ✅ |
| Gapless track change | | | 9 ms, after a minute of normal playback into the next song | ✅ |

The packaged build has the larger working set and the smaller private memory: `ytm-lite.exe`
is a 17 MB image of compiled code, which the source run keeps as bytecode in its heap instead.
With the window open that's 7–8 MB more RSS and 5–6 MB less private. Signed in, with Liked Songs
(677 songs) on screen or as the queue, it puts the packaged build right at the 150 MB line:
150.2 and 150.9 MB (an otherwise identical build compiled without docstrings read 150.5 and
148.9 MB; runs differ by 1–2 MB), with 86 MB private. Task Manager's "Memory" column (the
private working set) read 71.7 MB for the build in the signed-out window scenario, measured
before the OpenSSL fix and the smaller read-ahead below. The next lever would be the media
overlay: pywinrt costs ~5 MB private and about as much working set once something plays, and
calling the same Windows APIs through ctypes, as the Start Menu shortcut does, would remove it.

Milestone 5's features cost little where it counts: the source run's window scenarios are
lower than milestone 4's (136.8 MB against 140.2 MB with the big playlist on screen) thanks to
the smaller read-ahead and the OpenSSL fix below. Idle with the window open is higher (98.4 MB
against 92.6 MB) because the window now opens on Home, covers and all, instead of an almost
empty page.

Findings that shaped milestone 5:

| Finding | Decision |
|---|---|
| Qt's OpenSSL TLS backend loads any OpenSSL it finds on PATH just to check itself (Git for Windows puts one there): ~5 MB in the player. Earlier source measurements of scenarios that downloaded covers include it | Covers use Windows' TLS (schannel); the build leaves the OpenSSL backend out; `measure.py` starts the app with a PATH without OpenSSL folders |
| mpv's 4 MiB read-ahead held two minutes of Premium audio | 2 MiB ahead (a minute of Premium, two of standard quality) and 512 KiB behind: 2.5–7 MB less while playing; track changes stay gapless (9 ms) |
| YouTube's player response carries each song's loudness against YouTube's reference level ("content loudness" in Stats for nerds), in the audio formats that ytmusicapi's `get_song` keeps: Metallica +3.5 dB, Skrillex +2.3, Taylor Swift −1.2, Debussy −10.6 | "Like YouTube Music" normalization does what YouTube's player does: a song above the reference is turned down by that much, a quieter one is left alone. It's set per file (mpv's `volume-gain`), so it changes exactly at the track boundary |
| In the packaged build pywinrt's interop module sits at the package root, so looking for a `winrt/windows/media/interop` folder found nothing and the overlay fell back to hotkeys | pywinrt is detected by finding the module's spec in the `winrt` namespace package, which runs no code in either layout |
| Windows names a desktop app (media overlay, notifications) after the Start Menu shortcut that carries its AppUserModelID; the overlay session was "python.exe" | The packaged app sets the ID and writes its Start Menu shortcut with the shell's COM objects (ctypes, no new package); the shell's Apps folder now resolves `AntsXD.ytm-lite` to "ytm-lite" |
| RegisterHotKey fails for a combination another app already holds, and a bare letter would be taken from every app | Refusals show next to the hotkey in Settings; hotkeys need Ctrl, Alt or Win, except F-keys and media keys |
| Qt's Fusion style instead of the Windows 11 one, and compiling without docstrings, each changed the window scenarios by about 1 MB, inside the noise | Neither was kept |

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
