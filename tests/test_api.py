"""The app-side API proxy against a fake worker process."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from ytm_lite.app import codec
from ytm_lite.app.api import Api, ApiError, ApiErrorKind
from ytm_lite.app.auth import Cookie, Credentials
from ytm_lite.app.models import (
    Account,
    Album,
    Artist,
    ArtistSummary,
    Playlist,
    SearchResults,
    Track,
)
from ytm_lite.app.worker_client import WorkerError, WorkerProcess


class FakeWorker:
    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any] | None, bool, dict[str, str] | None]] = []
        self.answers: dict[str, Any] = {}

    def configure(self, **fields: Any) -> None:
        self.config.update(fields)

    def call_api(
        self,
        method: str,
        args: dict[str, Any] | None = None,
        *,
        background: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self.calls.append((method, args, background, headers))
        answer = self.answers[method]
        if isinstance(answer, Exception):
            raise answer
        return answer


CREDS = Credentials((Cookie("LOGIN_INFO", "l"), Cookie("__Secure-3PAPISID", "p")), "headers")


def make() -> tuple[Api, FakeWorker]:
    worker = FakeWorker()
    return Api(cast(WorkerProcess, worker)), worker


def test_signed_out_calls_fail_before_reaching_the_worker() -> None:
    api, worker = make()
    with pytest.raises(ApiError) as excinfo:
        api.library_playlists()
    assert excinfo.value.kind is ApiErrorKind.AUTH
    assert worker.calls == []


def test_credentials_are_sent_to_the_worker() -> None:
    api, worker = make()
    api.set_credentials(CREDS)
    assert api.signed_in
    assert worker.config["api_headers"]["cookie"] == "LOGIN_INFO=l; __Secure-3PAPISID=p"
    api.set_credentials(None)
    assert worker.config["api_headers"] is None and not api.signed_in


def test_results_become_models() -> None:
    api, worker = make()
    api.set_credentials(CREDS)
    playlist = Playlist("PL1", "Mix", (Track("v", "T"),), True, track_count=1)
    worker.answers["playlist"] = codec.playlist_to_json(playlist)
    worker.answers["account_info"] = codec.account_to_json(Account("Tester"))
    assert api.playlist("PL1", None, background=True) == playlist
    assert worker.calls[-1] == ("playlist", {"playlist_id": "PL1", "limit": None}, True, None)
    assert api.account_info_for(CREDS) == Account("Tester")
    assert worker.calls[-1][3] == CREDS.ytmusic_headers()


def test_history_needs_an_account_and_runs_in_the_background() -> None:
    api, worker = make()
    with pytest.raises(ApiError):
        api.add_history("v")
    assert worker.calls == []
    api.set_credentials(CREDS)
    worker.answers["add_history"] = None
    api.add_history("v")
    assert worker.calls[-1] == ("add_history", {"video_id": "v"}, True, None)


def test_radio_works_signed_out() -> None:
    api, worker = make()
    worker.answers["radio"] = [codec.track_to_json(Track("r1", "R1", duration=120))]
    assert api.radio("seed") == [Track("r1", "R1", duration=120)]
    assert worker.calls[-1] == ("radio", {"video_id": "seed"}, False, None)


def test_worker_errors_become_api_errors(tmp_path: Path) -> None:
    api, worker = make()
    api.set_credentials(CREDS)
    worker.answers["rate_song"] = WorkerError("auth", "Server returned HTTP 401")
    with pytest.raises(ApiError) as excinfo:
        api.rate_song("v", True)
    assert excinfo.value.kind is ApiErrorKind.AUTH


def test_search_album_and_artist_work_signed_out() -> None:
    api, worker = make()
    top = ArtistSummary("UCq", "Queen", thumbnail="https://t/q", audience="38M")
    results = SearchResults("q", None, top, songs=(Track("s", "S"),))
    worker.answers["search"] = codec.search_results_to_json(results)
    worker.answers["search_suggestions"] = ["queen", "queen bohemian rhapsody"]
    album = Album("MPREb_x", "Revival", (Track("a", "A"),), year="2017")
    worker.answers["album"] = codec.album_to_json(album)
    artist = Artist(
        "UCm", "Oasis", top_songs=(Track("z", "Z"),), songs_playlist_id="PLMp", audience="21M"
    )
    worker.answers["artist"] = codec.artist_to_json(artist)
    assert api.search("q") == results
    assert worker.calls[-1] == ("search", {"query": "q", "filter": None}, False, None)
    assert api.search_suggestions("que") == ["queen", "queen bohemian rhapsody"]
    assert api.album("MPREb_x") == album
    assert api.artist("UCm") == artist
