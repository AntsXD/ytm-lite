"""yt-dlp updates: the worker side against a fake PyPI (no network), and the app side's
bookkeeping (which folder the worker runs with)."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtWidgets import QApplication

from ytm_lite.app.api import Api
from ytm_lite.app.updater import YtDlpUpdater
from ytm_lite.app.worker_client import WorkerProcess
from ytm_lite.app.workers import Workers
from ytm_worker import update


def wheel(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


YTDLP = wheel(
    {
        "yt_dlp/__init__.py": "from . import version\n",
        "yt_dlp/version.py": "__version__ = '2099.1.1'\n",
        "yt_dlp-2099.1.1.dist-info/METADATA": "Name: yt-dlp\nVersion: 2099.1.1\n",
    }
)
EJS = wheel({"yt_dlp_ejs/__init__.py": "", "yt_dlp_ejs-9.9.dist-info/METADATA": "Name: x\n"})


def release(name: str, version: str, data: bytes, requires: list[str] | None = None) -> Any:
    return {
        "info": {"name": name, "version": version, "requires_dist": requires or []},
        "urls": [
            {
                "filename": f"{name.replace('-', '_')}-{version}-py3-none-any.whl",
                "packagetype": "bdist_wheel",
                "url": f"https://files/{name}-{version}.whl",
                "digests": {"sha256": hashlib.sha256(data).hexdigest()},
            }
        ],
    }


class FakeResponse:
    def __init__(self, body: Any) -> None:
        self.body = body
        self.content = body if isinstance(body, bytes) else b""

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self.body


class FakePyPI:
    def __init__(self, ytdlp: bytes = YTDLP, served: bytes | None = None) -> None:
        pin = ['yt-dlp-ejs==9.9; extra == "default"', "brotli; extra == 'default'"]
        self.routes: dict[str, Any] = {
            "https://pypi.org/pypi/yt-dlp/json": release("yt-dlp", "2099.1.1", ytdlp, pin),
            "https://pypi.org/pypi/yt-dlp-ejs/9.9/json": release("yt-dlp-ejs", "9.9", EJS),
            "https://files/yt-dlp-2099.1.1.whl": served if served is not None else ytdlp,
            "https://files/yt-dlp-ejs-9.9.whl": EJS,
        }
        self.got: list[str] = []

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.got.append(url)
        return FakeResponse(self.routes[url])


def test_installs_a_newer_yt_dlp_and_its_pinned_ejs(tmp_path: Path) -> None:
    pypi = FakePyPI()
    result = update.install_latest({"folder": str(tmp_path), "current": "2026.10.21"}, pypi)
    assert result == {
        "status": "installed",
        "version": "2099.1.1",
        "path": str(tmp_path / "2099.1.1"),
    }
    folder = tmp_path / "2099.1.1"
    assert (folder / "yt_dlp" / "version.py").exists() and (folder / "yt_dlp_ejs").is_dir()
    assert (folder / update.COMPLETE).exists()
    assert [p.name for p in tmp_path.iterdir()] == ["2099.1.1"]  # no staging left over
    again = update.install_latest({"folder": str(tmp_path), "current": "2099.1.1"}, pypi)
    assert again == {"status": "current", "version": "2099.1.1"}


def test_refuses_a_wheel_that_doesnt_match_its_checksum(tmp_path: Path) -> None:
    pypi = FakePyPI(served=YTDLP + b"tampered")
    with pytest.raises(update.UpdateError, match="checksum"):
        update.install_latest({"folder": str(tmp_path), "current": "1"}, pypi)
    assert list(tmp_path.iterdir()) == []


def test_refuses_files_outside_the_folder(tmp_path: Path) -> None:
    evil = wheel({"../../evil.py": "", "yt_dlp/__init__.py": ""})
    with pytest.raises(update.UpdateError, match="refusing"):
        update.install_latest({"folder": str(tmp_path / "v"), "current": "1"}, FakePyPI(evil))
    assert not (tmp_path / "evil.py").exists()


def test_refuses_a_yt_dlp_that_doesnt_import(tmp_path: Path) -> None:
    broken = wheel({"yt_dlp/__init__.py": "raise ImportError('nope')\n"})
    with pytest.raises(update.UpdateError, match="doesn't load"):
        update.install_latest({"folder": str(tmp_path), "current": "1"}, FakePyPI(broken))


def test_version_order() -> None:
    assert update.version_key("2026.10.21") < update.version_key("2026.10.21.1")
    assert update.version_key("2026.9.30") < update.version_key("2026.10.1")


class FakeWorker:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {"versions": {"yt-dlp": "2026.10.21"}}
        self.packages: list[Path | None] = []

    def set_packages(self, folder: Path | None) -> None:
        self.packages.append(folder)


def test_the_app_switches_the_worker_to_the_new_version(qapp: QApplication, tmp_path: Path) -> None:
    folder = tmp_path / "updates"
    (folder / "2000.1.1").mkdir(parents=True)  # an older download
    worker = FakeWorker()
    updater = YtDlpUpdater(
        folder, cast(WorkerProcess, worker), cast(Api, None), cast(Workers, None)
    )
    assert worker.packages == [None] and updater.version() == "2026.10.21"
    assert not (folder / "2000.1.1").exists()  # not in use: removed at startup
    messages: list[str] = []
    updater.status.connect(messages.append)
    (folder / "2099.1.1").mkdir()
    (folder / "2099.1.1" / update.COMPLETE).write_text("2099.1.1")
    updater._checked({"status": "installed", "version": "2099.1.1"})
    assert worker.packages[-1] == folder / "2099.1.1"
    assert updater.version() == "2099.1.1"
    assert json.loads((folder / "active.json").read_text()) == {"version": "2099.1.1"}
    assert "Updated to yt-dlp 2099.1.1" in messages[-1]
    # Next start: the download is used again.
    again = FakeWorker()
    YtDlpUpdater(folder, cast(WorkerProcess, again), cast(Api, None), cast(Workers, None))
    assert again.packages == [folder / "2099.1.1"]
