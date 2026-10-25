from __future__ import annotations

from ytm_worker.redact import REDACTED, redact


def test_raw_header_lines() -> None:
    text = "Cookie: SID=abc; HSID=def\nAuthorization: SAPISIDHASH 123_456\nAccept: */*"
    out = redact(text)
    assert "abc" not in out and "def" not in out and "123_456" not in out
    assert f"Cookie: {REDACTED}" in out
    assert "Accept: */*" in out


def test_json_and_dict_keys() -> None:
    assert "secret" not in redact('{"cookie": "SID=secret", "x-goog-authuser": "0"}')
    assert "secret" not in redact("{'authorization': 'SAPISIDHASH secret'}")


def test_cookie_pairs_anywhere() -> None:
    out = redact("sent __Secure-3PAPISID=s3cr3t; LOGIN_INFO=AFmmF2 and PREF=f6=40000000")
    assert "s3cr3t" not in out and "AFmmF2" not in out
    assert "PREF=f6=40000000" in out  # not a session cookie


def test_netscape_cookie_lines() -> None:
    line = ".youtube.com\tTRUE\t/\tTRUE\t1798000000\tSAPISID\tvalue123"
    out = redact(line)
    assert "value123" not in out
    assert "SAPISID" in out


def test_googlevideo_query_string() -> None:
    url = "https://rr3---sn-abc.googlevideo.com/videoplayback?expire=1&ip=1.2.3.4&sig=xyz"
    out = redact(f"opening {url} now")
    assert "1.2.3.4" not in out and "sig=xyz" not in out
    assert "googlevideo.com/videoplayback?" in out
    assert out.endswith(" now")


def test_plain_text_untouched() -> None:
    text = "cookie file loaded; 12 cookies: fine"
    assert redact(text) == text
