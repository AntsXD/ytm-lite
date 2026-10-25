"""Update yt-dlp without restarting the app (YouTube breaks it every few weeks).

The worker downloads the newest yt-dlp (and the yt-dlp-ejs it pins) from PyPI into
`<cache>/updates/<version>`, checks the files against PyPI's checksums and that the new
version imports; the worker is then restarted with that folder first on its path, and
the music keeps playing. The yt-dlp that came with the app stays as the fallback: if
the folder goes missing, the bundled one is used again.
"""

from __future__ import annotations

import json
import logging
import shutil
from importlib import metadata
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ytm_lite.app.api import Api, ApiError, ApiErrorKind
from ytm_lite.app.config import app_root, atomic_write_text, is_frozen, read_json
from ytm_lite.app.worker_client import WorkerProcess
from ytm_lite.app.workers import Workers

log = logging.getLogger(__name__)

COMPLETE = ".complete"  # written last by ytm_worker.update


def bundled_version() -> str:
    """The yt-dlp the app came with (read from its metadata; nothing is imported)."""
    if not is_frozen():
        try:
            return metadata.version("yt-dlp")
        except metadata.PackageNotFoundError:
            return "missing"
    site = app_root() / "worker" / "Lib" / "site-packages"
    for dist in metadata.distributions(path=[str(site)]):
        if dist.metadata["Name"] == "yt-dlp":
            return str(dist.version)
    return "missing"


class YtDlpUpdater(QObject):
    status = Signal(str)  # progress and results, for whoever is showing them
    updated = Signal(str)  # the new version is in use

    def __init__(
        self,
        folder: Path,
        worker: WorkerProcess,
        api: Api,
        workers: Workers,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._folder = folder
        self._state_file = folder / "active.json"
        self._worker = worker
        self._api = api
        self._workers = workers
        self.busy = False
        self._active = self._load_active()
        self._remove_others()  # older downloads; never while a worker might be using one
        worker.set_packages(self._active_path())

    def version(self) -> str:
        if self._active:
            return self._active
        reported = (self._worker.info.get("versions") or {}).get("yt-dlp")
        return str(reported or bundled_version())

    def check(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.emit("Checking PyPI for a newer yt-dlp…")
        folder, current = str(self._folder), self.version()
        self._workers.submit(
            lambda: self._api.update_ytdlp(folder, current),
            on_done=self._checked,
            on_error=self._failed,
        )

    def _checked(self, result: dict[str, object]) -> None:
        self.busy = False
        version = str(result.get("version") or "")
        if result.get("status") != "installed" or not version:
            self.status.emit(f"yt-dlp {self.version()} is the newest version.")
            return
        self._active = version
        atomic_write_text(self._state_file, json.dumps({"version": version}))
        self._worker.set_packages(self._active_path())
        log.info("now using yt-dlp %s", version)
        self.status.emit(f"Updated to yt-dlp {version}. Playback wasn't interrupted.")
        self.updated.emit(version)

    def _failed(self, error: Exception) -> None:
        self.busy = False
        if isinstance(error, ApiError) and error.kind is ApiErrorKind.NETWORK:
            reason = "PyPI couldn't be reached"
        else:
            reason = str(error)[:200] or type(error).__name__
        log.warning("yt-dlp update failed: %s", reason)
        self.status.emit(f"Couldn't update yt-dlp: {reason}.")

    def _load_active(self) -> str | None:
        state = read_json(self._state_file)
        version = state.get("version") if isinstance(state, dict) else None
        if isinstance(version, str) and (self._folder / version / COMPLETE).exists():
            return version
        return None

    def _active_path(self) -> Path | None:
        return self._folder / self._active if self._active else None

    def _remove_others(self) -> None:
        for entry in self._folder.iterdir() if self._folder.exists() else ():
            if entry.is_dir() and entry.name != self._active:
                shutil.rmtree(entry, ignore_errors=True)
