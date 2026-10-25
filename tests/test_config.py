from __future__ import annotations

from pathlib import Path

from ytm_lite.app.config import Settings, atomic_write_text
from ytm_lite.app.models import format_duration
from ytm_lite.app.player import mpv_option_string


def test_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings(close_to_tray=False, player_clients=["tv"])
    settings.save(path)
    assert Settings.load(path) == settings


def test_settings_ignore_bad_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"close_to_tray": "yes", "js_runtime": "node", "unknown": 1}', "utf-8")
    loaded = Settings.load(path)
    assert loaded.close_to_tray is True  # wrong type falls back to the default
    assert loaded.js_runtime == "node"


def test_settings_corrupt_or_missing(tmp_path: Path) -> None:
    assert Settings.load(tmp_path / "missing.json") == Settings()
    corrupt = tmp_path / "settings.json"
    corrupt.write_text("{not json", "utf-8")
    assert Settings.load(corrupt) == Settings()


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text("utf-8") == "two"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_mpv_option_string_quotes_lengths() -> None:
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    assert mpv_option_string({"user-agent": ua, "start": "1.5"}) == (
        f"user-agent=%{len(ua)}%{ua},start=%3%1.5"
    )
    assert mpv_option_string({"referrer": "é"}) == "referrer=%2%é"  # UTF-8 byte length
    assert mpv_option_string({}) == ""


def test_format_duration() -> None:
    assert format_duration(None) == "–:––"
    assert format_duration(65) == "1:05"
    assert format_duration(3725) == "1:02:05"
