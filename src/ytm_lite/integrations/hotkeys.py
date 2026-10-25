"""The user's global hotkeys (Settings → Hotkeys): play/pause, next, show the window...

Key sequences are stored as Qt writes them ("Ctrl+Alt+P") and registered with Windows
(RegisterHotKey), so they work while a game has focus. Other platforms have no portable
way to grab keys globally; desktop shortcuts can run `ytm-lite --play-pause` instead.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

if TYPE_CHECKING:
    from ytm_lite.integrations.windows_hotkeys import GlobalHotkeys

log = logging.getLogger(__name__)

SUPPORTED = sys.platform == "win32"

ACTION_LABELS = {
    "play_pause": "Play / pause",
    "next": "Next song",
    "previous": "Previous song",
    "like": "Like / unlike",
    "toggle_window": "Show / hide the window",
    "mini_player": "Mini player",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
}

_MOD_ALT, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN = 0x1, 0x2, 0x4, 0x8
_K = Qt.Key
_SPECIAL_KEYS: dict[Qt.Key, int] = {
    _K.Key_Space: 0x20,
    _K.Key_PageUp: 0x21,
    _K.Key_PageDown: 0x22,
    _K.Key_End: 0x23,
    _K.Key_Home: 0x24,
    _K.Key_Left: 0x25,
    _K.Key_Up: 0x26,
    _K.Key_Right: 0x27,
    _K.Key_Down: 0x28,
    _K.Key_Insert: 0x2D,
    _K.Key_Delete: 0x2E,
    _K.Key_Pause: 0x13,
    _K.Key_Print: 0x2C,
    _K.Key_Return: 0x0D,
    _K.Key_Enter: 0x0D,
    _K.Key_Tab: 0x09,
    _K.Key_Backspace: 0x08,
    _K.Key_Semicolon: 0xBA,
    _K.Key_Equal: 0xBB,
    _K.Key_Comma: 0xBC,
    _K.Key_Minus: 0xBD,
    _K.Key_Period: 0xBE,
    _K.Key_Slash: 0xBF,
    _K.Key_QuoteLeft: 0xC0,
    _K.Key_BracketLeft: 0xDB,
    _K.Key_Backslash: 0xDC,
    _K.Key_BracketRight: 0xDD,
    _K.Key_Apostrophe: 0xDE,
    _K.Key_VolumeMute: 0xAD,
    _K.Key_VolumeDown: 0xAE,
    _K.Key_VolumeUp: 0xAF,
    _K.Key_MediaNext: 0xB0,
    _K.Key_MediaPrevious: 0xB1,
    _K.Key_MediaStop: 0xB2,
    _K.Key_MediaPlay: 0xB3,
    _K.Key_MediaTogglePlayPause: 0xB3,
}
# Media and volume keys may be used on their own (so may F-keys); anything else needs a
# modifier, or it would be taken away from every other app.
_STANDALONE = {key for key, vk in _SPECIAL_KEYS.items() if 0xAD <= vk <= 0xB3}


def to_native(sequence: str) -> tuple[int, int] | None:
    """(Windows modifiers, virtual-key code) for a key sequence such as "Ctrl+Alt+P", or
    None if Windows can't register it as a global hotkey."""
    keys = QKeySequence.fromString(sequence, QKeySequence.SequenceFormat.PortableText)
    if keys.count() != 1:
        return None
    combo = keys[0]  # type: ignore[index]  # missing from the stubs, works at runtime
    key = combo.key()
    mods = combo.keyboardModifiers()
    native = 0
    if mods & Qt.KeyboardModifier.ControlModifier:
        native |= _MOD_CONTROL
    if mods & Qt.KeyboardModifier.AltModifier:
        native |= _MOD_ALT
    if mods & Qt.KeyboardModifier.ShiftModifier:
        native |= _MOD_SHIFT
    if mods & Qt.KeyboardModifier.MetaModifier:
        native |= _MOD_WIN
    code = key.value
    if _K.Key_A.value <= code <= _K.Key_Z.value or _K.Key_0.value <= code <= _K.Key_9.value:
        vk = code  # letters and digits share their ASCII codes
        if mods & Qt.KeyboardModifier.KeypadModifier and _K.Key_0.value <= code <= _K.Key_9.value:
            vk = 0x60 + code - _K.Key_0.value  # VK_NUMPAD0..9
    elif _K.Key_F1.value <= code <= _K.Key_F24.value:
        vk = 0x70 + code - _K.Key_F1.value  # VK_F1..F24: fine on their own too
        return native, vk
    elif key in _SPECIAL_KEYS:
        vk = _SPECIAL_KEYS[key]
        if key in _STANDALONE:
            return native, vk
    else:
        return None
    if native & (_MOD_CONTROL | _MOD_ALT | _MOD_WIN) == 0:
        return None  # e.g. "P" or "Shift+P" would stop that key working everywhere else
    return native, vk


class Hotkeys(QObject):
    """Registers the configured hotkeys and says which action was pressed."""

    triggered = Signal(str)  # an ACTION_LABELS key

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keys: GlobalHotkeys | None = None  # made once a hotkey is set

    def apply(self, hotkeys: dict[str, str]) -> dict[str, str]:
        """Register `hotkeys` (action -> key sequence) in place of the current ones;
        returns the actions that couldn't be registered, with the reason."""
        problems: dict[str, str] = {}
        wanted = {a: k for a, k in hotkeys.items() if a in ACTION_LABELS and k}
        if not SUPPORTED:
            return dict.fromkeys(wanted, "Global hotkeys work on Windows only")
        from ytm_lite.integrations.windows_hotkeys import GlobalHotkeys

        if self._keys is not None:
            self._keys.unregister_all()
        if not wanted:
            return problems
        if self._keys is None:
            self._keys = GlobalHotkeys(self)
            self._keys.triggered.connect(self.triggered)
        for action, sequence in wanted.items():
            native = to_native(sequence)
            if native is None:
                problems[action] = "Add Ctrl, Alt or Win to this key"
            elif not self._keys.register(action, *native):
                problems[action] = "Another app already uses this key"
        if problems:
            log.info("hotkeys not registered: %s", ", ".join(sorted(problems)))
        return problems

    def close(self) -> None:
        if self._keys is not None:
            self._keys.close()
            self._keys = None
