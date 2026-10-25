from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from ytm_lite.app import images
from ytm_lite.app.images import ImageLoader, image_key, sized_url


@pytest.mark.parametrize(
    ("url", "px", "expected"),
    [
        (
            "https://lh3.googleusercontent.com/abc=w544-h544-l90-rj",
            60,
            "https://lh3.googleusercontent.com/abc=w60-h60-l90-rj",
        ),
        (
            "https://lh3.googleusercontent.com/abc",
            120,
            "https://lh3.googleusercontent.com/abc=w120-h120-l90-rj",
        ),
        (
            "https://yt3.ggpht.com/xyz=s88-c-k-c0x00ffffff-no-rj",
            90,
            "https://yt3.ggpht.com/xyz=w90-h90-l90-rj",
        ),
        (
            "https://i.ytimg.com/vi/4D7u5KF7SP8/sddefault.jpg?sqp=-oay&rs=AOn4",
            54,
            "https://i.ytimg.com/vi/4D7u5KF7SP8/default.jpg",
        ),
        (
            "https://i.ytimg.com/vi/4D7u5KF7SP8/hqdefault.jpg",
            300,
            "https://i.ytimg.com/vi/4D7u5KF7SP8/hqdefault.jpg",
        ),
        ("https://example.com/cover.png", 60, "https://example.com/cover.png"),
    ],
)
def test_sized_url(url: str, px: int, expected: str) -> None:
    assert sized_url(url, px) == expected


def test_memory_lru_is_capped_by_bytes(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(images, "MEMORY_BYTES", 3 * 100 * 100 * 4)
    loader = ImageLoader(tmp_path)
    monkeypatch.setattr(loader, "_pump", lambda: None)  # no real downloads in tests
    for i in range(5):
        loader._store(image_key(f"u{i}", 100), QPixmap(100, 100))
    count, size = loader.memory_usage()
    assert count == 3 and size == 3 * 100 * 100 * 4
    assert loader.get("u0", 100) is None  # evicted (and now queued for download)
    assert loader.get("u4", 100) is not None
    loader.clear_memory()
    assert loader.memory_usage() == (0, 0)


def test_get_without_url(qapp: QApplication, tmp_path: Path) -> None:
    assert ImageLoader(tmp_path).get(None, 60) is None
