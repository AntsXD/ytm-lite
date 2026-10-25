from __future__ import annotations

import sys
from pathlib import Path

from ytm_lite.app.secrets import SecretStore, protect


def test_roundtrip(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "auth.bin")
    assert store.load() is None
    store.save(b"cookie=secret")
    assert store.load() == b"cookie=secret"
    store.clear()
    assert store.load() is None


def test_file_is_not_plain_text(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "auth.bin")
    store.save(b"cookie=secret")
    raw = (tmp_path / "auth.bin").read_bytes()
    if sys.platform == "win32":
        assert b"secret" not in raw  # DPAPI-encrypted
    else:
        assert (tmp_path / "auth.bin").stat().st_mode & 0o777 == 0o600


def test_unknown_or_corrupt_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "auth.bin"
    path.write_bytes(b"something else")
    assert SecretStore(path).load() is None
    if sys.platform == "win32":
        path.write_bytes(b"YTML1\n" + b"not a dpapi blob")
        assert SecretStore(path).load() is None


def test_protect_is_not_identity_on_windows() -> None:
    if sys.platform == "win32":
        assert protect(b"abc") != b"abc"
