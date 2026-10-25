from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ytm_lite.app.auth import (
    AuthError,
    Cookie,
    Credentials,
    FirefoxProfile,
    credentials_from_cookies_txt,
    credentials_from_firefox,
    credentials_from_headers,
    firefox_profiles,
    parse_headers,
    read_firefox_cookies,
)

COOKIE = "LOGIN_INFO=AFmmF2s; __Secure-3PAPISID=abc/def; SID=s1d; PREF=f6=4"

FIREFOX_RAW = f"""POST /youtubei/v1/browse?prettyPrint=false HTTP/2
Host: music.youtube.com
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0
Accept: */*
Cookie: {COOKIE}
X-Goog-AuthUser: 0
X-Goog-Visitor-Id: CgtWaXNpdG9y
Authorization: SAPISIDHASH 1700000000_abc
"""

CHROME_HTTP2_RAW = f""":authority: music.youtube.com
:method: POST
:path: /youtubei/v1/browse?prettyPrint=false
accept: */*
cookie: {COOKIE}
x-goog-authuser: 1
"""

CHROME_ALTERNATING = f""":authority
music.youtube.com
:method
POST
cookie
{COOKIE}
x-goog-authuser
2
"""

CURL_BASH = f"""curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\
  -H 'accept: */*' \\
  -b '{COOKIE}' \\
  -H 'x-goog-authuser: 0' \\
  --data-raw '{{"context":{{}}}}'"""

CURL_CMD = f"""curl ^"https://music.youtube.com/youtubei/v1/browse?prettyPrint=false^" ^
  -H ^"accept: */*^" ^
  -b ^"{COOKIE}^" ^
  -H ^"x-goog-authuser: 3^" ^
  --data-raw ^"^{{^\\^"context^\\^":^{{^}}^}}^\""""

FETCH_NODE = f"""fetch("https://music.youtube.com/youtubei/v1/browse?prettyPrint=false", {{
  "headers": {{
    "accept": "*/*",
    "cookie": "{COOKIE}",
    "x-goog-authuser": "0"
  }},
  "body": "{{}}",
  "method": "POST"
}});"""


@pytest.mark.parametrize(
    ("text", "authuser"),
    [
        (FIREFOX_RAW, "0"),
        (CHROME_HTTP2_RAW, "1"),
        (CHROME_ALTERNATING, "2"),
        (CURL_BASH, "0"),
        (CURL_CMD, "3"),
        (FETCH_NODE, "0"),
    ],
    ids=["firefox-raw", "chrome-http2", "chrome-alternating", "curl-bash", "curl-cmd", "fetch"],
)
def test_parse_header_formats(text: str, authuser: str) -> None:
    headers = parse_headers(text)
    assert headers["cookie"] == COOKIE
    assert headers["x-goog-authuser"] == authuser
    assert not any(name.startswith(":") for name in headers)


def test_credentials_from_headers() -> None:
    creds = credentials_from_headers(FIREFOX_RAW)
    assert creds.method == "headers"
    assert creds.visitor_id == "CgtWaXNpdG9y"
    assert {c.name for c in creds.cookies} == {"LOGIN_INFO", "__Secure-3PAPISID", "SID", "PREF"}
    assert creds.missing() == []


def test_headers_without_cookie_or_login() -> None:
    with pytest.raises(AuthError, match="No Cookie header"):
        credentials_from_headers("accept: */*\nx-goog-authuser: 0")
    with pytest.raises(AuthError, match="LOGIN_INFO"):
        credentials_from_headers("cookie: __Secure-3PAPISID=abc; SID=x")


def _creds() -> Credentials:
    return Credentials(
        cookies=(
            Cookie("LOGIN_INFO", "login", ".youtube.com", expires=1800000000),
            Cookie("__Secure-3PAPISID", "papisid", ".youtube.com"),
            Cookie("SID", "domain-sid", ".youtube.com"),
            Cookie("SID", "host-sid", "music.youtube.com", secure=False),
            Cookie("YSC", "www-only", "www.youtube.com"),
            Cookie("NID", "google", ".google.com"),
        ),
        method="firefox",
        authuser="1",
        visitor_id="visitor",
        firefox_profile="C:/ff/profile",
    )


def test_cookie_header_prefers_host_only_duplicates() -> None:
    header = _creds().cookie_header()
    assert header == "LOGIN_INFO=login; __Secure-3PAPISID=papisid; SID=host-sid"


def test_netscape_scopes_everything_to_youtube_com() -> None:
    lines = _creds().netscape().splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    assert lines[1] == ".youtube.com\tTRUE\t/\tTRUE\t1800000000\tLOGIN_INFO\tlogin"
    names = [line.split("\t")[5] for line in lines[1:]]
    assert names == ["LOGIN_INFO", "__Secure-3PAPISID", "SID"]
    assert "domain-sid" in lines[3]  # the .youtube.com copy wins for yt-dlp


def test_ytmusic_headers_select_browser_auth() -> None:
    headers = _creds().ytmusic_headers()
    assert headers["authorization"].startswith("SAPISIDHASH")
    assert headers["x-goog-authuser"] == "1"
    assert headers["x-goog-visitor-id"] == "visitor"
    assert "__Secure-3PAPISID=papisid" in headers["cookie"]


def test_ytmusicapi_accepts_the_headers() -> None:
    from ytmusicapi import YTMusic
    from ytmusicapi.auth.types import AuthType

    client = YTMusic(auth=_creds().ytmusic_headers())
    assert client.auth_type == AuthType.BROWSER
    assert client.sapisid == "papisid"


def test_json_roundtrip() -> None:
    creds = _creds()
    assert Credentials.from_json(creds.to_json()) == creds


def test_cookies_txt() -> None:
    text = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1800000000\tLOGIN_INFO\tlogin\n"
        "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1800000000\t__Secure-3PAPISID\tpap\n"
        ".google.com\tTRUE\t/\tTRUE\t1800000000\tSID\tgoogle\n"
        "malformed line\n"
    )
    creds = credentials_from_cookies_txt(text)
    assert [c.name for c in creds.cookies] == ["LOGIN_INFO", "__Secure-3PAPISID"]
    assert creds.cookies[0].expires == 1800000000


PROFILES_INI = """[Install308046B0AF4A39CB]
Default=Profiles/abc.default-release
Locked=1

[Profile1]
Name=default
IsRelative=1
Path=Profiles/xyz.default
Default=1

[Profile0]
Name=default-release
IsRelative=1
Path=Profiles/abc.default-release

[General]
StartWithLastProfile=1
Version=2
"""


def test_firefox_profiles_prefer_install_default(tmp_path: Path) -> None:
    (tmp_path / "profiles.ini").write_text(PROFILES_INI, "utf-8")
    profiles = firefox_profiles([tmp_path])
    assert [p.name for p in profiles] == ["default-release", "default"]
    assert profiles[0].default and not profiles[1].default
    assert profiles[0].path == tmp_path / "Profiles/abc.default-release"


def _make_cookie_db(profile: Path, rows: list[tuple[str, str, str, str, int, str]]) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(profile / "cookies.sqlite")
    db.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL "
        "DEFAULT '', name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, "
        "lastAccessed INTEGER, creationTime INTEGER, isSecure INTEGER, isHttpOnly INTEGER)"
    )
    db.executemany(
        "INSERT INTO moz_cookies (host, name, value, path, expiry, originAttributes, isSecure) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)",
        rows,
    )
    db.commit()
    db.close()


def test_read_firefox_cookies_filters_hosts_and_containers(tmp_path: Path) -> None:
    profile = tmp_path / "p"
    _make_cookie_db(
        profile,
        [
            (".youtube.com", "LOGIN_INFO", "login", "/", 1800000000, ""),
            (".youtube.com", "__Secure-3PAPISID", "pap", "/", 1800000000123, ""),  # ms
            (".youtube.com", "SID", "container", "/", 1800000000, "^userContextId=1"),
            ("music.youtube.com", "VISITOR", "v", "/", 0, ""),
            (".google.com", "SID", "google", "/", 1800000000, ""),
        ],
    )
    cookies = read_firefox_cookies(profile)
    by_name = {c.name: c for c in cookies}
    assert set(by_name) == {"LOGIN_INFO", "__Secure-3PAPISID", "VISITOR"}
    assert by_name["__Secure-3PAPISID"].expires == 1800000000
    assert list(tmp_path.glob("**/ytm-lite-*")) == []  # temp copy removed


def test_credentials_from_firefox_falls_back_to_signed_in_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ytm_lite.app.auth as auth

    signed_out = tmp_path / "a"
    signed_in = tmp_path / "b"
    _make_cookie_db(signed_out, [(".youtube.com", "PREF", "x", "/", 0, "")])
    _make_cookie_db(
        signed_in,
        [
            (".youtube.com", "LOGIN_INFO", "login", "/", 0, ""),
            (".youtube.com", "__Secure-3PAPISID", "pap", "/", 0, ""),
        ],
    )
    with pytest.raises(AuthError):
        credentials_from_firefox(FirefoxProfile("a", signed_out, True))
    profiles = [FirefoxProfile("a", signed_out, True), FirefoxProfile("b", signed_in, False)]
    monkeypatch.setattr(auth, "firefox_profiles", lambda roots=None: profiles)
    creds = credentials_from_firefox()
    assert creds.method == "firefox"
    assert creds.firefox_profile == str(signed_in)
