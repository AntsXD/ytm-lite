"""Give memory back to Windows when the app goes to the tray."""

from __future__ import annotations

import gc
import logging
import sys

log = logging.getLogger(__name__)


def trim_working_set() -> None:
    """Collect garbage, then ask Windows to page out this process's working set.

    Pages that are still in use fault back in as they're touched; the rest (the window's
    caches, decoded covers, startup code) stays out of RAM until it's needed. A no-op
    elsewhere.
    """
    gc.collect()
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    psapi.EmptyWorkingSet.restype = wintypes.BOOL
    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    if not psapi.EmptyWorkingSet(kernel32.GetCurrentProcess()):
        log.debug("EmptyWorkingSet failed (%s)", ctypes.get_last_error())
