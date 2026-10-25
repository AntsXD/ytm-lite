"""Spike S5: what the Windows media overlay (SMTC) costs in memory, per route.

    python tools/spikes/s5_smtc.py interop      # get_for_window() on a hidden window
    python tools/spikes/s5_smtc.py mediaplayer  # MediaPlayer().system_media_transport_controls
                                                # (needs winrt-Windows.Media.Playback)

Each run starts from a bare QApplication, measures, sets up SMTC with a title, artist
and cover, sets "playing", waits for Windows to fetch the cover, and measures again.
"""

from __future__ import annotations

import sys
import time

import psutil
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QWidget

COVER = "https://lh3.googleusercontent.com/ulK2YaLtOW0PzcN7ufltG6e4ae3WZ9Bvg8CCwhe6LOccu1lCKxJy2r5AsYrsHeMBSLrGJCNpJqXgwczk=w300-h300-l90-rj"


def memory() -> tuple[float, float]:
    info = psutil.Process().memory_info()
    return info.rss / 1e6, getattr(info, "private", 0) / 1e6


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.02)


def main() -> int:
    route = sys.argv[1] if len(sys.argv) > 1 else "interop"
    app = QApplication(sys.argv[:1])
    helper = QWidget()
    hwnd = int(helper.winId())
    pump(0.5)
    before = memory()

    from winrt.windows.foundation import Uri
    from winrt.windows.media import (
        MediaPlaybackStatus,
        MediaPlaybackType,
        SystemMediaTransportControlsButton,
    )
    from winrt.windows.storage.streams import RandomAccessStreamReference

    player = None
    if route == "interop":
        from winrt.windows.media.interop import get_for_window

        smtc = get_for_window(hwnd)
    else:
        from winrt.windows.media.playback import MediaPlayer  # type: ignore[import-not-found]

        player = MediaPlayer()
        player.command_manager.is_enabled = False
        smtc = player.system_media_transport_controls
    pressed: list[str] = []
    smtc.add_button_pressed(lambda _s, args: pressed.append(str(args.button)))
    smtc.is_enabled = True
    smtc.is_play_enabled = smtc.is_pause_enabled = True
    smtc.is_next_enabled = smtc.is_previous_enabled = True
    updater = smtc.display_updater
    updater.type = MediaPlaybackType.MUSIC
    updater.music_properties.title = "Foolish Of Me"
    updater.music_properties.artist = "Seven Lions"
    updater.thumbnail = RandomAccessStreamReference.create_from_uri(Uri(COVER))
    updater.update()
    smtc.playback_status = MediaPlaybackStatus.PLAYING
    pump(4)
    after = memory()
    print(
        f"{route}: RSS {before[0]:.1f} -> {after[0]:.1f} MB (+{after[0] - before[0]:.1f}), "
        f"private {before[1]:.1f} -> {after[1]:.1f} MB (+{after[1] - before[1]:.1f}), "
        f"threads {psutil.Process().num_threads()}"
    )
    del player, smtc, updater, SystemMediaTransportControlsButton, app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
