"""The long-lived objects the UI talks to, created once in main.py."""

from __future__ import annotations

from dataclasses import dataclass

from ytm_lite.app.api import Api
from ytm_lite.app.config import Paths, Settings
from ytm_lite.app.images import ImageLoader
from ytm_lite.app.library import Library
from ytm_lite.app.liked import LikedIndex
from ytm_lite.app.playback import PlaybackController
from ytm_lite.app.resolver import ResolverClient
from ytm_lite.app.session import Session
from ytm_lite.app.workers import Workers


@dataclass(frozen=True, slots=True)
class Services:
    paths: Paths
    settings: Settings
    workers: Workers
    api: Api
    resolver: ResolverClient
    session: Session
    library: Library
    liked: LikedIndex
    images: ImageLoader
    controller: PlaybackController
