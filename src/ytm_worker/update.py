"""Install the latest yt-dlp, and the yt-dlp-ejs it pins, into a versioned folder.

Runs in the worker, which already has requests (the app process never downloads code
itself). Both are pure-Python wheels: their SHA-256 must match PyPI's, every file must land
inside the target folder, and the new yt-dlp must import in a fresh interpreter before the
app switches to it. The app then restarts the worker with that folder first on its path.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

log = logging.getLogger("ytm_worker")

PYPI_LATEST = "https://pypi.org/pypi/{name}/json"
PYPI_RELEASE = "https://pypi.org/pypi/{name}/{version}/json"
TIMEOUT = 30
_EJS_PIN = re.compile(r"^yt-dlp-ejs\s*==\s*([\w.]+)\s*;\s*extra\s*==\s*['\"]default['\"]")
COMPLETE = ".complete"


class UpdateError(Exception):
    pass


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _get_json(session: Any, url: str) -> dict[str, Any]:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise UpdateError("PyPI sent an unexpected answer")
    return data


def wheel_file(release: dict[str, Any]) -> tuple[str, str]:
    """URL and SHA-256 of the pure-Python wheel of a PyPI release."""
    for file in release.get("urls") or ():
        name = str(file.get("filename", ""))
        if file.get("packagetype") == "bdist_wheel" and name.endswith("-py3-none-any.whl"):
            return str(file["url"]), str(file["digests"]["sha256"])
    info = release.get("info") or {}
    raise UpdateError(f"no wheel for {info.get('name')} {info.get('version')}")


def pinned_ejs(release: dict[str, Any]) -> str | None:
    for requirement in (release.get("info") or {}).get("requires_dist") or ():
        match = _EJS_PIN.match(str(requirement))
        if match:
            return match.group(1)
    return None


def _download(session: Any, url: str, sha256: str) -> bytes:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    data: bytes = response.content
    if hashlib.sha256(data).hexdigest() != sha256.lower():
        raise UpdateError(f"{url.rsplit('/', 1)[-1]} doesn't match PyPI's checksum")
    return data


def extract(wheel: bytes, target: Path) -> None:
    """Unpack the importable part of a wheel (not its .data scripts and manuals)."""
    root = target.resolve()
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        members = []
        for member in archive.infolist():
            if not (root / member.filename).resolve().is_relative_to(root):
                raise UpdateError(f"refusing a wheel with the path {member.filename!r}")
            if not member.filename.split("/", 1)[0].endswith(".data"):
                members.append(member)
        archive.extractall(root, members)


def _check_import(folder: Path) -> None:
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); import yt_dlp, yt_dlp_ejs; "
        "print(yt_dlp.version.__version__)"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(folder)],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=flags,
    )
    if result.returncode != 0:
        last = (result.stderr.strip().splitlines() or ["unknown error"])[-1]
        raise UpdateError(f"the new yt-dlp doesn't load ({last[:200]})")


def install_latest(args: dict[str, Any], session: Any = None) -> dict[str, Any]:
    """args: {"folder": where versions go, "current": the version in use}. Returns
    {"status": "current", "version"} or {"status": "installed", "version", "path"}."""
    folder = Path(str(args["folder"]))
    current = str(args.get("current") or "")
    if session is None:
        import requests

        session = requests.Session()
    release = _get_json(session, PYPI_LATEST.format(name="yt-dlp"))
    latest = str(release["info"]["version"])
    if current and version_key(latest) <= version_key(current):
        return {"status": "current", "version": current}
    target = folder / latest
    if (target / COMPLETE).exists():
        return {"status": "installed", "version": latest, "path": str(target)}
    staging = folder / f".{latest}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        url, sha256 = wheel_file(release)
        log.info("downloading yt-dlp %s", latest)
        extract(_download(session, url, sha256), staging)
        ejs = pinned_ejs(release)
        if ejs is not None:
            ejs_release = _get_json(session, PYPI_RELEASE.format(name="yt-dlp-ejs", version=ejs))
            url, sha256 = wheel_file(ejs_release)
            extract(_download(session, url, sha256), staging)
        _check_import(staging)
        (staging / COMPLETE).write_text(latest, encoding="utf-8")
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    log.info("yt-dlp %s installed", latest)
    return {"status": "installed", "version": latest, "path": str(target)}
