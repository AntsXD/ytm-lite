"""App directories and user settings."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from platformdirs import PlatformDirs

from ytm_lite import APP_NAME

log = logging.getLogger(__name__)

# Points every app directory at one folder (used by tests and measurement runs).
HOME_ENV = "YTM_LITE_HOME"


@dataclass(frozen=True, slots=True)
class Paths:
    config: Path
    cache: Path
    logs: Path

    @property
    def settings_file(self) -> Path:
        return self.config / "settings.json"

    @property
    def state_file(self) -> Path:
        return self.config / "state.json"


def app_paths() -> Paths:
    home = os.environ.get(HOME_ENV)
    if home:
        root = Path(home)
        paths = Paths(config=root / "config", cache=root / "cache", logs=root / "logs")
    else:
        dirs = PlatformDirs(APP_NAME, appauthor=False)
        paths = Paths(
            config=Path(dirs.user_config_dir),
            cache=Path(dirs.user_cache_dir),
            logs=Path(dirs.user_log_dir),
        )
    for folder in (paths.config, paths.cache, paths.logs):
        folder.mkdir(parents=True, exist_ok=True)
    return paths


def is_frozen() -> bool:
    """True inside a Nuitka or PyInstaller build."""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def app_root() -> Path:
    """Repository root when running from source, the install folder when frozen."""
    if is_frozen():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parents[3]


def atomic_write_text(path: Path, text: str) -> None:
    """Write through a temp file and rename, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_json(path: Path) -> Any:
    """Parsed JSON, or None if the file is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("%s is unreadable; ignoring it", path.name, exc_info=True)
        return None


@dataclass(slots=True)
class Settings:
    close_to_tray: bool = True
    radio_autoplay: bool = True
    report_history: bool = True
    # The user chose "Continue without signing in": don't ask again at startup.
    skip_sign_in: bool = False
    # "auto", "deno", "node", or a path to a runtime executable.
    js_runtime: str = "auto"
    # yt-dlp player clients to use instead of the automatic choice.
    player_clients: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Settings:
        raw = read_json(path)
        if not isinstance(raw, dict):
            return cls()
        defaults = cls()
        values: dict[str, Any] = {}
        for f in fields(cls):
            value = raw.get(f.name)
            if isinstance(value, type(getattr(defaults, f.name))):
                values[f.name] = value
        return cls(**values)

    def save(self, path: Path) -> None:
        atomic_write_text(path, json.dumps(asdict(self), indent=2))
