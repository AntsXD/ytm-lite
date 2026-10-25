"""Resolve a videoId to a direct audio-only stream URL with yt-dlp.

yt-dlp is imported lazily, and only the YouTube extractor is registered, so the other
~1,800 extractors are never loaded.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from ytm_worker.imports import IMPORT_LOCK
from ytm_worker.jsruntime import JsRuntime
from ytm_worker.protocol import ErrorKind, StreamDict

log = logging.getLogger("ytm_worker")

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


class Attempt(NamedTuple):
    clients: tuple[str, ...]  # empty: yt-dlp's default clients
    use_js: bool  # allow the JS challenge solver
    cookies: bool  # send the signed-in cookies


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
    player_clients: tuple[str, ...] = ()  # user override; empty means automatic
    format: str = DEFAULT_FORMAT

    @property
    def signed_in(self) -> bool:
        return bool(self.cookies)


class _YtdlpLogger:
    """Routes yt-dlp's output into logging (redacted by the handler's formatter).

    With YTM_WORKER_TRACE=1 in the environment, yt-dlp's progress lines are logged at
    INFO with the time since the resolve started, to see where a slow resolve goes.
    """

    trace = bool(os.environ.get("YTM_WORKER_TRACE"))

    def __init__(self) -> None:
        self.started = time.monotonic()

    def _progress(self, msg: str) -> None:
        if self.trace and not msg.startswith("[debug]"):
            log.info("+%.2fs %s", time.monotonic() - self.started, msg[:160])
        else:
            log.debug("%s", msg)

    def debug(self, msg: str) -> None:
        self._progress(msg)

    def info(self, msg: str) -> None:
        self._progress(msg)

    def warning(self, msg: str) -> None:
        log.warning("%s", msg)

    def error(self, msg: str) -> None:
        log.error("%s", msg)


class Resolver:
    """Wraps YoutubeDL. Not thread-safe: use one Resolver per thread.

    `runtime` finds the JS runtime; it's only called when a client set needs one, so a
    signed-out worker never even probes for Deno or Node.
    """

    def __init__(
        self, config: ResolverConfig, runtime: Callable[[], JsRuntime | None] = lambda: None
    ) -> None:
        self.config = config
        self._runtime = runtime
        self._ydl: dict[tuple[bool, bool], Any] = {}  # keyed by (use JS, send cookies)

    def _client(self, use_js: bool, cookies: bool) -> Any:
        key = (use_js, cookies)
        if key not in self._ydl:
            with IMPORT_LOCK:
                from yt_dlp import YoutubeDL
                from yt_dlp.extractor.youtube import YoutubeIE

            runtime = self._runtime() if use_js else None
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
            if cookies and self.config.cookies:
                params["cookiefile"] = io.StringIO(self.config.cookies)
            ydl = YoutubeDL(params, auto_init=False)
            ydl.add_info_extractor(YoutubeIE())
            self._ydl[key] = ydl
        return self._ydl[key]

    def attempts(self, fast: bool = False) -> list[Attempt]:
        """Client sets to try in order; empty clients mean yt-dlp's defaults.

        `fast` (signed in): start with the signed-out path, which needs no JS challenge
        and returns in ~1.5 s, at standard quality. The Premium path is the fallback.
        """
        if self.config.player_clients:
            return [Attempt(self.config.player_clients, True, True)]
        anonymous = Attempt(ANONYMOUS_CLIENTS, False, False)
        if self.config.signed_in:
            premium = [Attempt(SIGNED_IN_CLIENTS, True, True), Attempt((), True, True)]
            return [anonymous, *premium] if fast else premium
        return [anonymous, Attempt((), True, False)]

    def resolve(
        self, video_id: str, format_override: str | None = None, fast: bool = False
    ) -> StreamDict:
        last: ResolveError | None = None
        for attempt in self.attempts(fast):
            try:
                return self._extract(video_id, attempt, format_override)
            except ResolveError as exc:
                last = exc
                if exc.kind is not ErrorKind.EXTRACTION:
                    raise
                log.info(
                    "no audio with clients %s; trying the next set", attempt.clients or "default"
                )
        assert last is not None
        raise last

    def _extract(self, video_id: str, attempt: Attempt, format_override: str | None) -> StreamDict:
        from yt_dlp.utils import DownloadError

        clients, use_js = attempt.clients, attempt.use_js
        ydl = self._client(use_js, attempt.cookies)
        youtube_args: dict[str, list[str]] = {"skip": ["hls", "dash"]}
        if clients:
            youtube_args["player_client"] = list(clients)
        if clients == SIGNED_IN_CLIENTS:
            # Use built-in client defaults instead of downloading web_music's config page:
            # ~1.2 s faster, and Premium formats still come back.
            youtube_args["player_skip"] = ["configs"]
        # Experiments: YTM_WORKER_EXTRACTOR_ARGS='{"player_skip": ["configs"]}'
        extra = os.environ.get("YTM_WORKER_EXTRACTOR_ARGS")
        if extra:
            youtube_args.update(json.loads(extra))
        ydl.params["extractor_args"] = {"youtube": youtube_args}
        ydl.params["format"] = format_override or self.config.format
        url = f"https://music.youtube.com/watch?v={video_id}"
        started = time.monotonic()
        ydl.params["logger"].started = started
        try:
            info = ydl.extract_info(url, download=False, ie_key="Youtube")
        except DownloadError as exc:
            message = str(exc).removeprefix("ERROR: ")
            has_js = self._runtime() is not None if use_js else True
            raise ResolveError(classify(message, has_js), message) from exc
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
