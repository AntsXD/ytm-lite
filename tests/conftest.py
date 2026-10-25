"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from ytm_lite.app.api import Api
from ytm_lite.app.config import Paths, Settings
from ytm_lite.app.images import ImageLoader
from ytm_lite.app.library import Library
from ytm_lite.app.library_cache import LibraryCache
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.play_queue import PlayQueue
from ytm_lite.app.playback import PlaybackController
from ytm_lite.app.player import Player
from ytm_lite.app.resolver import ResolverClient
from ytm_lite.app.secrets import SecretStore
from ytm_lite.app.services import Services
from ytm_lite.app.session import Session
from ytm_lite.app.worker_client import WorkerProcess
from ytm_lite.app.workers import Workers


@pytest.fixture
def services(qapp: QApplication, tmp_path: Path) -> Services:
    """The app's real objects, signed out and with nothing started: no worker process, no
    mpv, no network (Home, which loads first, answers with nothing)."""
    paths = Paths(tmp_path / "config", tmp_path / "cache", tmp_path / "logs")
    for folder in (paths.config, paths.cache, paths.logs):
        folder.mkdir()
    settings = Settings()
    workers = Workers()
    worker = WorkerProcess(paths.cache)
    api = Api(worker)
    api.home = lambda: ()  # type: ignore[method-assign]
    resolver = ResolverClient(worker)
    session = Session(SecretStore(paths.config / "auth.bin"), api, resolver, workers, settings)
    liked = LikedIndex(api, workers)
    library = Library(api, workers, LibraryCache(paths.cache / "library"), liked, session)
    controller = PlaybackController(PlayQueue(), resolver, Player())
    return Services(
        paths,
        settings,
        workers,
        api,
        resolver,
        session,
        library,
        liked,
        ImageLoader(paths.cache),
        controller,
    )
