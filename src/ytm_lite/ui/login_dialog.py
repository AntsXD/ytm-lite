"""Sign-in dialog: import from Firefox, paste request headers, or load a cookies.txt.

Nothing is stored until YouTube has accepted the credentials (an account-info request).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ytm_lite.app.api import Api, ApiError, ApiErrorKind
from ytm_lite.app.auth import (
    REQUIRED_COOKIES,
    AuthError,
    Credentials,
    FirefoxProfile,
    cookies_from_header,
    credentials_from_cookies_txt,
    credentials_from_firefox,
    credentials_from_headers,
    firefox_profiles,
    parse_headers,
)
from ytm_lite.app.models import Account
from ytm_lite.app.workers import Task, Workers

_STEPS = """<ol style="margin-left: -20px">
<li>Open a <b>private window</b>, go to <b>music.youtube.com</b> and sign in.</li>
<li>Press <b>F12</b>, open the <b>Network</b> tab and type <code>browse</code> in the filter.</li>
<li>Click any <code>browse</code> request and copy its request headers:<br>
Firefox: Headers → Request Headers → <i>Raw</i>, select all and copy.<br>
Chrome / Edge: right-click the request → Copy → <i>Copy as cURL (bash)</i>.</li>
<li>Paste below and press Sign in. Then close the private window, so the browser
can't rotate (and invalidate) the cookies you copied.</li>
</ol>"""


class LoginDialog(QDialog):
    def __init__(self, workers: Workers, api: Api, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in to YouTube Music")
        self.setMinimumWidth(600)
        self._workers = workers
        self._api = api
        self._task: Task | None = None
        self.credentials: Credentials | None = None
        self.account: Account | None = None
        # Closing the dialog mid-check must not deliver results to a deleted dialog.
        self.finished.connect(self._cancel_task)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._firefox_tab(), "Firefox")
        self.tabs.addTab(self._headers_tab(), "Paste headers")
        self.tabs.addTab(self._cookies_tab(), "cookies.txt")

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        skip = QPushButton("Continue without signing in")
        skip.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(skip)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addLayout(bottom)

    # -- tabs ------------------------------------------------------------------------------

    def _firefox_tab(self) -> QWidget:
        tab = QWidget()
        self.profile_box = QComboBox()
        profiles = firefox_profiles()
        for profile in profiles:
            label = f"{profile.name} (default)" if profile.default else profile.name
            self.profile_box.addItem(label, profile)
        info = QLabel(
            "Reads only your youtube.com cookies from this Firefox profile, so make sure "
            "you're signed in to music.youtube.com there. If the session expires later, "
            "the app quietly re-reads them from Firefox."
        )
        info.setWordWrap(True)
        self.firefox_button = QPushButton("Import from Firefox")
        self.firefox_button.clicked.connect(self._import_firefox)
        if not profiles:
            info.setText("No Firefox profile was found. Use “Paste headers” instead.")
            self.profile_box.setEnabled(False)
            self.firefox_button.setEnabled(False)
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("Firefox profile"))
        layout.addWidget(self.profile_box)
        layout.addWidget(info)
        layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.firefox_button)
        layout.addLayout(row)
        return tab

    def _headers_tab(self) -> QWidget:
        tab = QWidget()
        steps = QLabel(_STEPS)
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.TextFormat.RichText)
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("Paste the request headers or the cURL command here")
        self.paste.setMinimumHeight(120)
        self.paste_status = QLabel("")
        self.paste_status.setObjectName("Dim")
        self.headers_button = QPushButton("Sign in")
        self.headers_button.setEnabled(False)
        self.headers_button.clicked.connect(self._sign_in_with_headers)
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(200)
        self._check_timer.timeout.connect(self._check_paste)
        self.paste.textChanged.connect(self._check_timer.start)
        layout = QVBoxLayout(tab)
        layout.addWidget(steps)
        layout.addWidget(self.paste, 1)
        row = QHBoxLayout()
        row.addWidget(self.paste_status, 1)
        row.addWidget(self.headers_button)
        layout.addLayout(row)
        return tab

    def _cookies_tab(self) -> QWidget:
        tab = QWidget()
        info = QLabel(
            "Advanced: load a Netscape-format cookies.txt exported from a signed-in "
            "YouTube session. Only youtube.com cookies are used."
        )
        info.setWordWrap(True)
        self.cookies_button = QPushButton("Choose cookies.txt…")
        self.cookies_button.clicked.connect(self._choose_cookies)
        layout = QVBoxLayout(tab)
        layout.addWidget(info)
        layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.cookies_button)
        layout.addLayout(row)
        return tab

    # -- actions -----------------------------------------------------------------------------

    def _check_paste(self) -> None:
        text = self.paste.toPlainText()
        if not text.strip():
            self.paste_status.setText("")
            self.headers_button.setEnabled(False)
            return
        headers = parse_headers(text)
        names = {c.name for c in cookies_from_header(headers.get("cookie", ""))}
        missing = [name for name in REQUIRED_COOKIES if name not in names]
        if "cookie" not in headers:
            self.paste_status.setText("No Cookie header found yet")
        elif missing:
            self.paste_status.setText(f"Cookie found, but missing {', '.join(missing)}")
        else:
            self.paste_status.setText(f"Found {len(names)} cookies ✓")
        self.headers_button.setEnabled("cookie" in headers and not missing)

    def _import_firefox(self) -> None:
        profile: FirefoxProfile = self.profile_box.currentData()
        self._set_busy("Reading cookies from Firefox…")
        self._task = self._workers.submit(
            lambda: credentials_from_firefox(profile),
            on_done=self._validate,
            on_error=self._failed,
            pool="io",
        )

    def _sign_in_with_headers(self) -> None:
        try:
            credentials = credentials_from_headers(self.paste.toPlainText())
        except AuthError as exc:
            self._failed(exc)
            return
        self._validate(credentials)

    def _choose_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose cookies.txt", "", "Cookie files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                credentials = credentials_from_cookies_txt(fh.read())
        except (OSError, AuthError) as exc:
            self._failed(exc)
            return
        self._validate(credentials)

    def _validate(self, credentials: Credentials) -> None:
        self._set_busy("Checking your session with YouTube…")
        self._task = self._workers.submit(
            lambda: self._api.account_info_for(credentials),
            on_done=lambda account: self._finish(credentials, account),
            on_error=self._failed,
        )

    def _finish(self, credentials: Credentials, account: Account) -> None:
        self.credentials = credentials
        self.account = account
        self.accept()

    def _failed(self, exc: Exception) -> None:
        if isinstance(exc, ApiError):
            if exc.kind in (ApiErrorKind.AUTH, ApiErrorKind.PARSE):
                message = (
                    "YouTube didn't accept this session. Make sure you're signed in to "
                    "music.youtube.com, then try again."
                )
            elif exc.kind is ApiErrorKind.NETWORK:
                message = f"Couldn't reach YouTube: {exc}"
            else:
                message = f"YouTube returned an error: {exc}"
        else:
            message = str(exc)
        self._set_busy(None)
        self.status.setText(f'<span style="color:#ff6b6b">{message}</span>')

    def _cancel_task(self, _result: int) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _set_busy(self, message: str | None) -> None:
        busy = message is not None
        self.tabs.setEnabled(not busy)
        if busy:
            self.status.setText(message or "")
