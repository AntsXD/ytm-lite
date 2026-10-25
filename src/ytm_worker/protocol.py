"""JSON-lines protocol between the app and the worker process.

One JSON object per line, UTF-8. stdout carries protocol messages only; the worker
logs to stderr.

App -> worker:
    {"op": "configure", "cachedir": str, "cookies": str | null, "js_runtime": str,
     "player_clients": [str], "api_headers": {...} | null, "visitor_id": str | null,
     "idle_timeout": float}
    {"op": "resolve", "id": int, "video_id": str, "priority": 0 | 1, "format": str | null,
     "fast": bool}   # fast: quick standard-quality resolve instead of Premium (signed in)
    {"op": "api", "id": int, "method": str, "args": {...}, "background": bool,
     "headers": {...} | null}
    {"op": "shutdown"}

Worker -> app:
    {"op": "ready", "pid": int, "versions": {...}, "signed_in": bool}
    {"op": "result", "id": int, "video_id": str, "fast": bool, "stream": StreamDict}
    {"op": "error", "id": int, "video_id": str, "fast": bool, "kind": ErrorKind,
     "message": str}
    {"op": "api_result", "id": int, "result": ...}
    {"op": "api_error", "id": int, "kind": ApiErrorKind, "message": str}
    {"op": "runtime", "js_runtime": {"name": str, "version": str} | null}
    {"op": "visitor", "visitor_id": str}
    {"op": "bye"}   # idle exit; requests written after this are not read
"""

from __future__ import annotations

import json
from enum import IntEnum, StrEnum
from typing import Any, TypedDict


class Priority(IntEnum):
    NOW = 0
    PREFETCH = 1


class ErrorKind(StrEnum):
    """Why a stream couldn't be resolved."""

    EXTRACTION = "extraction"  # YouTube changed something, or no playable audio was offered
    UNAVAILABLE = "unavailable"  # private, removed, region- or age-gated
    NETWORK = "network"
    NO_JS_RUNTIME = "no_js_runtime"
    INTERNAL = "internal"


class ApiErrorKind(StrEnum):
    """Why a YouTube Music API call failed."""

    AUTH = "auth"  # not signed in, or the session expired
    NETWORK = "network"
    SERVER = "server"  # YouTube answered with an error
    PARSE = "parse"  # a response we couldn't read (YouTube changed a layout)


class StreamDict(TypedDict):
    video_id: str
    url: str
    format_id: str
    codec: str
    abr: float | None  # kbps
    expires_at: float  # unix time
    available_at: float | None  # unix time before which googlevideo answers 403
    http_headers: dict[str, str]
    duration: float | None
    premium: bool


def encode(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes | str) -> dict[str, Any]:
    message = json.loads(line)
    if not isinstance(message, dict) or not isinstance(message.get("op"), str):
        raise ValueError("not a protocol message")
    return message
