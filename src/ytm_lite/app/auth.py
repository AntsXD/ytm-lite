"""Sign-in: turn a Firefox profile, pasted request headers or a cookies.txt into credentials.

One set of credentials feeds both consumers:
- ytmusicapi gets "browser" headers (Cookie, X-Goog-AuthUser; it recomputes the
  SAPISIDHASH Authorization from the __Secure-3PAPISID cookie on every request),
- yt-dlp gets the same cookies as Netscape cookies.txt text, kept in memory.
"""

from __future__ import annotations

import configparser
import itertools
import json
import os
import re
import shlex
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

YTM_ORIGIN = "https://music.youtube.com"
# ytmusicapi computes its SAPISIDHASH from this cookie; yt-dlp needs LOGIN_INFO too.
REQUIRED_COOKIES = ("LOGIN_INFO", "__Secure-3PAPISID")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"


class AuthError(Exception):
    """A sign-in source couldn't produce usable credentials; the message is for the user."""


@dataclass(frozen=True, slots=True)
class Cookie:
    name: str
    value: str
    domain: str = ".youtube.com"
    path: str = "/"
    secure: bool = True
    expires: int = 0  # unix seconds; 0 for session cookies

    @property
    def for_music(self) -> bool:
        """Sent with requests to music.youtube.com."""
        domain = self.domain.lstrip(".")
        return domain in ("youtube.com", "music.youtube.com")


@dataclass(frozen=True, slots=True)
class Credentials:
    cookies: tuple[Cookie, ...]
    method: str  # "firefox", "headers" or "cookies_txt"
    authuser: str = "0"
    visitor_id: str | None = None
    firefox_profile: str | None = None  # profile folder, for silent re-imports
    account_name: str | None = field(default=None, compare=False)

    def missing(self) -> list[str]:
        names = {c.name for c in self.cookies if c.for_music}
        return [name for name in REQUIRED_COOKIES if name not in names]

    def cookie_header(self) -> str:
        seen: dict[str, str] = {}
        # Domain cookies first so a host-only duplicate for music.youtube.com wins.
        for cookie in sorted(self.cookies, key=lambda c: not c.domain.startswith(".")):
            if cookie.for_music:
                seen[cookie.name] = cookie.value
        return "; ".join(f"{name}={value}" for name, value in seen.items())

    def netscape(self) -> str:
        """cookies.txt for yt-dlp. Everything is scoped to .youtube.com, which is where
        yt-dlp looks; host-only music.youtube.com cookies would be invisible to it."""
        lines = ["# Netscape HTTP Cookie File"]
        seen: set[str] = set()
        for cookie in sorted(self.cookies, key=lambda c: not c.domain.startswith(".")):
            if not cookie.for_music or cookie.name in seen:
                continue
            seen.add(cookie.name)
            lines.append(
                "\t".join(
                    (
                        ".youtube.com",
                        "TRUE",
                        "/",
                        "TRUE",
                        str(cookie.expires),
                        cookie.name,
                        cookie.value,
                    )
                )
            )
        return "\n".join(lines) + "\n"

    def ytmusic_headers(self) -> dict[str, str]:
        """Headers for YTMusic(auth=...) in browser-auth mode."""
        headers = {
            "cookie": self.cookie_header(),
            "x-goog-authuser": self.authuser,
            # Only the prefix matters: it selects browser auth, and ytmusicapi replaces the
            # value with a fresh SAPISIDHASH on every request.
            "authorization": "SAPISIDHASH 0_0",
            "user-agent": USER_AGENT,
            "accept": "*/*",
            "content-type": "application/json",
            "origin": YTM_ORIGIN,
            "x-origin": YTM_ORIGIN,
        }
        if self.visitor_id:
            headers["x-goog-visitor-id"] = self.visitor_id
        return headers

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Credentials:
        raw: dict[str, Any] = json.loads(text)
        raw["cookies"] = tuple(Cookie(**c) for c in raw.get("cookies", ()))
        return cls(**raw)


def _check(credentials: Credentials, source: str) -> Credentials:
    missing = credentials.missing()
    if missing:
        raise AuthError(
            f"{source} has no YouTube sign-in (missing {', '.join(missing)}). "
            "Sign in at music.youtube.com first, then try again."
        )
    return credentials


# -- pasted request headers ---------------------------------------------------------------

_HEADER_LINE = re.compile(r"^([A-Za-z0-9-]+):\s+(.*)$")
_NAME_ONLY = re.compile(r"^:?([A-Za-z0-9-]+):?$")
_REQUEST_LINE = re.compile(r"^(GET|POST|PUT|OPTIONS|HEAD)\s+\S+\s+HTTP/", re.IGNORECASE)


def _parse_curl(text: str) -> dict[str, str]:
    if "^" in text:  # "Copy as cURL (cmd)": ^ escapes everything, lines end in ^
        text = re.sub(r"\^\r?\n", " ", text)
        text = re.sub(r"\^(.)", r"\1", text)
    text = re.sub(r"\\\r?\n", " ", text)  # bash line continuations
    text = text.replace("$'", "'")  # bash ANSI-C quoting; values here are plain ASCII
    tokens = shlex.split(text, posix=True)
    headers: dict[str, str] = {}
    for flag, value in itertools.pairwise(tokens):
        if flag in ("-H", "--header") and ":" in value:
            name, _, content = value.partition(":")
            headers[name.strip().lower()] = content.strip()
        elif flag in ("-b", "--cookie"):
            headers["cookie"] = value.strip()
    return headers


def _parse_fetch(text: str) -> dict[str, str]:
    match = re.search(r'"headers"\s*:\s*\{(.*?)\}', text, re.DOTALL)
    if not match:
        return {}
    pairs = re.findall(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', match.group(1))
    return {name.lower(): json.loads(f'"{value}"') for name, value in pairs}


def _parse_raw(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    pending: str | None = None
    for line in (raw.strip() for raw in text.splitlines()):
        if not line or _REQUEST_LINE.match(line):
            continue
        if pending is not None:  # Chrome's "name on one line, value on the next" copy
            if pending and not pending.startswith(":"):
                headers[pending.lower()] = line
            pending = None
            continue
        if line.startswith(":"):  # HTTP/2 pseudo-header like ":authority: x"
            if not _HEADER_LINE.match(line[1:]):
                pending = ":"
            continue
        match = _HEADER_LINE.match(line)
        if match:
            headers[match.group(1).lower()] = match.group(2).strip()
            continue
        name = _NAME_ONLY.match(line)
        if name:
            pending = name.group(1)
    return headers


def parse_headers(text: str) -> dict[str, str]:
    """Headers from DevTools: raw headers (Chrome or Firefox), "Copy as cURL"
    (bash or cmd) or "Copy as fetch". Keys are lower-cased."""
    stripped = text.strip()
    if stripped.lower().startswith("curl "):
        return _parse_curl(stripped)
    if "fetch(" in stripped:
        return _parse_fetch(stripped)
    return _parse_raw(stripped)


def cookies_from_header(header: str) -> tuple[Cookie, ...]:
    cookies = []
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies.append(Cookie(name=name, value=value))
    return tuple(cookies)


def credentials_from_headers(text: str) -> Credentials:
    headers = parse_headers(text)
    if "cookie" not in headers:
        raise AuthError(
            'No Cookie header found. Copy the request headers (or "Copy as cURL") of a '
            'music.youtube.com request; "Copy as fetch" leaves cookies out.'
        )
    credentials = Credentials(
        cookies=cookies_from_header(headers["cookie"]),
        method="headers",
        authuser=headers.get("x-goog-authuser", "0") or "0",
        visitor_id=headers.get("x-goog-visitor-id") or None,
    )
    return _check(credentials, "These headers")


# -- cookies.txt ----------------------------------------------------------------------------


def credentials_from_cookies_txt(text: str) -> Credentials:
    cookies = []
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain, _, path, secure, expires, name, value = fields
        if domain.lstrip(".").endswith("youtube.com"):
            expiry = int(expires) if expires.isdigit() else 0
            cookies.append(
                Cookie(name, value.strip(), domain, path, secure.upper() == "TRUE", expiry)
            )
    return _check(Credentials(cookies=tuple(cookies), method="cookies_txt"), "This cookies.txt")


# -- Firefox --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FirefoxProfile:
    name: str
    path: Path
    default: bool


def firefox_roots() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return [Path(appdata) / "Mozilla" / "Firefox"] if appdata else []
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Firefox"]
    return [
        home / ".mozilla" / "firefox",
        home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
    ]


def firefox_profiles(roots: Iterable[Path] | None = None) -> list[FirefoxProfile]:
    """Profiles from profiles.ini, the one Firefox actually launches with first."""
    profiles: list[FirefoxProfile] = []
    for root in firefox_roots() if roots is None else roots:
        ini = root / "profiles.ini"
        if not ini.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(ini, encoding="utf-8")
        # [Install...] sections name the profile each Firefox install really uses.
        install_defaults = {
            parser[s].get("Default", "") for s in parser.sections() if s.startswith("Install")
        }
        for section in parser.sections():
            if not section.startswith("Profile") or "Path" not in parser[section]:
                continue
            entry = parser[section]
            rel = entry.get("IsRelative", "1") == "1"
            path = root / entry["Path"] if rel else Path(entry["Path"])
            default = entry["Path"] in install_defaults or (
                not install_defaults and entry.get("Default") == "1"
            )
            profiles.append(FirefoxProfile(entry.get("Name", entry["Path"]), path, default))
    return sorted(profiles, key=lambda p: not p.default)


def read_firefox_cookies(profile_dir: Path) -> tuple[Cookie, ...]:
    """YouTube cookies of the default container, read from a throwaway copy of the
    database (Firefox keeps the original locked, and recent writes sit in the -wal file)."""
    database = profile_dir / "cookies.sqlite"
    if not database.is_file():
        raise AuthError(f"No cookie database in Firefox profile {profile_dir.name}.")
    with tempfile.TemporaryDirectory(prefix="ytm-lite-") as tmp:
        copy = Path(tmp) / "cookies.sqlite"
        shutil.copyfile(database, copy)
        wal = profile_dir / "cookies.sqlite-wal"
        if wal.is_file():
            shutil.copyfile(wal, Path(tmp) / "cookies.sqlite-wal")
        connection = sqlite3.connect(copy)
        try:
            rows = connection.execute(
                "SELECT host, name, value, path, expiry, isSecure, originAttributes "
                "FROM moz_cookies WHERE host = 'youtube.com' OR host LIKE '%.youtube.com' "
                "OR host = 'music.youtube.com'"
            ).fetchall()
        except sqlite3.Error as exc:
            raise AuthError(f"Couldn't read Firefox's cookies: {exc}") from exc
        finally:
            connection.close()
    cookies = []
    for host, name, value, path, expiry, secure, origin_attributes in rows:
        if origin_attributes:  # container tabs, first-party isolation, partitions
            continue
        expires = int(expiry or 0)
        if expires > 10_000_000_000:  # newer Firefox stores milliseconds
            expires //= 1000
        cookies.append(Cookie(name, value, host, path or "/", bool(secure), expires))
    return tuple(cookies)


def credentials_from_firefox(profile: FirefoxProfile | None = None) -> Credentials:
    profiles = [profile] if profile else firefox_profiles()
    if not profiles:
        raise AuthError("Firefox doesn't seem to be installed (no profiles.ini found).")
    first_error: AuthError | None = None
    for candidate in profiles:
        try:
            cookies = read_firefox_cookies(candidate.path)
            return _check(
                Credentials(cookies, method="firefox", firefox_profile=str(candidate.path)),
                f"Firefox profile “{candidate.name}”",
            )
        except AuthError as exc:
            first_error = first_error or exc
    assert first_error is not None
    raise first_error
