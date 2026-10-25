from __future__ import annotations

from typing import Any

import pytest

from ytm_worker.jsruntime import JsRuntime
from ytm_worker.protocol import ErrorKind
from ytm_worker.resolve import (
    ANONYMOUS_CLIENTS,
    FALLBACK_LIFETIME,
    SIGNED_IN_CLIENTS,
    ResolveError,
    Resolver,
    ResolverConfig,
    classify,
    parse_expiry,
    stream_from_info,
)

URL = "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1761400000&ei=abc&itag=251"


def test_parse_expiry_query_param() -> None:
    assert parse_expiry(URL) == 1761400000.0


def test_parse_expiry_path_param() -> None:
    url = "https://rr1---sn-x.googlevideo.com/videoplayback/expire/1761400123/ei/abc"
    assert parse_expiry(url) == 1761400123.0


def test_parse_expiry_fallback() -> None:
    assert parse_expiry("https://example.com/audio", now=1000.0) == 1000.0 + FALLBACK_LIFETIME


@pytest.mark.parametrize(
    ("message", "has_js", "kind"),
    [
        ("[youtube] x: Video unavailable", True, ErrorKind.UNAVAILABLE),
        ("[youtube] x: Private video. Sign in", True, ErrorKind.UNAVAILABLE),
        ("Unable to download API page: timed out", True, ErrorKind.NETWORK),
        ("HTTP Error 429: Too Many Requests", True, ErrorKind.NETWORK),
        ("n challenge solving failed", False, ErrorKind.NO_JS_RUNTIME),
        ("n challenge solving failed", True, ErrorKind.EXTRACTION),
        ("Sign in to confirm you’re not a bot", True, ErrorKind.EXTRACTION),
        ("Requested format is not available", True, ErrorKind.EXTRACTION),
    ],
)
def test_classify(message: str, has_js: bool, kind: ErrorKind) -> None:
    assert classify(message, has_js) is kind


def _info(**overrides: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "url": URL,
        "format_id": "251",
        "acodec": "opus",
        "abr": 134.2,
        "duration": 370,
        "http_headers": {"User-Agent": "UA", "Accept": "*/*", "Referer": "https://x"},
    }
    info.update(overrides)
    return info


def test_stream_from_selected_format() -> None:
    stream = stream_from_info("vid", _info())
    assert stream["url"] == URL
    assert stream["format_id"] == "251"
    assert stream["codec"] == "opus"
    assert stream["abr"] == pytest.approx(134.2)
    assert stream["expires_at"] == 1761400000.0
    assert stream["http_headers"] == {"User-Agent": "UA", "Referer": "https://x"}
    assert stream["premium"] is False
    assert stream["available_at"] is None


def test_stream_premium_and_available_at() -> None:
    stream = stream_from_info("vid", _info(format_id="774", available_at=1761300000))
    assert stream["premium"] is True
    assert stream["available_at"] == 1761300000.0


def test_stream_from_requested_formats() -> None:
    info = {"duration": 10, "requested_formats": [_info(format_id="141", acodec="mp4a.40.2")]}
    stream = stream_from_info("vid", info)
    assert stream["format_id"] == "141"
    assert stream["premium"] is True


def test_stream_without_formats_raises() -> None:
    with pytest.raises(ResolveError) as excinfo:
        stream_from_info("vid", {"duration": 1})
    assert excinfo.value.kind is ErrorKind.EXTRACTION


def test_attempts_anonymous_avoids_js_first() -> None:
    resolver = Resolver(ResolverConfig(cachedir="c"))
    assert resolver.attempts() == [(ANONYMOUS_CLIENTS, False), ((), True)]


def test_attempts_signed_in_uses_web_music() -> None:
    config = ResolverConfig(cachedir="c", cookies="# Netscape HTTP Cookie File\n")
    assert config.signed_in
    assert Resolver(config).attempts() == [(SIGNED_IN_CLIENTS, True), ((), True)]


def test_attempts_user_override() -> None:
    config = ResolverConfig(cachedir="c", player_clients=("tv",))
    assert Resolver(config).attempts() == [(("tv",), True)]


def test_resolve_falls_back_only_on_extraction_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = Resolver(ResolverConfig(cachedir="c"), lambda: JsRuntime("deno", "d", "2.9.8"))
    calls: list[tuple[str, ...]] = []

    def fake_extract(video_id: str, clients: tuple[str, ...], use_js: bool, fmt: str | None) -> Any:
        calls.append(clients)
        if clients:
            raise ResolveError(ErrorKind.EXTRACTION, "bot check")
        return stream_from_info(video_id, _info())

    monkeypatch.setattr(resolver, "_extract", fake_extract)
    assert resolver.resolve("vid")["format_id"] == "251"
    assert calls == [ANONYMOUS_CLIENTS, ()]

    calls.clear()

    def unavailable(video_id: str, clients: tuple[str, ...], use_js: bool, fmt: str | None) -> Any:
        calls.append(clients)
        raise ResolveError(ErrorKind.UNAVAILABLE, "gone")

    monkeypatch.setattr(resolver, "_extract", unavailable)
    with pytest.raises(ResolveError):
        resolver.resolve("vid")
    assert calls == [ANONYMOUS_CLIENTS]  # no point trying other clients
