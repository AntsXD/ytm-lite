"""libmpv playback through python-mpv.

The mpv instance is created on first use and can be released when idle, so libmpv's
memory isn't held while nothing plays. mpv callbacks run on python-mpv's event thread
and only emit Qt signals, which Qt queues onto the UI thread.
"""

from __future__ import annotations

import locale
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

# Audio only, no built-in Lua scripts (each is its own VM and thread), and a fixed
# output format so switching between Opus (48 kHz) and AAC (44.1 kHz) stays gapless.
MPV_OPTIONS: dict[str, str] = {
    "vid": "no",
    "video": "no",
    "audio-display": "no",
    "ytdl": "no",
    "config": "no",
    "load-scripts": "no",
    "osc": "no",
    "load-stats-overlay": "no",
    "load-console": "no",
    "load-select": "no",
    "load-positioning": "no",
    "load-commands": "no",
    "load-auto-profiles": "no",
    "input-default-bindings": "no",
    "terminal": "no",
    "idle": "yes",
    "cache": "yes",
    # 4 MiB is about 4 minutes of 134k Opus (2 minutes at 256k). The spec's 16/4 MiB cost
    # ~40 MB more, and with a cache smaller than the track mpv only opens the armed next
    # file once the current one is fully read, so normally just one stream is buffered.
    "demuxer-max-bytes": "4MiB",
    "demuxer-max-back-bytes": "1MiB",
    "prefetch-playlist": "yes",
    "gapless-audio": "yes",
    "audio-samplerate": "48000",
    "audio-channels": "stereo",
    "audio-client-name": "ytm-lite",
    "network-timeout": "10",
    "volume-max": "100",
}

# Settings → Playback → "Even out loud and quiet parts": FFmpeg's dynamic normalizer.
DYNAMIC_NORMALIZATION = "lavfi=[dynaudnorm]"
# Enough of an mpv to list audio devices.
BARE_OPTIONS = {
    "vid": "no",
    "video": "no",
    "ytdl": "no",
    "config": "no",
    "load-scripts": "no",
    "osc": "no",
    "terminal": "no",
    "idle": "yes",
}

_START_FILE = 6
_END_FILE = 7
_PLAYBACK_RESTART = 21
END_REASONS = {0: "eof", 1: "restarted", 2: "aborted", 3: "quit", 4: "error", 5: "redirect"}
_LOG_LEVELS = {
    "fatal": logging.CRITICAL,
    "error": logging.ERROR,
    "warn": logging.WARNING,
    "info": logging.INFO,
}
_STREAM_ERROR_HINTS = ("HTTP error", "Failed to open", "Forbidden", "Connection reset")


def tls_options() -> dict[str, str]:
    """mpv doesn't check the stream servers' certificates unless told to. FFmpeg's TLS
    in the Windows libmpv build is OpenSSL with no CA store of its own, so mpv is given
    certifi's bundle (the same one the worker's requests use) on every platform."""
    import certifi

    return {"tls-verify": "yes", "tls-ca-file": certifi.where()}


def mpv_option_string(options: Mapping[str, str]) -> str:
    """Per-file loadfile options. Values are length-quoted (%n%) so commas are safe."""
    return ",".join(f"{key}=%{len(value.encode())}%{value}" for key, value in options.items())


def add_libmpv_dir(directory: Path) -> None:
    """Make libmpv-2.dll in `directory` loadable (python-mpv searches PATH)."""
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
    if sys.platform == "win32":
        os.add_dll_directory(str(directory))


class Player(QObject):
    file_started = Signal(int)  # mpv playlist_entry_id
    file_ended = Signal(int, str, str)  # playlist_entry_id, reason, error text
    playback_restarted = Signal()  # audio is playing after a load or seek
    pause_changed = Signal(bool)
    idle_changed = Signal(bool)
    duration_changed = Signal(object)  # float | None
    stream_error = Signal(str)  # network/HTTP failure reported by mpv's log
    released = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mpv: Any = None
        self._volume = 70.0
        self._audio_device = "auto"
        self._dynamic_normalization = False

    # -- lifecycle --------------------------------------------------------------------

    def active(self) -> bool:
        return self._mpv is not None

    def _ensure(self) -> Any:
        if self._mpv is None:
            # mpv_create() returns NULL unless numbers use the "C" locale, and Qt (on
            # Linux) or other libraries may have changed it.
            locale.setlocale(locale.LC_NUMERIC, "C")
            import mpv

            all_options = {
                **MPV_OPTIONS,
                **tls_options(),
                "audio-device": self._audio_device,
                "af": DYNAMIC_NORMALIZATION if self._dynamic_normalization else "",
            }
            options = {key.replace("-", "_"): value for key, value in all_options.items()}
            instance = mpv.MPV(log_handler=self._on_log, loglevel="warn", **options)
            instance.volume = self._volume
            instance.register_event_callback(self._on_event)
            instance.observe_property("pause", self._on_pause)
            instance.observe_property("idle-active", self._on_idle_active)
            instance.observe_property("duration", self._on_duration)
            self._mpv = instance
            log.info("libmpv ready (%s)", instance.mpv_version)
        return self._mpv

    def release(self) -> None:
        """Destroy the mpv instance. Never call this from an mpv callback."""
        instance, self._mpv = self._mpv, None
        if instance is not None:
            instance.terminate()
            log.info("libmpv released")
            self.released.emit()

    # -- commands -----------------------------------------------------------------------

    def load(self, url: str, *, append: bool, options: Mapping[str, str]) -> int:
        """Load a URL; returns its mpv playlist entry id.

        `append` adds it after the current entry (and starts it if mpv is idle).
        """
        result = self._ensure().command(
            "loadfile",
            url,
            "append-play" if append else "replace",
            "-1",
            mpv_option_string(options),
        )
        return int(result["playlist_entry_id"])

    def playlist_next(self) -> None:
        self._command("playlist-next", "weak")

    def playlist_clear(self) -> None:
        """Remove every entry except the one playing."""
        self._command("playlist-clear")

    def stop(self) -> None:
        self._command("stop")

    def seek(self, seconds: float) -> None:
        self._command("seek", f"{max(0.0, seconds):.3f}", "absolute+exact")

    def set_paused(self, paused: bool) -> None:
        if self._mpv is not None:
            self._mpv.pause = paused

    def paused(self) -> bool:
        return bool(self._property("pause"))

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(100.0, volume))
        if self._mpv is not None:
            self._mpv.volume = self._volume

    def volume(self) -> float:
        return self._volume

    def set_gain(self, db: float) -> None:
        """The current file's gain (normalization); the next file brings its own."""
        if self._mpv is not None:
            self._set_property("volume-gain", db)

    def set_dynamic_normalization(self, on: bool) -> None:
        self._dynamic_normalization = on
        if self._mpv is not None:
            self._set_property("af", DYNAMIC_NORMALIZATION if on else "")

    def set_audio_device(self, name: str) -> None:
        self._audio_device = name or "auto"
        if self._mpv is not None:
            self._set_property("audio-device", self._audio_device)

    def audio_devices(self) -> list[tuple[str, str]]:
        """(name, description) of each audio output. Without a running mpv, a bare one
        is made just to ask, and destroyed again."""
        instance, temporary = self._mpv, None
        if instance is None:
            locale.setlocale(locale.LC_NUMERIC, "C")
            import mpv

            options = {key.replace("-", "_"): value for key, value in BARE_OPTIONS.items()}
            instance = temporary = mpv.MPV(**options)
        try:
            devices = instance._get_property("audio-device-list") or []
        except Exception:
            log.debug("audio-device-list failed", exc_info=True)
            devices = []
        finally:
            if temporary is not None:
                temporary.terminate()
        return [
            (str(d.get("name", "")), str(d.get("description") or d.get("name", "")))
            for d in devices
            if isinstance(d, dict) and d.get("name")
        ]

    def set_loop_file(self, loop: bool) -> None:
        if self._mpv is not None:
            self._mpv.loop_file = "inf" if loop else "no"

    def position(self) -> float | None:
        value = self._property("time-pos")
        return float(value) if value is not None else None

    def duration(self) -> float | None:
        value = self._property("duration")
        return float(value) if value is not None else None

    def stats(self) -> dict[str, Any]:
        if self._mpv is None:
            return {"active": False}
        cache = self._property("demuxer-cache-state") or {}
        params = self._property("audio-params") or {}
        return {
            "active": True,
            "cache_bytes": cache.get("fw-bytes"),
            "codec": self._property("audio-codec-name"),
            "samplerate": params.get("samplerate"),
            "ao": self._property("current-ao"),
            "device": self._property("audio-device"),
            "gain": self._property("volume-gain"),
            "filters": "dynaudnorm" if self._dynamic_normalization else "none",
        }

    def _command(self, *args: str) -> None:
        if self._mpv is None:
            return
        try:
            self._mpv.command(*args)
        except Exception as exc:  # mpv rejects e.g. seeks while nothing is loaded
            log.debug("mpv command %s failed: %s", args[0], exc)

    def _set_property(self, name: str, value: Any) -> None:
        try:
            self._mpv._set_property(name, value)
        except Exception as exc:
            log.warning("mpv rejected %s=%r: %s", name, value, exc)

    def _property(self, name: str) -> Any:
        if self._mpv is None:
            return None
        try:
            return self._mpv._get_property(name)
        except Exception:
            return None

    # -- mpv event thread -----------------------------------------------------------------

    def _on_event(self, event: Any) -> None:
        event_id = event.event_id.value
        if event_id == _START_FILE:
            self.file_started.emit(int(event.data.playlist_entry_id))
        elif event_id == _END_FILE:
            data = event.data
            reason = END_REASONS.get(data.reason, "unknown")
            error = ""
            if reason == "error":
                import mpv

                try:
                    error = mpv._mpv_error_string(data.error).decode("utf-8")
                except Exception:
                    error = f"mpv error {data.error}"
            self.file_ended.emit(int(data.playlist_entry_id), reason, error)
        elif event_id == _PLAYBACK_RESTART:
            self.playback_restarted.emit()

    def _on_pause(self, _name: str, value: Any) -> None:
        self.pause_changed.emit(bool(value))

    def _on_idle_active(self, _name: str, value: Any) -> None:
        self.idle_changed.emit(bool(value))

    def _on_duration(self, _name: str, value: Any) -> None:
        self.duration_changed.emit(float(value) if value is not None else None)

    def _on_log(self, level: str, prefix: str, text: str) -> None:
        text = text.rstrip()
        log.log(_LOG_LEVELS.get(level, logging.DEBUG), "mpv/%s: %s", prefix, text)
        # A mid-stream 403 often ends the file with reason "eof", so watch the log for it.
        if level in ("error", "warn") and any(hint in text for hint in _STREAM_ERROR_HINTS):
            self.stream_error.emit(text)
