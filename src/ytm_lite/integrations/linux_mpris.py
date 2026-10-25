"""MPRIS on the D-Bus session bus (Linux): desktop media widgets, playerctl and media
keys all speak it. Uses QtDBus, so there's no extra dependency or thread: calls arrive
on the Qt event loop.

Spec: https://specifications.freedesktop.org/mpris-spec/latest/
Types matter to clients: mpris:trackid is an object path, xesam:artist a list of strings,
and the Position property and Seeked signal are int64 microseconds. PySide6 can't force
int64 inside a variant, so mpris:length goes out as int32 for tracks under ~35 minutes
(GNOME Shell, Plasma and playerctl read either). Checked over a real session bus in WSL.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import ClassInfo, Property, QMetaType, QObject, Signal, Slot
from PySide6.QtDBus import (
    QDBusAbstractAdaptor,
    QDBusArgument,
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
)

from ytm_lite.app.models import Track
from ytm_lite.app.playback import PlaybackState
from ytm_lite.app.thumbnails import sized_url
from ytm_lite.integrations.base import MediaControls

log = logging.getLogger(__name__)

PATH = "/org/mpris/MediaPlayer2"
SERVICE = "org.mpris.MediaPlayer2.ytm_lite"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
COVER_PX = 300

_STATUS = {
    PlaybackState.PLAYING: "Playing",
    PlaybackState.LOADING: "Playing",
    PlaybackState.PAUSED: "Paused",
    PlaybackState.STOPPED: "Stopped",
}


def track_path(track: Track) -> str:
    """An object path for a track (only [A-Za-z0-9_] in each element)."""
    safe = "".join(c if c.isalnum() else "_" for c in track.video_id)
    return f"/org/mpris/MediaPlayer2/ytm_lite/track/{safe or 'unknown'}"


def metadata(track: Track | None) -> dict[str, Any]:
    if track is None:
        return {"mpris:trackid": QDBusObjectPath(NO_TRACK)}
    data: dict[str, Any] = {
        "mpris:trackid": QDBusObjectPath(track_path(track)),
        "xesam:title": track.title,
        "xesam:artist": [a.name for a in track.artists] or [""],
        "xesam:url": track.link,
    }
    if track.album is not None:
        data["xesam:album"] = track.album.name
    if track.duration:
        data["mpris:length"] = track.duration * 1_000_000
    if track.thumbnail:
        data["mpris:artUrl"] = sized_url(track.thumbnail, COVER_PX)
    return data


@ClassInfo(**{"D-Bus Interface": ROOT_INTERFACE})  # type: ignore[arg-type]
class _RootAdaptor(QDBusAbstractAdaptor):  # type: ignore[operator]
    def __init__(self, controls: MprisControls) -> None:
        super().__init__(controls)
        self._controls = controls

    @Property(bool)
    def CanQuit(self) -> bool:
        return False

    @Property(bool)
    def CanRaise(self) -> bool:
        return True

    @Property(bool)
    def HasTrackList(self) -> bool:
        return False

    @Property(str)
    def Identity(self) -> str:
        return "ytm-lite"

    @Property(str)
    def DesktopEntry(self) -> str:
        return "ytm-lite"

    @Property("QStringList")  # type: ignore[arg-type]
    def SupportedUriSchemes(self) -> list[str]:
        return []

    @Property("QStringList")  # type: ignore[arg-type]
    def SupportedMimeTypes(self) -> list[str]:
        return []

    @Slot()
    def Raise(self) -> None:
        self._controls.raise_window.emit()

    @Slot()
    def Quit(self) -> None:
        pass


@ClassInfo(**{"D-Bus Interface": PLAYER_INTERFACE})  # type: ignore[arg-type]
class _PlayerAdaptor(QDBusAbstractAdaptor):  # type: ignore[operator]
    Seeked = Signal("qlonglong")  # type: ignore[arg-type]

    def __init__(self, controls: MprisControls) -> None:
        super().__init__(controls)
        self._controls = controls

    @Property(str)
    def PlaybackStatus(self) -> str:
        return _STATUS[self._controls.state]

    @Property(str)
    def LoopStatus(self) -> str:
        return "None"

    @Property(float)
    def Rate(self) -> float:
        return 1.0

    @Property(float)
    def MinimumRate(self) -> float:
        return 1.0

    @Property(float)
    def MaximumRate(self) -> float:
        return 1.0

    @Property(bool)
    def Shuffle(self) -> bool:
        return False

    @Property("QVariantMap")  # type: ignore[arg-type]
    def Metadata(self) -> dict[str, Any]:
        return metadata(self._controls.track)

    @Property(float)
    def Volume(self) -> float:
        return 1.0

    @Property("qlonglong")  # type: ignore[arg-type]
    def Position(self) -> int:
        return round(self._controls.position * 1_000_000)

    @Property(bool)
    def CanGoNext(self) -> bool:
        return True

    @Property(bool)
    def CanGoPrevious(self) -> bool:
        return True

    @Property(bool)
    def CanPlay(self) -> bool:
        return True

    @Property(bool)
    def CanPause(self) -> bool:
        return True

    @Property(bool)
    def CanSeek(self) -> bool:
        return self._controls.track is not None

    @Property(bool)
    def CanControl(self) -> bool:
        return True

    @Slot()
    def Next(self) -> None:
        self._controls.next.emit()

    @Slot()
    def Previous(self) -> None:
        self._controls.previous.emit()

    @Slot()
    def Pause(self) -> None:
        self._controls.pause.emit()

    @Slot()
    def PlayPause(self) -> None:
        self._controls.play_pause.emit()

    @Slot()
    def Stop(self) -> None:
        self._controls.pause.emit()

    @Slot()
    def Play(self) -> None:
        self._controls.play.emit()

    @Slot("qlonglong")
    def Seek(self, offset: int) -> None:
        self._controls.seek.emit(max(0.0, self._controls.position + offset / 1_000_000))

    @Slot(QDBusObjectPath, "qlonglong")
    def SetPosition(self, track_id: QDBusObjectPath, position: int) -> None:
        track = self._controls.track
        if track is not None and track_id.path() == track_path(track) and position >= 0:
            self._controls.seek.emit(position / 1_000_000)

    @Slot(str)
    def OpenUri(self, _uri: str) -> None:
        pass


class MprisControls(MediaControls):
    name = "mpris"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.track: Track | None = None
        self.state = PlaybackState.STOPPED
        self.position = 0.0
        self.registered = False
        self._root = _RootAdaptor(self)
        self._player = _PlayerAdaptor(self)
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.info("no D-Bus session bus; MPRIS off")
            return
        if not bus.registerObject(PATH, self):
            log.warning("couldn't export the MPRIS object: %s", bus.lastError().message())
            return
        if not bus.registerService(SERVICE):
            log.warning("couldn't claim %s: %s", SERVICE, bus.lastError().message())
            return
        self.registered = True
        log.info("MPRIS ready as %s", SERVICE)

    def update_track(self, track: Track | None) -> None:
        self.track = track
        self.position = 0.0
        self._changed(Metadata=metadata(track), CanSeek=track is not None)

    def update_state(self, state: PlaybackState) -> None:
        if _STATUS[state] != _STATUS[self.state]:
            self.state = state
            self._changed(PlaybackStatus=_STATUS[state])
        self.state = state

    def update_timeline(self, position: float, duration: float) -> None:
        jumped = abs(position - self.position) > 2
        self.position = position
        if jumped:
            self._player.Seeked.emit(round(position * 1_000_000))

    def _changed(self, **properties: Any) -> None:
        if not self.registered:
            return
        message = QDBusMessage.createSignal(
            PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged"
        )
        invalidated = QDBusArgument()  # an empty "as": a plain [] would go out as "av"
        invalidated.beginArray(QMetaType.Type.QString.value)
        invalidated.endArray()
        message.setArguments([PLAYER_INTERFACE, properties, invalidated])
        QDBusConnection.sessionBus().send(message)

    def close(self) -> None:
        if self.registered:
            bus = QDBusConnection.sessionBus()
            bus.unregisterService(SERVICE)
            bus.unregisterObject(PATH)
            self.registered = False
