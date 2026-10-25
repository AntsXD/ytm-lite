"""Single instance, the media bridge, the media-key hotkey fallback, and MPRIS metadata
(the D-Bus side was checked over a real session bus in WSL; see linux_mpris)."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from test_playback import FakePlayer, FakeResolver
from ytm_lite.app.images import ImageLoader
from ytm_lite.app.models import AlbumRef, ArtistRef, Track
from ytm_lite.app.play_queue import PlayQueue, RepeatMode
from ytm_lite.app.playback import PlaybackController, PlaybackState
from ytm_lite.app.single_instance import SingleInstance, server_name
from ytm_lite.integrations.base import MediaControls
from ytm_lite.integrations.hotkeys import Hotkeys, to_native
from ytm_lite.integrations.linux_mpris import NO_TRACK, metadata, track_path
from ytm_lite.integrations.media_controls import MediaBridge, jpeg_bytes

SECOND_LAUNCH = """
import sys
from PySide6.QtCore import QCoreApplication
from ytm_lite.app.single_instance import SingleInstance
app = QCoreApplication([])
sys.exit(0 if SingleInstance(sys.argv[1]).forward(sys.argv[2]) else 3)
"""


def test_second_launch_hands_its_command_over(qapp: QApplication, qtbot: QtBot) -> None:
    name = f"ytm-lite-test-{uuid.uuid4().hex[:8]}"
    running = SingleInstance(name)
    assert running.listen()
    received: list[str] = []
    running.command_received.connect(received.append)
    second = subprocess.Popen([sys.executable, "-c", SECOND_LAUNCH, name, "next"])
    qtbot.waitUntil(lambda: received == ["next"], timeout=10_000)
    assert second.wait(10) == 0
    assert not SingleInstance(f"{name}-nobody").forward("show")


def test_server_name_is_per_profile(tmp_path: Path) -> None:
    assert server_name(tmp_path / "a") != server_name(tmp_path / "b")
    assert server_name(tmp_path / "a") == server_name(tmp_path / "a")


class RecordingControls(MediaControls):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.calls: list[tuple[str, object]] = []

    def update_track(self, track: Track | None) -> None:
        self.calls.append(("track", track.title if track else None))

    def update_state(self, state: PlaybackState) -> None:
        self.calls.append(("state", state))

    def update_timeline(self, position: float, duration: float) -> None:
        self.calls.append(("timeline", (position, duration)))


def test_bridge_follows_the_controller_and_back(qapp: QApplication, tmp_path: Path) -> None:
    player = FakePlayer()
    resolver = FakeResolver(tmp_path)
    controller = PlaybackController(PlayQueue(), resolver, player)
    controls = RecordingControls()
    bridge = MediaBridge(controls, controller, ImageLoader(tmp_path))
    entries = [
        (Track("a", "A", duration=100), "context"),
        (Track("b", "B", duration=100), "context"),
    ]
    controller.restore(entries, 0, None, False, RepeatMode.OFF, 12.0)
    assert ("track", "A") in controls.calls
    assert ("state", PlaybackState.PAUSED) in controls.calls
    assert ("timeline", (12.0, 100.0)) in controls.calls

    controls.next.emit()  # a media key
    current = controller.queue.current()
    assert current is not None and current.track.video_id == "b"
    controls.play.emit()
    assert resolver.requests[-1][0] == "b"

    # Playback restarts on a new track (gapless: no state change) or after a seek.
    player.is_active, player.pos, player.dur = True, 0.0, 250.0
    player.playback_restarted.emit()
    assert controls.calls[-1] == ("timeline", (0.0, 250.0))
    del bridge


WINRT_IMPORT = """
import sys
import PySide6.QtWidgets  # its import hook inspects every imported module
from ytm_lite.integrations.windows_smtc import _import_winrt
_import_winrt()
import winrt.windows.media.interop
assert "asyncio" not in sys.modules and "ssl" not in sys.modules
import winrt.runtime._internals as internals
assert callable(internals.asyncio.get_running_loop)  # the real one, on demand
assert "asyncio" in sys.modules
"""


@pytest.mark.skipif(sys.platform != "win32", reason="pywinrt")
def test_winrt_loads_without_asyncio() -> None:
    result = subprocess.run(
        [sys.executable, "-c", WINRT_IMPORT], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hotkeys")
def test_media_key_hotkeys(qapp: QApplication, qtbot: QtBot) -> None:
    import ctypes

    from ytm_lite.integrations.windows_hotkeys import WM_HOTKEY, HotkeyControls

    try:
        controls = HotkeyControls()
    except OSError:
        pytest.skip("another app holds the media keys")
    pressed: list[str] = []
    controls.next.connect(lambda: pressed.append("next"))
    controls.play_pause.connect(lambda: pressed.append("play_pause"))
    post = ctypes.windll.user32.PostMessageW
    post.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    post(controls.keys.hwnd, WM_HOTKEY, 2, 0)  # what Windows sends for the Next key
    post(controls.keys.hwnd, WM_HOTKEY, 1, 0)
    qtbot.waitUntil(lambda: pressed == ["next", "play_pause"], timeout=2000)
    controls.close()


def test_key_sequences_to_windows_hotkeys() -> None:
    assert to_native("Ctrl+Alt+P") == (0x2 | 0x1, 0x50)
    assert to_native("Ctrl+Shift+Right") == (0x2 | 0x4, 0x27)
    assert to_native("Meta+F9") == (0x8, 0x78)
    assert to_native("F9") == (0, 0x78)  # F-keys and media keys may stand alone
    assert to_native("Media Play") == (0, 0xB3)
    assert to_native("Ctrl+Alt+7") == (0x3, 0x37)
    assert to_native("P") is None  # would take P away from every other app
    assert to_native("Shift+P") is None
    assert to_native("Ctrl+A, Ctrl+B") is None  # chords can't be global hotkeys
    assert to_native("") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hotkeys")
def test_configured_hotkeys(qapp: QApplication, qtbot: QtBot) -> None:
    import ctypes

    from ytm_lite.integrations.windows_hotkeys import WM_HOTKEY

    hotkeys = Hotkeys()
    pressed: list[str] = []
    hotkeys.triggered.connect(pressed.append)
    problems = hotkeys.apply({"next": "Ctrl+Alt+Shift+F11", "like": "L", "bogus": "Ctrl+Alt+B"})
    assert problems == {"like": "Add Ctrl, Alt or Win to this key"}
    keys = hotkeys._keys
    assert keys is not None
    post = ctypes.windll.user32.PostMessageW
    post.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    post(keys.hwnd, WM_HOTKEY, 1, 0)
    qtbot.waitUntil(lambda: pressed == ["next"], timeout=2000)
    # A second registration of the same combination fails: that's another app's key now.
    other = Hotkeys()
    assert other.apply({"play_pause": "Ctrl+Alt+Shift+F11"}) == {
        "play_pause": "Another app already uses this key"
    }
    assert hotkeys.apply({}) == {}  # cleared: now the other one can have it
    assert other.apply({"play_pause": "Ctrl+Alt+Shift+F11"}) == {}
    other.close()
    hotkeys.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_shortcut_carries_the_app_id(tmp_path: Path) -> None:
    from ytm_lite.integrations.windows_shortcut import APP_ID, read_shortcut, write_shortcut

    link = tmp_path / "Start Menu" / "ytm-lite.lnk"
    target = Path(sys.executable)
    write_shortcut(link, target, description="test", icon=target)
    assert read_shortcut(link) == (target, APP_ID)
    write_shortcut(link, target, app_id="Other.app")  # replaced in place
    assert read_shortcut(link) == (target, "Other.app")


def test_jpeg_bytes(qapp: QApplication) -> None:
    pixmap = QPixmap(8, 8)
    pixmap.fill(QColor("#8b5cf6"))
    assert jpeg_bytes(pixmap)[:3] == b"\xff\xd8\xff"


def test_mpris_metadata() -> None:
    track = Track(
        "abc-DEF_1",
        "Title",
        (ArtistRef("One"), ArtistRef("Two")),
        AlbumRef("Album"),
        180,
        "https://lh3.googleusercontent.com/x=w60-h60",
    )
    data = metadata(track)
    assert data["mpris:trackid"].path() == track_path(track)
    assert track_path(track).endswith("/abc_DEF_1")  # only [A-Za-z0-9_] in object paths
    assert data["xesam:artist"] == ["One", "Two"]
    assert data["xesam:album"] == "Album"
    assert data["mpris:length"] == 180_000_000
    assert data["mpris:artUrl"].endswith("=w300-h300-l90-rj")
    assert metadata(None)["mpris:trackid"].path() == NO_TRACK
