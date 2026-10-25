"""JSON-lines protocol between the app and the resolver worker.

One JSON object per line, UTF-8. stdout carries protocol messages only; the worker
logs to stderr.

App -> worker:
    {"op": "configure", "cachedir": str, "cookies": str | null,
     "js_runtime": str, "player_clients": [str]}
    {"op": "resolve", "id": int, "video_id": str, "priority": 0 | 1, "format": str | null}
    {"op": "shutdown"}

Worker -> app:
    {"op": "ready", "versions": {...}, "js_runtime": {...} | null}
    {"op": "result", "id": int, "video_id": str, "stream": StreamDict}
    {"op": "error", "id": int, "video_id": str, "kind": ErrorKind, "message": str}
"""

from __future__ import annotations

import json
from enum import IntEnum, StrEnum
from typing import Any, TypedDict


class Priority(IntEnum):
    NOW = 0
    PREFETCH = 1


class ErrorKind(StrEnum):
    EXTRACTION = "extraction"  # YouTube changed something, or no playable audio was offered
    UNAVAILABLE = "unavailable"  # private, removed, region- or age-gated
    NETWORK = "network"
    NO_JS_RUNTIME = "no_js_runtime"
    INTERNAL = "internal"


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
