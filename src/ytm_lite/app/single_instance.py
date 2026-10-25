"""One running copy per profile. A second launch hands its command (show the window,
play/pause, next, previous) to the running copy over a local socket and exits."""

from __future__ import annotations

import logging
import time
import zlib
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

COMMANDS = ("show", "play-pause", "next", "previous")


def server_name(config_dir: Path) -> str:
    """Per profile, so a throwaway profile (tests, measurements) doesn't find your app.
    A CRC rather than hashlib: hashlib loads OpenSSL (~5 MB private, 6 MB working set)."""
    checksum = zlib.crc32(str(config_dir.resolve()).encode("utf-8"))
    return f"ytm-lite-{checksum:08x}"


class SingleInstance(QObject):
    command_received = Signal(str)

    TIMEOUT_MS = 1000

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._server: QLocalServer | None = None

    def forward(self, command: str) -> bool:
        """Send `command` to a running copy. False if there is none."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(self.TIMEOUT_MS):
            return False
        socket.write(f"{command}\n".encode())
        # Hold on to the socket until the line is out: dropping it aborts the write.
        deadline = time.monotonic() + self.TIMEOUT_MS / 1000
        while socket.bytesToWrite() > 0 and time.monotonic() < deadline:
            socket.waitForBytesWritten(50)
        socket.disconnectFromServer()
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            socket.waitForDisconnected(self.TIMEOUT_MS)
        return True

    def listen(self) -> bool:
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._name):
            QLocalServer.removeServer(self._name)  # left over from a crash (Unix sockets)
            if not server.listen(self._name):
                log.warning("single-instance server failed: %s", server.errorString())
                return False
        server.newConnection.connect(self._accept)
        self._server = server
        return True

    def _accept(self) -> None:
        server = self._server
        while server is not None and server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                break
            socket.readyRead.connect(self._ready)
            socket.disconnected.connect(self._closed)
            # The other launch writes and hangs up at once: its line may be here already.
            self._read(socket)

    def _ready(self) -> None:
        socket = self.sender()
        if isinstance(socket, QLocalSocket):
            self._read(socket)

    def _closed(self) -> None:
        socket = self.sender()
        if isinstance(socket, QLocalSocket):
            self._read(socket)
            socket.deleteLater()

    def _read(self, socket: QLocalSocket) -> None:
        while socket.canReadLine():
            command = bytes(socket.readLine().data()).decode("utf-8", "replace").strip()
            if command in COMMANDS:
                self.command_received.emit(command)
            else:
                log.warning("ignoring unknown command %r", command[:40])
