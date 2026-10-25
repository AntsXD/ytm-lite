"""The ytmusicapi side of the worker (ytm_worker.ytm), with fake clients: no network."""

from __future__ import annotations

import time
from typing import Any

import pytest
import requests
from ytmusicapi.exceptions import YTMusicServerError, YTMusicUserError

from ytm_worker.protocol import ApiErrorKind
from ytm_worker.ytm import Gate, ServiceError, YtmService, classify


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (YTMusicServerError("Server returned HTTP 401: Unauthorized.\nx"), ApiErrorKind.AUTH),
        (YTMusicServerError("Server returned HTTP 403: Forbidden.\nx"), ApiErrorKind.AUTH),
        (YTMusicServerError("Server returned HTTP 500: Error.\nx"), ApiErrorKind.SERVER),
        (
            YTMusicUserError("Please provide authentication before using this function"),
            ApiErrorKind.AUTH,
        ),
        (requests.ConnectionError("down"), ApiErrorKind.NETWORK),
        (KeyError("Unable to find 'x' using path [...] on {huge json}"), ApiErrorKind.PARSE),
        (RuntimeError("??"), ApiErrorKind.SERVER),
    ],
)
def test_classify(exc: Exception, kind: ApiErrorKind) -> None:
    error = classify(exc)
    assert error.kind is kind
    assert len(str(error)) <= 300


def test_gate_spaces_background_calls_only() -> None:
    gate = Gate(interval=0.05)
    started = time.monotonic()
    for _ in range(3):
        gate.pass_through()  # foreground: no waiting
    assert time.monotonic() - started < 0.04
    gate.local.background = True
    started = time.monotonic()
    for _ in range(3):
        gate.pass_through()
    assert time.monotonic() - started >= 0.09  # two full intervals between three calls


class FakeClient:
    def __init__(self, headers: dict[str, str] | None) -> None:
        self.headers = headers
        self.__dict__["base_headers"] = {"X-Goog-Visitor-Id": "visitor-1"}

    def get_account_info(self) -> dict[str, Any]:
        return {"accountName": "Tester", "channelHandle": "@t", "accountPhotoUrl": None}

    def get_playlist(self, playlist_id: str, limit: int | None) -> dict[str, Any]:
        tracks = [{"videoId": f"v{i}", "title": f"T{i}"} for i in range(3)]
        return {"title": "Mix", "trackCount": 3, "tracks": tracks}


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> tuple[YtmService, list[Any]]:
    svc = YtmService()
    created: list[Any] = []

    def fake_create(headers: dict[str, str] | None) -> FakeClient:
        client = FakeClient(headers)
        created.append(client)
        return client

    monkeypatch.setattr(svc, "_create", fake_create)
    return svc, created


def test_calls_return_compact_json(service: tuple[YtmService, list[Any]]) -> None:
    svc, _ = service
    assert svc.call("account_info", {}) == {"n": "Tester", "h": "@t", "p": None}
    playlist = svc.call("playlist", {"playlist_id": "PL1", "limit": None})
    assert playlist["id"] == "PL1" and playlist["x"] == 1
    assert [t["v"] for t in playlist["tr"]] == ["v0", "v1", "v2"]


def test_pool_reuse_new_credentials_and_one_off_headers(
    service: tuple[YtmService, list[Any]],
) -> None:
    svc, created = service
    seen: list[str] = []
    svc.on_visitor = seen.append
    svc.call("account_info", {})
    svc.call("account_info", {})
    assert len(created) == 1 and seen == ["visitor-1"]
    svc.configure({"cookie": "a=b"}, None)
    svc.call("account_info", {})
    assert len(created) == 2 and created[-1].headers == {"cookie": "a=b"}
    svc.call("account_info", {}, headers={"cookie": "new"})  # validating other credentials
    assert len(created) == 3 and created[-1].headers == {"cookie": "new"}
    svc.call("account_info", {})
    assert len(created) == 3  # the pooled signed-in client was reused


def test_errors(service: tuple[YtmService, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _ = service
    with pytest.raises(ServiceError) as excinfo:
        svc.call("nope", {})
    assert excinfo.value.kind is ApiErrorKind.SERVER

    def broken(self: FakeClient) -> dict[str, Any]:
        return {}  # no accountName: a signed-out answer

    monkeypatch.setattr(FakeClient, "get_account_info", broken)
    assert svc.call("account_info", {})["n"] == "YouTube Music user"

    def down(self: FakeClient) -> dict[str, Any]:
        raise requests.Timeout("slow")

    monkeypatch.setattr(FakeClient, "get_account_info", down)
    with pytest.raises(ServiceError) as excinfo:
        svc.call("account_info", {})
    assert excinfo.value.kind is ApiErrorKind.NETWORK
