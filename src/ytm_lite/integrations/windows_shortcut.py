"""App identity on Windows: an AppUserModelID for the process, and a Start Menu shortcut
carrying it. That shortcut is how Windows finds the name and icon to show for the app in
the media overlay (instead of "python.exe"), in notifications and on the taskbar.

The shortcut is written with the shell's own COM objects (IShellLink, IPropertyStore,
IPersistFile) through ctypes, so no extra package is needed. Failed calls raise OSError
(ctypes turns HRESULT failures into exceptions).
"""

from __future__ import annotations

import ctypes
import logging
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_ID = "AntsXD.ytm-lite"
SHORTCUT_NAME = "ytm-lite.lnk"

_CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
_IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
_IID_PROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
_IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"
_PKEY_APP_USER_MODEL_ID = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"  # pid 5
_VT_LPWSTR = 31
_CLSCTX_INPROC_SERVER = 0x1
_COINIT_APARTMENTTHREADED = 0x2

# IShellLinkW, IPropertyStore and IPersistFile vtable slots used here.
_LINK_GET_PATH, _LINK_SET_DESCRIPTION, _LINK_SET_WORKING_DIRECTORY = 3, 7, 9
_LINK_SET_ICON_LOCATION, _LINK_SET_PATH = 17, 20
_STORE_GET_VALUE, _STORE_SET_VALUE, _STORE_COMMIT = 5, 6, 7
_FILE_LOAD, _FILE_SAVE = 5, 6


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def of(cls, text: str) -> _GUID:
        value = uuid.UUID(text)
        guid = cls(value.time_low, value.time_mid, value.time_hi_version)
        guid.Data4[:] = list(value.bytes[8:])
        return guid


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PropVariant(ctypes.Structure):
    # vt, three reserved words, then the value; 24 bytes on 64-bit Windows.
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("reserved", ctypes.c_ushort * 3),
        ("pwszVal", ctypes.c_void_p),
        ("padding", ctypes.c_void_p),
    ]


_APP_ID_KEY = _PropertyKey(_GUID.of(_PKEY_APP_USER_MODEL_ID), 5)


def _method(pointer: ctypes.c_void_p, index: int, *argtypes: Any) -> Callable[..., int]:
    """Slot `index` of a COM object's vtable, bound to the object."""
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])
    return lambda *args: function(pointer, *args)


@contextmanager
def _interface(link: ctypes.c_void_p, iid: str) -> Iterator[ctypes.c_void_p]:
    out = ctypes.c_void_p()
    query = _method(link, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
    query(ctypes.byref(_GUID.of(iid)), ctypes.byref(out))
    try:
        yield out
    finally:
        _method(out, 2)()  # Release


@contextmanager
def _shell_link() -> Iterator[ctypes.c_void_p]:
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.restype = ctypes.HRESULT
    ole32.CoCreateInstance.restype = ctypes.HRESULT
    try:
        ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        initialized = True
    except OSError:  # already initialized differently on this thread: COM works anyway
        initialized = False
    link = ctypes.c_void_p()
    try:
        ole32.CoCreateInstance(
            ctypes.byref(_GUID.of(_CLSID_SHELL_LINK)),
            None,
            _CLSCTX_INPROC_SERVER,
            ctypes.byref(_GUID.of(_IID_SHELL_LINK_W)),
            ctypes.byref(link),
        )
        yield link
    finally:
        if link:
            _method(link, 2)()  # Release
        if initialized:
            ole32.CoUninitialize()


def _load(link: ctypes.c_void_p, path: Path) -> None:
    with _interface(link, _IID_PERSIST_FILE) as persist:
        _method(persist, _FILE_LOAD, wintypes.LPCWSTR, wintypes.DWORD)(str(path), 0)


def set_process_app_id(app_id: str = APP_ID) -> None:
    """Call before any window exists."""
    shell32 = ctypes.windll.shell32
    shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def start_menu_folder() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def write_shortcut(
    path: Path,
    target: Path,
    *,
    app_id: str = APP_ID,
    description: str = "",
    icon: Path | None = None,
) -> None:
    """Create or replace a .lnk to `target` that carries `app_id`."""
    with _shell_link() as link:
        _method(link, _LINK_SET_PATH, wintypes.LPCWSTR)(str(target))
        _method(link, _LINK_SET_WORKING_DIRECTORY, wintypes.LPCWSTR)(str(target.parent))
        if description:
            _method(link, _LINK_SET_DESCRIPTION, wintypes.LPCWSTR)(description)
        if icon is not None:
            _method(link, _LINK_SET_ICON_LOCATION, wintypes.LPCWSTR, ctypes.c_int)(str(icon), 0)
        with _interface(link, _IID_PROPERTY_STORE) as store:
            text = ctypes.create_unicode_buffer(app_id)
            value = _PropVariant(vt=_VT_LPWSTR, pwszVal=ctypes.addressof(text))
            _method(
                store, _STORE_SET_VALUE, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant)
            )(ctypes.byref(_APP_ID_KEY), ctypes.byref(value))
            _method(store, _STORE_COMMIT)()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _interface(link, _IID_PERSIST_FILE) as persist:
            _method(persist, _FILE_SAVE, wintypes.LPCWSTR, wintypes.BOOL)(str(path), True)


def read_shortcut(path: Path) -> tuple[Path | None, str | None]:
    """A shortcut's target and AppUserModelID."""
    with _shell_link() as link:
        _load(link, path)
        buffer = ctypes.create_unicode_buffer(1024)
        _method(
            link, _LINK_GET_PATH, wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        )(buffer, len(buffer), None, 0)
        target = Path(buffer.value) if buffer.value else None
        with _interface(link, _IID_PROPERTY_STORE) as store:
            value = _PropVariant()
            _method(
                store, _STORE_GET_VALUE, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant)
            )(ctypes.byref(_APP_ID_KEY), ctypes.byref(value))
            app_id = ctypes.wstring_at(value.pwszVal) if value.vt == _VT_LPWSTR else None
            ctypes.windll.ole32.PropVariantClear(ctypes.byref(value))
    return target, app_id


def ensure_start_menu_shortcut(target: Path, icon: Path | None = None) -> None:
    """The packaged app's Start Menu entry, pointing at wherever the app is now."""
    path = start_menu_folder() / SHORTCUT_NAME
    try:
        if path.exists() and read_shortcut(path) == (target, APP_ID):
            return
        write_shortcut(path, target, description="Lightweight YouTube Music player", icon=icon)
        log.info("Start Menu shortcut written")
    except OSError:
        log.warning("couldn't write the Start Menu shortcut", exc_info=True)
