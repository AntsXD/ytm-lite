"""Build the Windows package, dist/ytm-lite/: ytm-lite.exe and everything it needs.

    uv sync --group build
    uv run python tools/fetch_libmpv.py      # once
    uv run python tools/build.py             # the app and the worker runtime
    uv run python tools/build.py --zip       # ...and dist/ytm-lite-<version>-windows-x64.zip
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
- README.txt (starting and removing the app), LICENSE.txt (ytm-lite's) and
  THIRD-PARTY-NOTICES.txt with licenses/: every bundled component, its license texts and
  where its source is.
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import platform
import py_compile
import re
import shutil
import struct
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BUILD = ROOT / "build"
DIST = ROOT / "dist" / "ytm-lite"
LICENSES = ROOT / "tools" / "licenses"
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
VERSION: str = PROJECT["version"]

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

# shinchiro's libmpv builds turn on mpv's GPL option and build FFmpeg with --enable-gpl
# --enable-version3 (packages/mpv.cmake and packages/ffmpeg.cmake in mpv-winbuild-cmake).
LIBMPV_LICENSE = "GPL-3.0-or-later"
# License texts a package is offered under but its wheel doesn't carry (tools/licenses/).
EXTRA_LICENSE_TEXTS = {
    "pyside6-essentials": ("LGPL-3.0.txt", "GPL-3.0.txt"),
    "shiboken6": ("LGPL-3.0.txt", "GPL-3.0.txt"),
}
# Packages whose metadata doesn't say enough: what they are and where their source is.
ABOUT = {
    "pyside6-essentials": (
        "Qt for Python with the Qt 6 libraries, used under the LGPL-3.0",
        "https://download.qt.io/official_releases/QtForPython/",
        "https://download.qt.io/official_releases/qt/ (Qt's own third-party code: "
        "https://doc.qt.io/qt-6/licenses-used-in-qt.html)",
    ),
    "shiboken6": (
        "Qt for Python's runtime, used under the LGPL-3.0",
        "https://download.qt.io/official_releases/QtForPython/",
    ),
}
LICENSE_FILE = re.compile(r"(licen[cs]e|copying|notice|authors)", re.IGNORECASE)

PACKAGE_README = """\
ytm-lite {version}
{underline}

A lightweight YouTube Music player for Windows, built to run next to RAM-hungry games.
https://github.com/AntsXD/ytm-lite

Start it with ytm-lite.exe. Keep this folder together: the app needs lib and worker beside it.

The first start asks you to sign in (import from Firefox, paste headers or a cookies.txt);
you can also continue without signing in. The page above explains each way.

The app isn't code-signed, so Windows may warn the first time you start it: choose
"More info", then "Run anyway".

Where things go:
- Settings, your sign-in (encrypted), caches and logs: %LOCALAPPDATA%\\ytm-lite
- A Start menu entry, added on the first start so Windows can show the app's name in the
  media overlay: %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\ytm-lite.lnk
To remove ytm-lite, delete this folder, %LOCALAPPDATA%\\ytm-lite and that Start menu entry.

ytm-lite is unofficial and not affiliated with or endorsed by Google or YouTube.
Licenses: LICENSE.txt (ytm-lite's own, MIT) and THIRD-PARTY-NOTICES.txt (everything it
bundles, including libmpv under the GPL v3).
"""


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


def _name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9._-]+", requirement.strip())
    if match is None:
        raise SystemExit(f"can't read the requirement {requirement!r}")
    return match.group()


def _requirements(name: str) -> list[str]:
    found = []
    for requirement in metadata.requires(name) or ():
        spec, _, marker = requirement.partition(";")
        if "extra" in marker:
            continue
        found.append(_name(spec))
    return found


def closure(names: Iterable[str]) -> list[str]:
    """These installed distributions and everything they depend on (without extras)."""
    wanted, seen = list(names), list[str]()
    while wanted:
        name = wanted.pop()
        try:
            canonical = metadata.distribution(name).metadata["Name"]
        except metadata.PackageNotFoundError:
            continue  # a dependency for another platform or Python: not installed, not bundled
        if canonical.lower() in (s.lower() for s in seen):
            continue
        seen.append(canonical)
        wanted += _requirements(canonical)
    return sorted(seen, key=str.lower)


def worker_distributions() -> list[str]:
    """The worker's packages and everything they depend on, as installed in .venv."""
    return closure(WORKER_DISTRIBUTIONS)


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
        # Record paths relative to site-packages, not this machine's build folder; the
        # import system points tracebacks at the real files anyway.
        stripdir=str(site),
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


# -- licenses ---------------------------------------------------------------------------------


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _field(dist: metadata.Distribution, name: str) -> str:
    return str(dist.metadata[name]).strip() if name in dist.metadata else ""


def _license_of(dist: metadata.Distribution) -> str:
    if expression := _field(dist, "License-Expression"):
        return expression
    text = _field(dist, "License")
    if text and "\n" not in text and len(text) <= 60:
        return text
    classifiers = [
        entry.rsplit("::", 1)[-1].strip()
        for entry in dist.metadata.get_all("Classifier") or ()
        if entry.startswith("License ::")
    ]
    return ", ".join(classifiers) or "see its license texts"


def _source_of(dist: metadata.Distribution) -> str:
    urls: dict[str, str] = {}
    for entry in dist.metadata.get_all("Project-URL") or ():
        label, _, url = str(entry).partition(",")
        urls[label.strip().lower()] = url.strip()
    for label in ("source", "source code", "repository", "code", "homepage", "home"):
        if label in urls:
            return urls[label]
    homepage = _field(dist, "Home-page")
    return homepage or f"https://pypi.org/project/{dist.metadata['Name']}/{dist.version}/"


def _copy_license_texts(dist: metadata.Distribution, folder: Path) -> None:
    """The license files in a distribution's .dist-info folder."""
    for file in dist.files or ():
        relative = str(file).replace("\\", "/")
        if ".dist-info/" not in relative:
            continue
        inner = relative.split(".dist-info/", 1)[1]
        if not (inner.startswith("licenses/") or LICENSE_FILE.match(inner)):
            continue
        target = folder / inner.removeprefix("licenses/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(str(dist.locate_file(file))), target)


def libmpv_versions(dll: Path) -> tuple[str, str]:
    """mpv's and FFmpeg's versions, as the bundled libmpv reports them."""
    probe = (
        "import locale, os, sys; os.add_dll_directory(sys.argv[1]);"
        "os.environ['PATH'] = sys.argv[1] + os.pathsep + os.environ['PATH'];"
        "import mpv; locale.setlocale(locale.LC_NUMERIC, 'C');"
        "player = mpv.MPV(vid='no', ytdl='no', load_scripts='no');"
        "print(player.mpv_version); print(player.ffmpeg_version); player.terminate()"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(dll.parent)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    mpv_version, ffmpeg_version = result.stdout.strip().splitlines()[-2:]
    return mpv_version.removeprefix("mpv ").strip(), ffmpeg_version.strip()


def _commit(version: str) -> str:
    """The git commit in a `git describe` version such as v0.41.0-1227-ge471496c5."""
    match = re.search(r"-g([0-9a-f]{7,40})$", version)
    return match.group(1) if match else version


def write_notices() -> None:
    """README.txt, LICENSE.txt, THIRD-PARTY-NOTICES.txt and licenses/."""
    texts = DIST / "licenses"
    if texts.exists():
        shutil.rmtree(texts)
    texts.mkdir()
    title = f"ytm-lite {VERSION}"
    readme = PACKAGE_README.format(version=VERSION, underline="=" * len(title))
    (DIST / "README.txt").write_text(readme, encoding="utf-8")
    (DIST / "README.md").unlink(missing_ok=True)  # what builds before README.txt shipped
    shutil.copy2(ROOT / "LICENSE", DIST / "LICENSE.txt")
    sections: list[str] = []

    def add(heading: str, terms: str, folder: str, *sources: str) -> None:
        found = f"licenses/{folder}/" if (texts / folder).is_dir() else "none in its package"
        lines = [heading, f"  License: {terms}", f"  Texts:   {found}"]
        lines.append(f"  Source:  {sources[0]}")
        lines += [f"           {source}" for source in sources[1:]]
        sections.append("\n".join(lines))

    record = json.loads((ROOT / "vendor" / "libmpv.json").read_text(encoding="utf-8"))
    builder, _, rest = record["source"].partition("/releases/download/")
    mpv_version, ffmpeg_version = libmpv_versions(DIST / "lib" / "libmpv-2.dll")
    (texts / "libmpv").mkdir()
    shutil.copy2(LICENSES / "GPL-3.0.txt", texts / "libmpv" / "GPL-3.0.txt")
    add(
        f"mpv {mpv_version} and FFmpeg {ffmpeg_version} (lib/libmpv-2.dll)",
        LIBMPV_LICENSE,
        "libmpv",
        f"https://github.com/mpv-player/mpv/tree/{_commit(mpv_version)}",
        f"https://github.com/FFmpeg/FFmpeg/tree/{_commit(ffmpeg_version)}",
        f"{builder}/tree/{rest.split('/', 1)[0]} (the scripts this build was made with,",
        "which fetch and build every library inside it; the build itself:",
        f"{record['source']})",
    )

    app_python, worker_python = platform.python_version(), PYTHON_EMBED_URL.split("/")[-2]
    folder = f"Python-{app_python}"
    (texts / folder).mkdir()
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", texts / folder / "LICENSE.txt")
    where = (
        "python314.dll beside ytm-lite.exe, and worker/"
        if app_python == worker_python
        else f"python314.dll beside ytm-lite.exe; Python {worker_python} in worker/"
    )
    add(
        f"Python {app_python} ({where})",
        "PSF-2.0, with the licenses of the libraries in its Windows build (OpenSSL, libffi,"
        " SQLite and others) in the same file",
        folder,
        "https://www.python.org/downloads/source/",
    )

    dependencies = (_name(spec.partition(";")[0]) for spec in PROJECT["dependencies"])
    for name in closure(dependencies):
        dist = metadata.distribution(name)
        key = _normalized(name)
        folder = f"{dist.metadata['Name']}-{dist.version}"
        _copy_license_texts(dist, texts / folder)
        for extra in EXTRA_LICENSE_TEXTS.get(key, ()):
            (texts / folder).mkdir(parents=True, exist_ok=True)
            shutil.copy2(LICENSES / extra, texts / folder / extra)
        about, *sources = ABOUT.get(key, ("", _source_of(dist)))
        heading = f"{dist.metadata['Name']} {dist.version}" + (f" ({about})" if about else "")
        add(heading, _license_of(dist), folder, *sources)

    (texts / "material-icons").mkdir()
    shutil.copy2(LICENSES / "Apache-2.0.txt", texts / "material-icons" / "Apache-2.0.txt")
    add(
        "Material Icons by Google (the toolbar icons' path data, inside ytm-lite.exe)",
        "Apache-2.0",
        "material-icons",
        "https://github.com/google/material-design-icons",
    )

    nuitka = metadata.distribution("nuitka")
    folder = f"Nuitka-{nuitka.version}"
    for file in nuitka.files or ():
        if file.name == "LICENSE-RUNTIME.txt":
            (texts / folder).mkdir(exist_ok=True)
            shutil.copy2(Path(str(nuitka.locate_file(file))), texts / folder / file.name)
    add(
        f"Nuitka {nuitka.version}'s runtime library (compiled into ytm-lite.exe)",
        "AGPL-3.0 with the Nuitka Runtime Library Exception, which lets a compiled program be"
        " conveyed under terms of its author's choice",
        folder,
        "https://github.com/Nuitka/Nuitka",
    )

    header = (
        f"{title} for Windows: third-party notices\n\n"
        "ytm-lite's own code is under the MIT License (LICENSE.txt); its source is at\n"
        "https://github.com/AntsXD/ytm-lite. This package also contains the components below,\n"
        "each under its own license, with their license texts in the licenses folder.\n\n"
        "lib/libmpv-2.dll is under the GNU GPL, version 3 or later, and ytm-lite loads it, so\n"
        "this package as a whole is distributed under the terms of the GPL v3. The links below\n"
        "lead to the source of every component; if one stops working, open an issue at\n"
        "https://github.com/AntsXD/ytm-lite/issues.\n"
    )
    notices = header + "\n\n" + "\n\n".join(sections) + "\n"
    (DIST / "THIRD-PARTY-NOTICES.txt").write_text(notices, encoding="utf-8")
    log(f"notices: {len(sections)} components")


# -- putting it together --------------------------------------------------------------------


def make_zip() -> Path:
    """dist/ytm-lite-<version>-windows-x64.zip, holding the ytm-lite folder."""
    archive = DIST.parent / f"ytm-lite-{VERSION}-windows-x64.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as packed:
        for file in sorted(DIST.rglob("*")):
            if file.is_file():
                packed.write(file, Path(DIST.name) / file.relative_to(DIST))
    log(f"{archive.name}: {archive.stat().st_size / 1e6:.0f} MB, SHA-256 {_sha256(archive)}")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help="only rebuild worker/")
    parser.add_argument("--zip", action="store_true", help="also pack the release zip")
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
    elif not DIST.exists():
        raise SystemExit("build the app first (without --worker)")
    build_worker(DIST / "worker")
    smoke_test_worker(DIST / "worker")
    write_notices()
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    log(f"done: {DIST} ({size / 1e6:.0f} MB)")
    if args.zip:
        make_zip()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
