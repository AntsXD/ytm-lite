"""Download the pinned libmpv build into vendor/ and verify it.

Windows only; on Linux install your distribution's libmpv package instead.
The archive is a 7z, which the bsdtar that ships with Windows 10/11 can unpack.

Usage: uv run python tools/fetch_libmpv.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# shinchiro/mpv-winbuild-cmake is the libmpv build mpv.io links to for Windows.
# To update: pick a newer dated release, copy the mpv-dev-x86_64 asset URL and its
# sha256 digest from the GitHub release page, then rerun this script.
ARCHIVE_URL = (
    "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261021/"
    "mpv-dev-x86_64-20261021-git-e471496c56.7z"
)
ARCHIVE_SHA256 = "efc719eff3dc5954b4a5efc800632808bcec01cc9034940dd14e5eb7fe9ea633"
MEMBER = "libmpv-2.dll"

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if sys.platform != "win32":
        print("On Linux, install libmpv from your package manager (e.g. libmpv2).")
        return 0

    target = VENDOR_DIR / MEMBER
    manifest = VENDOR_DIR / "libmpv.json"
    if target.exists() and manifest.exists():
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        if recorded.get("archive_sha256") == ARCHIVE_SHA256 and recorded.get(
            "dll_sha256"
        ) == sha256_of(target):
            print(f"{target} is already up to date.")
            return 0

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "mpv-dev.7z"
        print(f"Downloading {ARCHIVE_URL}")
        with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as resp, archive.open("wb") as out:
            shutil.copyfileobj(resp, out)

        actual = sha256_of(archive)
        if actual != ARCHIVE_SHA256:
            print(f"sha256 mismatch: expected {ARCHIVE_SHA256}, got {actual}", file=sys.stderr)
            return 1

        subprocess.run(["tar", "-xf", str(archive), "-C", tmp, MEMBER], check=True)
        shutil.move(str(Path(tmp) / MEMBER), target)

    manifest.write_text(
        json.dumps(
            {
                "source": ARCHIVE_URL,
                "archive_sha256": ARCHIVE_SHA256,
                "dll_sha256": sha256_of(target),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Installed {target} ({target.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
