"""Spike S3: what a Qt window costs, and what tray mode (destroy window + trim) gives back.

Usage: uv run python tools/spikes/s3_qt_tray.py
"""

from __future__ import annotations

import ctypes
import gc
import sys

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QMainWindow, QStyle, QSystemTrayIcon, QTableView


def snap(label: str) -> None:
    mem = psutil.Process().memory_info()
    private = getattr(mem, "private", 0)
    print(f"   {label:<40} RSS {mem.rss / 1e6:6.1f} MB  private {private / 1e6:6.1f} MB")


def trim() -> None:
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.windll.psapi
        psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        psapi.EmptyWorkingSet.restype = wintypes.BOOL
        if not psapi.EmptyWorkingSet(kernel32.GetCurrentProcess()):
            print("   EmptyWorkingSet failed:", ctypes.GetLastError())


def make_window() -> QMainWindow:
    window = QMainWindow()
    model = QStandardItemModel(200, 5, window)
    for row in range(200):
        for col, text in enumerate((str(row + 1), f"Track title {row}", "Artist", "Album", "3:45")):
            model.setItem(row, col, QStandardItem(text))
    view = QTableView()
    view.setModel(model)
    window.setCentralWidget(view)
    window.resize(1100, 700)
    return window


def main() -> int:
    snap("python + psutil")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # the tray keeps the app alive
    app.setStyle("Fusion")
    snap("QApplication")
    tray = QSystemTrayIcon(QIcon(app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)))
    tray.show()
    window = make_window()
    window.show()
    steps: list[tuple[int, object]] = []

    def step_shown() -> None:
        snap("window + 200-row table + tray, shown")
        window.hide()

    def step_hidden() -> None:
        snap("window hidden (not destroyed)")
        trim()
        snap("hidden + EmptyWorkingSet")
        window.show()

    def step_destroy() -> None:
        snap("shown again")
        window.close()
        window.deleteLater()

    def step_destroyed() -> None:
        gc.collect()
        snap("window destroyed")
        trim()
        snap("destroyed + EmptyWorkingSet")

    def step_rebuild() -> None:
        global rebuilt
        rebuilt = make_window()
        rebuilt.show()

    def step_rebuilt() -> None:
        snap("window rebuilt and shown")
        app.quit()

    for delay, fn in (
        (1500, step_shown),
        (2500, step_hidden),
        (4000, step_destroy),
        (5000, step_destroyed),
        (7000, step_rebuild),
        (8500, step_rebuilt),
    ):
        QTimer.singleShot(delay, fn)
        steps.append((delay, fn))
    app.exec()
    return 0


rebuilt: QMainWindow | None = None

if __name__ == "__main__":
    raise SystemExit(main())
