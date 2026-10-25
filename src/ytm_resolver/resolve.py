"""Resolve a videoId to a direct audio-only stream URL with yt-dlp.

yt-dlp is imported lazily, and only the YouTube extractor is registered, so the other
~1,800 extractors are never loaded.
"""

from __future__ import annotations

import io
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from ytm_resolver.jsruntime import JsRuntime
from ytm_resolver.protocol import ErrorKind, StreamDict

log = logging.getLogger("ytm_resolver")

# 774 = Premium Opus ~256k, 141 = Premium AAC 256k, 251 = Opus ~160k, 140 = AAC 128k.
DEFAULT_FORMAT = "774/141/251/140/bestaudio"
PREMIUM_FORMATS = frozenset({"774", "141"})
# With Premium cookies the default client set makes 3-4 player requests per track;
# web_music alone serves the Premium formats (it needs the JS challenge solver).
SIGNED_IN_CLIENTS = ("web_music",)
# Logged out, visionos serves Opus 251 without any JS challenge, so no JS runtime is
# spawned (that process peaks around 300 MB), and its URLs are usable immediately.
ANONYMOUS_CLIENTS = ("visionos",)
# Used when googlevideo leaves out expire=; real URLs last several hours.
FALLBACK_LIFETIME = 4 * 3600
# Only these format headers are forwarded to mpv (same as mpv's own ytdl hook).
FORWARDED_HEADERS = ("User-Agent", "Referer")

_EXPIRE_RE = re.compile(r"[?&/]expire[=/](\d+)")


class ResolveError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def parse_expiry(url: str, now: float | None = None) -> float:
    """Unix time the signed URL stops working, from its expire= parameter."""
    match = _EXPIRE_RE.search(url)
    if match:
        return float(match.group(1))
    return (time.time() if now is None else now) + FALLBACK_LIFETIME


_UNAVAILABLE_HINTS = (
    "video unavailable",
    "private video",
    "this video is not available",
    "not available in your country",
    "has been removed",
    "members-only",
    "members only",
    "confirm your age",
    "copyright",
)
_NETWORK_HINTS = (
    "timed out",
    "getaddrinfo",
    "connection",
    "network is unreachable",
    "temporary failure",
    "unable to download api page",
    "http error 429",
    "http error 5",
)
_JS_HINTS = ("javascript runtime", "js runtime", "challenge", "signature")


def classify(message: str, has_js_runtime: bool) -> ErrorKind:
    low = message.lower()
    if any(hint in low for hint in _UNAVAILABLE_HINTS):
        return ErrorKind.UNAVAILABLE
    if not has_js_runtime and any(hint in low for hint in _JS_HINTS):
        return ErrorKind.NO_JS_RUNTIME
    if any(hint in low for hint in _NETWORK_HINTS):
        return ErrorKind.NETWORK
    return ErrorKind.EXTRACTION


def stream_from_info(video_id: str, info: dict[str, Any], now: float | None = None) -> StreamDict:
    """Pick the selected format out of a processed yt-dlp info dict."""
    fmt: dict[str, Any] = info
    if "url" not in info:
        requested = info.get("requested_formats") or []
        if not requested:
            raise ResolveError(ErrorKind.EXTRACTION, "yt-dlp returned no playable format")
        fmt = requested[0]
    url = str(fmt["url"])
    headers = fmt.get("http_headers") or info.get("http_headers") or {}
    format_id = str(fmt.get("format_id") or "")
    abr = fmt.get("abr") or fmt.get("tbr")
    available_at = fmt.get("available_at")
    duration = info.get("duration")
    return StreamDict(
        video_id=video_id,
        url=url,
        format_id=format_id,
        codec=str(fmt.get("acodec") or fmt.get("ext") or "?"),
        abr=float(abr) if abr else None,
        expires_at=parse_expiry(url, now),
        available_at=float(available_at) if available_at else None,
        http_headers={k: str(headers[k]) for k in FORWARDED_HEADERS if headers.get(k)},
        duration=float(duration) if duration else None,
        premium=format_id in PREMIUM_FORMATS,
    )


@dataclass(frozen=True, slots=True)
class ResolverConfig:
    cachedir: str
    cookies: str | None = None  # Netscape cookies.txt text, kept in memory only
    js_runtime: JsRuntime | None = None
    player_clients: tuple[str, ...] = ()  # user override; empty means automatic
    format: str = DEFAULT_FORMAT

    @property
    def signed_in(self) -> bool:
        return bool(self.cookies)


class _YtdlpLogger:
    """Routes yt-dlp's output into logging (redacted by the handler's formatter)."""

    def debug(self, msg: str) -> None:
        log.debug("%s", msg)

    def info(self, msg: str) -> None:
        log.debug("%s", msg)

    def warning(self, msg: str) -> None:
        log.warning("%s", msg)

    def error(self, msg: str) -> None:
        log.error("%s", msg)


class Resolver:
    """Wraps YoutubeDL. Not thread-safe: use one Resolver per thread."""

    def __init__(self, config: ResolverConfig) -> None:
        self.config = config
        self._ydl: dict[bool, Any] = {}  # keyed by "JS runtime enabled"

    def _client(self, use_js: bool) -> Any:
        if use_js not in self._ydl:
            from yt_dlp import YoutubeDL
            from yt_dlp.extractor.youtube import YoutubeIE

            runtime = self.config.js_runtime if use_js else None
            params: dict[str, Any] = {
                "format": self.config.format,
                "skip_download": True,
                "quiet": True,
                "no_warnings": False,
                "noprogress": True,
                "noplaylist": True,
                "logger": _YtdlpLogger(),
                "cachedir": self.config.cachedir,
                "socket_timeout": 15,
                # An empty dict disables JS runtimes (yt-dlp's default would look for Deno).
                "js_runtimes": runtime.as_ytdlp_option() if runtime else {},
            }
            if self.config.cookies:
                params["cookiefile"] = io.StringIO(self.config.cookies)
            ydl = YoutubeDL(params, auto_init=False)
            ydl.add_info_extractor(YoutubeIE())
            self._ydl[use_js] = ydl
        return self._ydl[use_js]

    def attempts(self) -> list[tuple[tuple[str, ...], bool]]:
        """(player clients, use JS runtime) to try in order; () means yt-dlp's defaults."""
        if self.config.player_clients:
            return [(self.config.player_clients, True)]
        if self.config.signed_in:
            return [(SIGNED_IN_CLIENTS, True), ((), True)]
        return [(ANONYMOUS_CLIENTS, False), ((), True)]

    def resolve(self, video_id: str, format_override: str | None = None) -> StreamDict:
        last: ResolveError | None = None
        for clients, use_js in self.attempts():
            try:
                return self._extract(video_id, clients, use_js, format_override)
            except ResolveError as exc:
                last = exc
                if exc.kind is not ErrorKind.EXTRACTION:
                    raise
                log.info("no audio with clients %s; trying the next set", clients or "default")
        assert last is not None
        raise last

    def _extract(
        self, video_id: str, clients: tuple[str, ...], use_js: bool, format_override: str | None
    ) -> StreamDict:
        from yt_dlp.utils import DownloadError

        ydl = self._client(use_js)
        youtube_args: dict[str, list[str]] = {"skip": ["hls", "dash"]}
        if clients:
            youtube_args["player_client"] = list(clients)
        ydl.params["extractor_args"] = {"youtube": youtube_args}
        ydl.params["format"] = format_override or self.config.format
        url = f"https://music.youtube.com/watch?v={video_id}"
        started = time.monotonic()
        try:
            info = ydl.extract_info(url, download=False, ie_key="Youtube")
        except DownloadError as exc:
            message = str(exc).removeprefix("ERROR: ")
            raise ResolveError(
                classify(message, self.config.js_runtime is not None), message
            ) from exc
        if not isinstance(info, dict):
            raise ResolveError(ErrorKind.EXTRACTION, "yt-dlp returned nothing")
        stream = stream_from_info(video_id, info)
        log.info(
            "resolved %s: format %s (%s, %s kbps) in %.2fs",
            video_id,
            stream["format_id"],
            stream["codec"],
            round(stream["abr"]) if stream["abr"] else "?",
            time.monotonic() - started,
        )
        return stream
