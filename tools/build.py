"""Build the Windows package, dist/ytm-lite/: ytm-lite.exe and everything it needs.

    uv sync --group build
    uv run python tools/fetch_libmpv.py      # once
    uv run python tools/build.py             # the app and the worker runtime
    uv run python tools/build.py --worker    # only refresh the worker runtime

What goes where:
- ytm-lite.exe and Qt: the app (src/ytm_lite) compiled by Nuitka. yt-dlp, ytmusicapi
  and requests are never compiled in; only the worker uses them.
- lib/libmpv-2.dll, from vendor/ (Nuitka doesn't take DLLs as data files).
- worker/: python.org's embeddable CPython (pinned, SHA-256 checked) running the worker.
  Lib/site-packages holds ytm_worker, the modules of ytm_lite it shares (models, parse,
  codec) and yt-dlp, yt-dlp-ejs, ytmusicapi, requests and their dependencies: the very
  files installed in .venv (each package's RECORD lists them), precompiled. deno.exe sits
  next to pythonw.exe, where the worker looks for a JavaScript runtime.
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import py_compile
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BUILD = ROOT / "build"
DIST = ROOT / "dist" / "ytm-lite"
VERSION = "0.1.0"

# python.org's embeddable package for the Python the app is developed on; the worker's
# compiled modules (charset-normalizer) need the same minor version.
PYTHON_EMBED_URL = "https://www.python.org/ftp/python/3.14.7/python-3.14.7-embed-amd64.zip"
PYTHON_EMBED_SHA256 = "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"
PTH = "python314._pth"

# The worker's packages; their dependencies (without extras) come along.
WORKER_DISTRIBUTIONS = ("yt-dlp", "yt-dlp-ejs", "ytmusicapi", "certifi", "deno")
SHARED_MODULES = (
    "ytm_lite/__init__.py",
    "ytm_lite/app/__init__.py",
    "ytm_lite/app/models.py",
    "ytm_lite/app/parse.py",
    "ytm_lite/app/codec.py",
)
QT_PLUGINS = "platforms,styles,imageformats,tls"
# Qt files the app never uses. The OpenSSL TLS backend matters most: when some OpenSSL is
# on PATH, Qt loads it just to check the backend (~6 MB); the app uses Windows' schannel.
UNUSED_QT_FILES = (
    "PySide6/qt-plugins/tls/qopensslbackend.dll",
    "PySide6/qt-plugins/iconengines/qsvgicon.dll",
    *(
        f"PySide6/qt-plugins/imageformats/{name}.dll"
        for name in ("qgif", "qicns", "qico", "qpdf", "qsvg", "qtga", "qtiff", "qwbmp")
    ),
    *(
        f"PySide6/qt-plugins/platforms/{name}.dll"
        for name in ("qdirect2d", "qminimal", "qoffscreen")
    ),
    "qt6svg.dll",  # only the SVG plugins need it
)
NOT_IN_THE_APP = (
    "yt_dlp",
    "yt_dlp_ejs",
    "ytmusicapi",
    "requests",
    "urllib3",
    "charset_normalizer",
    "deno",
    "ytm_worker.worker",
    "ytm_worker.resolve",
    "ytm_worker.ytm",
    "ytm_worker.update",
    "ytm_lite.integrations.linux_mpris",
)


def log(text: str) -> None:
    print(f"[build] {text}", flush=True)


# -- the icon ------------------------------------------------------------------------------


def make_icon(path: Path) -> None:
    """The app icon (drawn by ui.icons) as a multi-size .ico of PNG images."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtGui import QGuiApplication

    from ytm_lite.ui import icons

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    sizes = (16, 20, 24, 32, 40, 48, 64, 256)
    images: list[bytes] = []
    for size in sizes:
        pixmap = icons.app_icon().pixmap(QSize(size, size))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        images.append(bytes(data.data()))
    del app
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, png in zip(sizes, images, strict=True):
        side = 0 if size >= 256 else size  # 0 means 256 in an ICO directory
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + b"".join(images))


# -- the app ---------------------------------------------------------------------------------


def build_app(icon: Path) -> Path:
    out = BUILD / "nuitka"
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        f"--include-qt-plugins={QT_PLUGINS}",
        "--noinclude-qt-translations",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={icon}",
        "--include-package=winrt",
        "--include-package-data=winrt",
        "--python-flag=-m",  # run the package, as `python -m ytm_lite` does
        *(f"--nofollow-import-to={name}" for name in NOT_IN_THE_APP),
        f"--output-dir={out}",
        "--output-filename=ytm-lite.exe",
        "--company-name=AntsXD",
        "--product-name=ytm-lite",
        f"--file-version={VERSION}",
        f"--product-version={VERSION}",
        "--file-description=ytm-lite, a lightweight YouTube Music player",
        str(SRC / "ytm_lite"),
    ]
    log("compiling the app with Nuitka (several minutes)")
    started = time.monotonic()
    subprocess.run(command, check=True, cwd=ROOT)
    log(f"Nuitka done in {time.monotonic() - started:.0f} s")
    return out / "ytm_lite.dist"


# -- the worker runtime ---------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def embedded_python() -> Path:
    archive = BUILD / "cache" / PYTHON_EMBED_URL.rsplit("/", 1)[-1]
    if not archive.exists() or _sha256(archive) != PYTHON_EMBED_SHA256:
        log(f"downloading {archive.name}")
        archive.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(PYTHON_EMBED_URL, archive)  # pinned and checked below
    if _sha256(archive) != PYTHON_EMBED_SHA256:
        raise SystemExit(f"{archive.name} doesn't match its pinned SHA-256")
    return archive


def _requirements(name: str) -> list[str]:
    found = []
    for requirement in metadata.requires(name) or ():
        spec, _, marker = requirement.partition(";")
        if "extra" in marker:
            continue
        found.append(spec.split("<")[0].split(">")[0].split("=")[0].split("[")[0].strip())
    return found


def worker_distributions() -> list[str]:
    """The worker's packages and everything they depend on, as installed in .venv."""
    wanted, seen = list(WORKER_DISTRIBUTIONS), list[str]()
    while wanted:
        name = wanted.pop()
        canonical = metadata.distribution(name).metadata["Name"]
        if canonical.lower() in (s.lower() for s in seen):
            continue
        seen.append(canonical)
        wanted += _requirements(canonical)
    return sorted(seen, key=str.lower)


def copy_installed(name: str, site: Path, worker: Path) -> int:
    """Copy one installed distribution's files (its RECORD) into `site`."""
    dist = metadata.distribution(name)
    copied = 0
    for file in dist.files or ():
        relative = str(file).replace("\\", "/")
        source = Path(str(dist.locate_file(file)))
        if "__pycache__" in relative or relative.endswith(".pyc"):
            continue
        if relative.startswith("../"):
            # Outside site-packages: only deno's executable is wanted (beside python).
            if source.name.lower() == "deno.exe":
                shutil.copy2(source, worker / source.name)
                copied += 1
            continue
        target = site / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def build_worker(worker: Path) -> None:
    if worker.exists():
        shutil.rmtree(worker)
    worker.mkdir(parents=True)
    with zipfile.ZipFile(embedded_python()) as archive:
        archive.extractall(worker)
    # The ._pth file is the whole module path of an embedded Python (site stays off).
    (worker / PTH).write_text("python314.zip\n.\nLib\\site-packages\n", encoding="ascii")
    site = worker / "Lib" / "site-packages"
    site.mkdir(parents=True)
    names = worker_distributions()
    total = sum(copy_installed(name, site, worker) for name in names)
    log(f"worker packages: {', '.join(names)} ({total} files)")
    shutil.copytree(
        SRC / "ytm_worker", site / "ytm_worker", ignore=shutil.ignore_patterns("__pycache__")
    )
    for relative in SHARED_MODULES:
        target = site / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / relative, target)
    if not (worker / "deno.exe").exists():
        raise SystemExit("deno.exe wasn't found among the deno package's files")
    ok = compileall.compile_dir(
        str(site),
        quiet=1,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    if not ok:
        raise SystemExit("compiling the worker's modules failed")


def smoke_test_worker(worker: Path) -> None:
    """The bundled worker starts, imports yt-dlp and ytmusicapi, and finds Deno."""
    probe = (
        "import ytm_worker.worker, ytm_worker.ytm, ytm_worker.resolve, yt_dlp, ytmusicapi;"
        "from ytm_worker import jsruntime; runtime = jsruntime.detect();"
        "print(yt_dlp.version.__version__, runtime and runtime.name)"
    )
    result = subprocess.run(
        [str(worker / "python.exe"), "-I", "-c", probe], capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise SystemExit(f"the bundled worker doesn't start:\n{result.stderr}")
    log(f"worker check: yt-dlp {result.stdout.strip()}")


# -- putting it together --------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help="only rebuild worker/")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("tools/build.py builds the Windows package")
    libmpv = ROOT / "vendor" / "libmpv-2.dll"
    if not libmpv.exists():
        raise SystemExit("vendor/libmpv-2.dll is missing: run tools/fetch_libmpv.py first")

    if not args.worker:
        icon = BUILD / "app.ico"
        make_icon(icon)
        compiled = build_app(icon)
        if DIST.exists():
            shutil.rmtree(DIST)
        DIST.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(compiled), DIST)
        for relative in UNUSED_QT_FILES:
            (DIST / relative).unlink(missing_ok=True)
        icon_engines = DIST / "PySide6" / "qt-plugins" / "iconengines"
        if icon_engines.is_dir() and not any(icon_engines.iterdir()):
            icon_engines.rmdir()
        (DIST / "lib").mkdir(exist_ok=True)
        shutil.copy2(libmpv, DIST / "lib" / libmpv.name)
        shutil.copy2(ROOT / "README.md", DIST / "README.md")
    elif not DIST.exists():
        raise SystemExit("build the app first (without --worker)")
    build_worker(DIST / "worker")
    smoke_test_worker(DIST / "worker")
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    log(f"done: {DIST} ({size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
