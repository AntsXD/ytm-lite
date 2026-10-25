"""Cover URLs at the size they're shown (pure Python: no Qt needed)."""

from __future__ import annotations

import re

_GOOGLE_IMAGE = re.compile(
    r"^(https://(?:lh\d|yt\d)\.(?:googleusercontent|ggpht)\.com/[^=?#]+)(?:=[^?#]*)?$"
)
_YTIMG = re.compile(r"^(https://i\d?\.ytimg\.com/vi(?:_webp)?/[^/]+/)")


def sized_url(url: str, px: int) -> str:
    """Ask Google's image servers for roughly `px` pixels instead of the full size."""
    match = _GOOGLE_IMAGE.match(url)
    if match:
        return f"{match.group(1)}=w{px}-h{px}-l90-rj"
    match = _YTIMG.match(url)
    if match:
        variant = "default.jpg" if px <= 90 else "mqdefault.jpg" if px <= 180 else "hqdefault.jpg"
        return match.group(1) + variant
    return url


def image_key(url: str, px: int) -> str:
    return f"{px}|{url}"
