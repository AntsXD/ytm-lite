"""Which tracks are liked: drives every heart in the UI.

Built from the Liked Songs playlist. Toggling updates the UI at once and rolls back if
YouTube rejects the change.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QObject, Signal

from ytm_lite.app.api import Api
from ytm_lite.app.models import Track
from ytm_lite.app.workers import Workers


class LikedIndex(QObject):
    changed = Signal(str)  # a video id whose state changed
    reset = Signal()  # many changed at once
    failed = Signal(str)  # user-facing message

    def __init__(self, api: Api, workers: Workers, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._api = api
        self._workers = workers
        self._liked: set[str] = set()

    def is_liked(self, video_id: str) -> bool:
        return video_id in self._liked

    def replace(self, video_ids: Iterable[str]) -> None:
        self._liked = set(video_ids)
        self.reset.emit()

    def clear(self) -> None:
        self.replace(())

    def toggle(self, track: Track) -> None:
        self.set_liked(track, not self.is_liked(track.video_id))

    def set_liked(self, track: Track, liked: bool) -> None:
        video_id = track.video_id
        if liked == self.is_liked(video_id):
            return
        if not self._api.signed_in:
            self.failed.emit("Sign in to like songs")
            return
        self._mark(video_id, liked)

        def rollback(_error: Exception) -> None:
            self._mark(video_id, not liked)
            verb = "like" if liked else "unlike"
            self.failed.emit(f"Couldn't {verb} “{track.title}”")

        self._workers.submit(lambda: self._api.rate_song(video_id, liked), on_error=rollback)

    def _mark(self, video_id: str, liked: bool) -> None:
        if liked:
            self._liked.add(video_id)
        else:
            self._liked.discard(video_id)
        self.changed.emit(video_id)
