"""Mask secrets (cookies, auth headers, signed stream URLs) before anything is logged."""

from __future__ import annotations

import re

REDACTED = "<redacted>"

# Header-style secrets: "Cookie: ...", "authorization=...", JSON/dict keys like "cookie": "...".
_HEADER_RE = re.compile(
    r"""(["']?)\b(cookie|set-cookie|authorization|x-goog-[a-z0-9-]+)\1(\s*[:=]\s*)("[^"]*"|'[^']*'|[^\r\n]*)""",
    re.IGNORECASE,
)
# Individual Google session cookies wherever they appear ("SAPISID=abc; ...").
_COOKIE_PAIR_RE = re.compile(
    r"\b(SAPISID|APISID|HSID|SSID|SID|SIDCC|LOGIN_INFO|__Secure-[\w-]+|__Host-[\w-]+)=([^;\s\"']+)"
)
# Netscape cookies.txt lines for youtube.com: keep the cookie name, mask the value.
_NETSCAPE_LINE_RE = re.compile(
    r"(?m)^((?:#HttpOnly_)?\.?[\w.-]*youtube\.com\t\S+\t\S+\t\S+\t\d+\t[^\t\r\n]+\t)[^\r\n]*$"
)
# Signed googlevideo stream URLs: keep host and path, drop the query string.
_GOOGLEVIDEO_RE = re.compile(r"(https?://[^\s\"'/]*googlevideo\.com/[^\s\"'?]*)\?[^\s\"']*")


def redact(text: str) -> str:
    text = _NETSCAPE_LINE_RE.sub(rf"\g<1>{REDACTED}", text)
    text = _HEADER_RE.sub(lambda m: f"{m[1]}{m[2]}{m[1]}{m[3]}{REDACTED}", text)
    text = _COOKIE_PAIR_RE.sub(rf"\g<1>={REDACTED}", text)
    return _GOOGLEVIDEO_RE.sub(rf"\g<1>?{REDACTED}", text)
