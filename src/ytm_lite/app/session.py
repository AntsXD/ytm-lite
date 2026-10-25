"""The signed-in session: stored credentials, handed to ytmusicapi and the resolver.

A session signed in from Firefox heals itself: when YouTube rejects it (cookies
rotated, IP changed), the cookies are re-read from Firefox once before the user is
asked to sign in again.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ytm_lite.app.api import Api
from ytm_lite.app.auth import Credentials, FirefoxProfile, credentials_from_firefox
from ytm_lite.app.config import Settings
from ytm_lite.app.models import Account
from ytm_lite.app.resolver import ResolverClient
from ytm_lite.app.secrets import SecretStore
from ytm_lite.app.workers import Workers

log = logging.getLogger(__name__)


class Session(QObject):
    changed = Signal()  # signed in or signed out
    refreshed = Signal()  # same account, fresh cookies (re-imported from Firefox)
    expired = Signal()  # YouTube rejects the session and it couldn't be refreshed silently

    REIMPORT_COOLDOWN = 60.0

    def __init__(
        self,
        store: SecretStore,
        api: Api,
        resolver: ResolverClient,
        workers: Workers,
        settings: Settings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._api = api
        self._resolver = resolver
        self._workers = workers
        self._settings = settings
        self._credentials: Credentials | None = None
        self._last_reimport = -self.REIMPORT_COOLDOWN
        self._recovering = False

    @property
    def credentials(self) -> Credentials | None:
        return self._credentials

    @property
    def signed_in(self) -> bool:
        return self._credentials is not None

    @property
    def account_name(self) -> str | None:
        return self._credentials.account_name if self._credentials else None

    def load(self) -> None:
        """Read stored credentials (no network)."""
        data = self._store.load()
        if data is not None:
            try:
                self._credentials = Credentials.from_json(data.decode("utf-8"))
            except (ValueError, TypeError, KeyError):
                log.warning("stored credentials are unreadable; signing out")
                self._store.clear()
        self._apply()

    def sign_in(self, credentials: Credentials, account: Account) -> None:
        self._save(dataclasses.replace(credentials, account_name=account.name))
        log.info("signed in (%s)", credentials.method)
        self.changed.emit()

    def sign_out(self) -> None:
        self._credentials = None
        self._store.clear()
        self._apply()
        log.info("signed out")
        self.changed.emit()

    def set_account(self, account: Account) -> None:
        creds = self._credentials
        if creds is not None and creds.account_name != account.name:
            self._save(dataclasses.replace(creds, account_name=account.name))

    def apply_settings(self) -> None:
        self._apply()

    def refresh_from_firefox(self, done: Callable[[bool], None] | None = None) -> None:
        """Re-read the cookies from Firefox. `done(True)` if they changed (and were applied)."""
        creds = self._credentials
        if creds is None or creds.method != "firefox" or not creds.firefox_profile:
            if done is not None:
                done(False)
            return
        profile = FirefoxProfile("", Path(creds.firefox_profile), True)

        def on_done(new: Credentials) -> None:
            if self._credentials is not creds:  # signed out or replaced meanwhile
                return
            fresh = dataclasses.replace(
                new, account_name=creds.account_name, visitor_id=creds.visitor_id
            )
            refreshed = fresh.cookie_header() != creds.cookie_header()
            if refreshed:
                self._save(fresh)
                log.info("re-imported fresh cookies from Firefox")
                self.refreshed.emit()
            if done is not None:
                done(refreshed)

        def on_error(exc: Exception) -> None:
            log.warning("re-import from Firefox failed: %s", exc)
            if done is not None:
                done(False)

        self._workers.submit(
            lambda: credentials_from_firefox(profile), on_done=on_done, on_error=on_error, pool="io"
        )

    def handle_auth_failure(self) -> None:
        """YouTube rejected the session: try Firefox once, otherwise tell the user."""
        if self._credentials is None or self._recovering:
            return
        now = time.monotonic()
        if now - self._last_reimport < self.REIMPORT_COOLDOWN:
            self.expired.emit()
            return
        self._last_reimport = now
        self._recovering = True

        def after(refreshed: bool) -> None:
            self._recovering = False
            if not refreshed:
                self.expired.emit()

        self.refresh_from_firefox(after)

    def _save(self, credentials: Credentials) -> None:
        self._credentials = credentials
        self._store.save(credentials.to_json().encode("utf-8"))
        self._apply()

    def _apply(self) -> None:
        creds = self._credentials
        self._api.set_credentials(creds)
        self._resolver.configure(
            cookies=creds.netscape() if creds else None,
            js_runtime=self._settings.js_runtime,
            player_clients=self._settings.player_clients,
        )
