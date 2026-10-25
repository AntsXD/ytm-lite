"""Settings, applied when you press OK. A hotkey Windows refuses (another app owns it)
is shown next to its row, and the dialog stays open."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.config import Settings
from ytm_lite.integrations.hotkeys import ACTION_LABELS, SUPPORTED
from ytm_lite.ui.theme import TEXT_DIM
from ytm_lite.ui.widgets import ToggleSwitch

if TYPE_CHECKING:
    from ytm_lite.app.updater import YtDlpUpdater

NORMALIZE_CHOICES = (
    ("youtube", "Like YouTube Music"),
    ("dynamic", "Even out loud and quiet parts"),
    ("off", "Off"),
)
RUNTIME_CHOICES = (("auto", "Automatic"), ("deno", "Deno"), ("node", "Node.js"))
PROBLEM_STYLE = "color: #f28b82;"


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {TEXT_DIM};")
    return label


def _page(*rows: QWidget, stretch: bool = True) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    for row in rows:
        layout.addWidget(row)
    if stretch:
        layout.addStretch(1)
    return page


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        *,
        apply: Callable[[Settings], dict[str, str]],
        audio_devices: Callable[[], list[tuple[str, str]]],
        updater: YtDlpUpdater | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self._settings = settings
        self._apply = apply

        # General
        self.close_to_tray = ToggleSwitch("Keep playing in the tray when the window is closed")
        self.close_to_tray.setChecked(settings.close_to_tray)
        self.autoplay = ToggleSwitch("Autoplay: continue with similar songs when the queue ends")
        self.autoplay.setChecked(settings.radio_autoplay)
        self.history = ToggleSwitch("Add songs you play to your YouTube Music history")
        self.history.setChecked(settings.report_history)
        general = _page(
            self.close_to_tray,
            self.autoplay,
            self.history,
            _hint(
                "A song counts once it has played for 10 seconds, as in YouTube Music: it "
                "shows up in your history, your recommendations and your yearly Recap. "
                "Needs a sign-in."
            ),
        )

        # Playback
        self.normalize = QComboBox()
        for value, text in NORMALIZE_CHOICES:
            self.normalize.addItem(text, value)
        self.normalize.setCurrentIndex(max(0, self.normalize.findData(settings.normalize)))
        self.device = QComboBox()
        self.device.addItem("System default", "auto")
        for name, description in audio_devices():
            if name != "auto":
                self.device.addItem(description or name, name)
        index = self.device.findData(settings.audio_device)
        if index < 0:  # a device that's unplugged right now: keep the choice
            self.device.addItem(f"{settings.audio_device} (not connected)", settings.audio_device)
            index = self.device.count() - 1
        self.device.setCurrentIndex(index)
        playback_form = QWidget()
        form = QFormLayout(playback_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Volume normalization", self.normalize)
        form.addRow("Output device", self.device)
        playback = _page(
            playback_form,
            _hint(
                "Like YouTube Music: loud songs are turned down to a common level, using "
                "the loudness YouTube measured for each song. Even out: mpv's dynamic "
                "normalizer, which also lifts quiet passages (uses a little more CPU)."
            ),
        )

        # Hotkeys
        self._keys: dict[str, QKeySequenceEdit] = {}
        self._problems: dict[str, QLabel] = {}
        hotkey_rows = QWidget()
        grid = QFormLayout(hotkey_rows)
        grid.setContentsMargins(0, 0, 0, 0)
        for action, label in ACTION_LABELS.items():
            edit = QKeySequenceEdit(QKeySequence(settings.hotkeys.get(action, "")))
            edit.setMaximumSequenceLength(1)
            edit.setClearButtonEnabled(True)
            edit.setEnabled(SUPPORTED)
            problem = QLabel()
            problem.setStyleSheet(PROBLEM_STYLE)
            problem.hide()
            cell = QWidget()
            column = QVBoxLayout(cell)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            column.addWidget(edit)
            column.addWidget(problem)
            grid.addRow(label, cell)
            self._keys[action] = edit
            self._problems[action] = problem
        if SUPPORTED:
            hotkey_hint = _hint(
                "These work everywhere, even while a game has focus. Use Ctrl, Alt or the "
                "Windows key with a letter or number; F-keys work on their own. Media keys "
                "always work."
            )
        else:
            hotkey_hint = _hint(
                "Global hotkeys aren't available on this system. Point your desktop's "
                "keyboard shortcuts at ytm-lite --play-pause, --next or --previous instead."
            )
        self._tabs = QTabWidget()
        hotkeys = _page(hotkey_hint, hotkey_rows)

        # Advanced
        self.runtime = QComboBox()
        for value, text in RUNTIME_CHOICES:
            self.runtime.addItem(text, value)
        if self.runtime.findData(settings.js_runtime) < 0:
            self.runtime.addItem(settings.js_runtime, settings.js_runtime)
        self.runtime.setCurrentIndex(self.runtime.findData(settings.js_runtime))
        self.clients = QLineEdit(", ".join(settings.player_clients))
        self.clients.setPlaceholderText("automatic")
        advanced_form = QWidget()
        form = QFormLayout(advanced_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("JavaScript runtime", self.runtime)
        form.addRow("yt-dlp player clients", self.clients)
        advanced_rows: list[QWidget] = [
            advanced_form,
            _hint(
                "The runtime solves YouTube's challenge for Premium streams. Player "
                "clients override yt-dlp's choice (comma-separated, e.g. web_music); "
                "leave empty unless playback breaks."
            ),
        ]
        if updater is not None:
            advanced_rows.append(self._update_row(updater))
        advanced = _page(*advanced_rows)

        self._tabs.addTab(general, "General")
        self._tabs.addTab(playback, "Playback")
        self._tabs.addTab(hotkeys, "Hotkeys")
        self._tabs.addTab(advanced, "Advanced")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setObjectName("Primary")
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(buttons)

    def _update_row(self, updater: YtDlpUpdater) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 8, 0, 0)
        version = QLabel()
        status = _hint("")
        check = QPushButton("Check for update")

        def refresh() -> None:
            version.setText(f"yt-dlp {updater.version()}")
            check.setEnabled(not updater.busy)

        def show_status(text: str) -> None:
            status.setText(text)
            refresh()

        updater.status.connect(show_status)
        check.clicked.connect(updater.check)
        check.clicked.connect(refresh)
        self.finished.connect(lambda _code: updater.status.disconnect(show_status))
        refresh()
        line.addWidget(version)
        line.addWidget(check)
        line.addWidget(status, 1)
        return row

    def edited(self) -> Settings:
        settings = self._settings.copy()
        settings.close_to_tray = self.close_to_tray.isChecked()
        settings.radio_autoplay = self.autoplay.isChecked()
        settings.report_history = self.history.isChecked()
        settings.normalize = str(self.normalize.currentData())
        settings.audio_device = str(self.device.currentData())
        settings.hotkeys = {
            action: text
            for action, edit in self._keys.items()
            if (text := edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText))
        }
        settings.js_runtime = str(self.runtime.currentData())
        settings.player_clients = [c.strip() for c in self.clients.text().split(",") if c.strip()]
        return settings

    def _ok(self) -> None:
        problems = self._apply(self.edited())
        for action, label in self._problems.items():
            label.setText(problems.get(action, ""))
            label.setVisible(action in problems)
        if problems:
            self._tabs.setCurrentIndex(2)
            return
        self.accept()
