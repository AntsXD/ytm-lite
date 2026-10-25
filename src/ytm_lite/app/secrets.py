"""Credential storage: DPAPI-encrypted on Windows, an owner-only (0600) file elsewhere.

DPAPI ties the data to this Windows user account, so a copied file is useless on
another machine or account.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_MAGIC = b"YTML1\n"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    assert sys.platform == "win32"
    from ctypes import wintypes

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    func.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    func.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    ui_forbidden = 0x1
    if not func(
        ctypes.byref(blob_in), None, None, None, None, ui_forbidden, ctypes.byref(blob_out)
    ):
        raise OSError(ctypes.get_last_error() or "DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect(data: bytes) -> bytes:
    return _dpapi(data, protect=True) if sys.platform == "win32" else data


def unprotect(data: bytes) -> bytes:
    return _dpapi(data, protect=False) if sys.platform == "win32" else data


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, data: bytes) -> None:
        payload = _MAGIC + protect(data)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)
        if sys.platform != "win32":
            os.chmod(self.path, 0o600)

    def load(self) -> bytes | None:
        try:
            payload = self.path.read_bytes()
        except FileNotFoundError:
            return None
        if not payload.startswith(_MAGIC):
            log.warning("credential file has an unknown format; ignoring it")
            return None
        try:
            return unprotect(payload[len(_MAGIC) :])
        except OSError:
            log.warning("could not decrypt stored credentials (different user or machine?)")
            return None

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
