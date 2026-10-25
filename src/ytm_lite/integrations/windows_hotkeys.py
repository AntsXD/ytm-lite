"""Media keys as global hotkeys: the fallback when SMTC can't start (no OS overlay).

RegisterHotKey rather than a keyboard hook: it keeps working while a game has focus and
costs nothing per keystroke. The hotkeys belong to a hidden helper window; WM_HOTKEY
arrives through a native event filter.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray, QCoreApplication, Qt
from PySide6.QtWidgets import QWidget

from ytm_lite.integrations.base import MediaControls

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
_KEYS = {1: VK_MEDIA_PLAY_PAUSE, 2: VK_MEDIA_NEXT_TRACK, 3: VK_MEDIA_PREV_TRACK}


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hwnd: int, controls: HotkeyControls) -> None:
        super().__init__()
        self._hwnd = hwnd
        self._controls = controls

    def nativeEventFilter(
        self, event_type: QByteArray | bytes | bytearray | memoryview, message: int, /
    ) -> object:
        kind = event_type.data() if isinstance(event_type, QByteArray) else bytes(event_type)
        if kind == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.hWnd == self._hwnd:
                self._controls.pressed(int(msg.wParam))
                return True, 0
        return False, 0


class HotkeyControls(MediaControls):
    name = "hotkeys"

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._helper = QWidget()
        self._helper.setWindowFlag(Qt.WindowType.Tool)
        self._hwnd = int(self._helper.winId())
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        self._user32 = user32
        self._registered = [
            key_id
            for key_id, vk in _KEYS.items()
            if user32.RegisterHotKey(self._hwnd, key_id, MOD_NOREPEAT, vk)
        ]
        if not self._registered:
            raise OSError(ctypes.get_last_error(), "no media key could be registered")
        self._filter = _HotkeyFilter(self._hwnd, self)
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)
        log.info("media keys registered as hotkeys (%d of 3)", len(self._registered))

    def pressed(self, key_id: int) -> None:
        if key_id == 1:
            self.play_pause.emit()
        elif key_id == 2:
            self.next.emit()
        elif key_id == 3:
            self.previous.emit()

    def close(self) -> None:
        for key_id in self._registered:
            self._user32.UnregisterHotKey(self._hwnd, key_id)
        self._registered = []
        app = QCoreApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self._filter)
        self._helper.deleteLater()
