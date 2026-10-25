"""Global hotkeys on Windows: RegisterHotKey on a hidden helper window.

RegisterHotKey rather than a keyboard hook: it keeps working while a game has focus and
costs nothing per keystroke. WM_HOTKEY arrives through a native event filter. Used for the
user's configured hotkeys, and for the media keys when the media overlay can't start.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QByteArray,
    QCoreApplication,
    QObject,
    Qt,
    Signal,
)
from PySide6.QtWidgets import QWidget

from ytm_lite.integrations.base import MediaControls

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hwnd: int, hotkeys: GlobalHotkeys) -> None:
        super().__init__()
        self._hwnd = hwnd
        self._hotkeys = hotkeys

    def nativeEventFilter(
        self, event_type: QByteArray | bytes | bytearray | memoryview, message: int, /
    ) -> object:
        kind = event_type.data() if isinstance(event_type, QByteArray) else bytes(event_type)
        if kind == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.hWnd == self._hwnd:
                self._hotkeys.pressed(int(msg.wParam))
                return True, 0
        return False, 0


class GlobalHotkeys(QObject):
    triggered = Signal(str)  # the name a hotkey was registered under

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._helper = QWidget()
        self._helper.setWindowFlag(Qt.WindowType.Tool)
        self.hwnd = int(self._helper.winId())
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        self._user32 = user32
        self._names: dict[int, str] = {}
        self._filter = _HotkeyFilter(self.hwnd, self)
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    def register(self, name: str, modifiers: int, vk: int) -> bool:
        """False if another app (or Windows) already owns this key combination."""
        key_id = max(self._names, default=0) + 1
        if not self._user32.RegisterHotKey(self.hwnd, key_id, modifiers | MOD_NOREPEAT, vk):
            log.info("hotkey %s unavailable (error %d)", name, ctypes.get_last_error())
            return False
        self._names[key_id] = name
        return True

    def unregister_all(self) -> None:
        for key_id in self._names:
            self._user32.UnregisterHotKey(self.hwnd, key_id)
        self._names.clear()

    def pressed(self, key_id: int) -> None:
        name = self._names.get(key_id)
        if name is not None:
            self.triggered.emit(name)

    def close(self) -> None:
        self.unregister_all()
        app = QCoreApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self._filter)
        self._helper.deleteLater()


class HotkeyControls(MediaControls):
    """The media keys, when the media overlay (SMTC) can't take them."""

    name = "hotkeys"

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.keys = GlobalHotkeys(self)
        keys = (
            ("play_pause", VK_MEDIA_PLAY_PAUSE),
            ("next", VK_MEDIA_NEXT_TRACK),
            ("previous", VK_MEDIA_PREV_TRACK),
        )
        registered = [name for name, vk in keys if self.keys.register(name, 0, vk)]
        if not registered:
            self.keys.close()
            raise OSError("no media key could be registered")
        self.keys.triggered.connect(self._pressed)
        log.info("media keys registered as hotkeys (%d of 3)", len(registered))

    def _pressed(self, name: str) -> None:
        signal = {"play_pause": self.play_pause, "next": self.next, "previous": self.previous}
        signal[name].emit()

    def close(self) -> None:
        self.keys.close()
