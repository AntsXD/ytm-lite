"""Find a JavaScript runtime for yt-dlp's YouTube challenge solver.

yt-dlp enables only Deno by default; Node must be named explicitly. We prefer the
Deno binary shipped next to the interpreter (the `deno` PyPI package in a venv, or the
copy bundled with the frozen build), then Deno or Node on PATH.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MIN_VERSIONS: dict[str, tuple[int, int, int]] = {"deno": (2, 3, 0), "node": (22, 0, 0)}
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class JsRuntime:
    name: str
    path: str
    version: str

    def as_ytdlp_option(self) -> dict[str, dict[str, str]]:
        return {self.name: {"path": self.path}}


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _probe(path: Path) -> str | None:
    """Return the runtime's version string, or None if it can't be run."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=flags,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_RE.search(out)
    return match.group(0) if match else None


def _version_ok(name: str, version: str) -> bool:
    match = _VERSION_RE.search(version)
    if not match:
        return False
    found = tuple(int(part) for part in match.groups())
    return found >= MIN_VERSIONS.get(name, (0, 0, 0))


def candidates(preferred: str = "auto") -> list[tuple[str, Path]]:
    """Runtimes to try, best first. `preferred` is "auto", a runtime name, or a path."""
    as_path = Path(preferred)
    if preferred not in ("auto", "deno", "node") and as_path.is_file():
        name = "node" if as_path.stem.lower().startswith("node") else "deno"
        return [(name, as_path)]

    found: list[tuple[str, Path]] = []
    exe_dir = Path(sys.executable).resolve().parent
    for folder in (exe_dir, exe_dir / "Scripts", exe_dir / "bin"):
        bundled = folder / _exe("deno")
        if bundled.is_file():
            found.append(("deno", bundled))
            break
    for name in ("deno", "node"):
        on_path = shutil.which(name)
        if on_path:
            found.append((name, Path(on_path)))

    if preferred in ("deno", "node"):
        found = [c for c in found if c[0] == preferred]
    return found


def detect(
    preferred: str = "auto", probe: Callable[[Path], str | None] = _probe
) -> JsRuntime | None:
    for name, path in candidates(preferred):
        version = probe(path)
        if version and _version_ok(name, version):
            return JsRuntime(name=name, path=str(path), version=version)
    return None
