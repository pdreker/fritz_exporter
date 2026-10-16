"""Generic client for the Fritz!Box web interface (login + data.lua + REST API).

A number of statistics cannot be pulled over the TR-064 API, and the Fritz!Box
web interface exposes them instead: the internal ``data.lua`` pages the web UI
itself uses, and the ``/api/v0/...`` REST endpoints. Both require a normal web
login (challenge-response via ``login_sid.lua``) rather than the TR-064 session
used elsewhere in the exporter.

This module implements that client; it is technology-agnostic (not specific to
cable/DOCSIS boxes). Technology-specific pages live in their own modules, e.g.
:mod:`fritzexporter.fritz_docsis`.

The endpoints and JSON structures were reverse-engineered from the Fritz!Box
web UI and cross-checked against the ``fritzbox-cable-api`` project and the
neobiker.de / dc8wan.de shell scripts.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import re
import time
from http import HTTPStatus
from typing import Any

import requests
from defusedxml import ElementTree

logger = logging.getLogger("fritzexporter.fritz_webui")

__all__ = [
    "FritzWebUiClient",
    "FritzWebUiError",
    "FritzWebUiLoginError",
    "FritzWebUiMd5DisabledError",
]

# Seconds to wait after a refused login before the next attempt (longer if the
# device says it blocks logins for longer).
LOGIN_RETRY_DELAY: float = 60.0

# Fritz!OS 7.24 and later only offer the PBKDF2 challenge when asked for it with
# version=2; without it they keep sending the MD5 challenge, for older clients.
_LOGIN_VERSION = {"version": "2"}

# The login_sid.lua challenge-response scheme changed in Fritz!OS 7.24.
# Newer firmware uses PBKDF2-SHA256 ("2$iter1$salt1$iter2$salt2"), older
# firmware uses a simple MD5 over the UTF-16LE encoded challenge-password.
_PBKDF2_CHALLENGE_RE = re.compile(
    r"2\$([0-9]{1,7})\$((?:[0-9a-fA-F]{2}){1,64})\$([0-9]{1,7})\$((?:[0-9a-fA-F]{2}){1,64})"
)
_LEGACY_CHALLENGE_RE = re.compile(r"[0-9A-Za-z]{1,32}")
_BLOCK_TIME_RE = re.compile(r"[0-9]{1,6}")
# The challenge is attacker-controlled on a hostile network: the floor keeps the
# response from being a cheap hash of the password, the ceiling keeps the
# PBKDF2 run (which holds the collect lock) short. Both apply to the sum of the
# two counts, which is what a response costs. Firmware sends 60000 + 6000; the
# floor still admits AVM's documented example of 10000 + 2000.
_PBKDF2_MIN_TOTAL_ITERATIONS = 10_000
_PBKDF2_MAX_TOTAL_ITERATIONS = 1_000_000
# Longest challenge echoed to the debug log; the reply may be hostile.
_CHALLENGE_LOG_LIMIT = 200

# data.lua (and the REST API) return HTML (the login page) instead of JSON
# when the SID is invalid or the session has expired.
_HTML_RESPONSE_PREFIXES = ("<", "\ufeff<")
_HTML_RESPONSE_HINT = "(SID invalid or no permission)"


class FritzWebUiError(Exception):
    """Raised when data cannot be retrieved from the Fritz!Box web interface."""


class FritzWebUiLoginError(FritzWebUiError):
    """The device refused the login: wrong credentials, or blocked after failures.

    ``block_time`` is how long the device says it blocks logins, in seconds.
    """

    def __init__(self, msg: str, block_time: int = 0) -> None:
        super().__init__(msg)
        self.block_time = block_time


class FritzWebUiMd5DisabledError(Exception):
    """Raised when a device offers only the legacy MD5 login and MD5 is not allowed.

    Not a ``FritzWebUiError`` on purpose: capabilities log and skip those, but this
    one must stop the exporter.
    """

    def __init__(self, host: str) -> None:
        super().__init__(
            f"{host} offers only the legacy MD5 web login (Fritz!OS before 7.24). "
            "Anyone who can read the traffic to the device can recover the password "
            "from an MD5 login, so the exporter refuses it by default. Upgrade Fritz!OS "
            "or, to accept the risk, enable allow_unsafe_md5_password "
            "(env: FRITZ_ALLOW_UNSAFE_MD5_PASSWORD) for this device."
        )


_MAX_CAUSE_DEPTH = 5


def _http_error_text(resp: requests.Response) -> str:
    """Describe an error status by its code and standard phrase.

    ``resp.reason`` is not used: the server or a proxy sets it and can put any
    text there, the request URL included.
    """
    try:
        reason = f" {HTTPStatus(resp.status_code).phrase}"
    except ValueError:
        reason = ""
    return f"HTTP {resp.status_code}{reason}"


def _cause_names(exc: BaseException) -> list[str]:
    """Class names (and errno name) along the cause chain of ``exc``, never any message text."""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(names) < _MAX_CAUSE_DEPTH:
        seen.add(id(current))
        name = type(current).__name__
        if type(current) is OSError and current.errno is not None:
            name = errno.errorcode.get(current.errno, name)
        # requests and urllib3 wrap the underlying error in ``reason`` or in ``args``
        wrapped = [getattr(current, "reason", None), *current.args]
        nested = next((a for a in wrapped if isinstance(a, BaseException)), None)
        if name != "MaxRetryError":
            names.append(name)
        current = nested or current.__cause__ or current.__context__
    return names


def _failure_text(exc: requests.RequestException) -> str:
    """Describe a failed request by class names only.

    ``requests`` puts the full request URL into its exception messages, and
    that URL carries the SID (AHA) or the user name (login).
    """
    return f"request failed ({': '.join(_cause_names(exc))})"


class FritzWebUiClient:
    """Fetches data from a Fritz!Box via its web interface (web login)."""

    def __init__(  # noqa: PLR0913
        self,
        host: str,
        username: str,
        password: str,
        *,
        use_tls: bool = False,
        port: int | None = None,
        timeout: float | None = None,
        allow_unsafe_md5_password: bool = False,
        name: str | None = None,
    ) -> None:
        self.host = host
        # How log messages and errors name the device; see device_log_name().
        self.name = name or host
        self.username = username
        self.password = password
        self.timeout = timeout
        self.allow_unsafe_md5_password = allow_unsafe_md5_password

        scheme = "https" if use_tls else "http"
        host_port = f"{host}:{port}" if port else host
        self.base_url = f"{scheme}://{host_port}"
        self.session = requests.Session()
        # Fritz!Box devices serve a self-signed certificate; match the TR-064
        # session in fritzconnection, which does not verify it either.
        self.session.verify = False
        self._sid: str | None = None
        self._sid_expiry: float = 0.0
        # The last refused login, and when the next attempt may be made.
        self._login_refused: str | None = None
        self._login_retry_at: float = 0.0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @staticmethod
    def _md5_response(challenge: str, password: str) -> str:
        """Compute the legacy MD5 challenge-response (Fritz!OS < 7.24)."""
        if not _LEGACY_CHALLENGE_RE.fullmatch(challenge):
            msg = "unexpected legacy login challenge format"
            raise FritzWebUiError(msg)
        combined = (challenge + "-" + password).encode("utf-16-le")
        # MD5 is mandated by the Fritz!Box login protocol for older firmware;
        # it is not used for security here.
        return f"{challenge}-{hashlib.md5(combined).hexdigest()}"  # noqa: S324

    @staticmethod
    def _pbkdf2_response(challenge: str, password: str) -> str:
        """Compute the PBKDF2-SHA256 challenge-response (Fritz!OS >= 7.24)."""
        match = _PBKDF2_CHALLENGE_RE.fullmatch(challenge)
        if not match:
            msg = "unexpected PBKDF2 login challenge format"
            raise FritzWebUiError(msg)
        iter1, salt1_hex, iter2, salt2_hex = match.groups()
        iter1, iter2 = int(iter1), int(iter2)
        total = iter1 + iter2
        if not (
            min(iter1, iter2) >= 1
            and _PBKDF2_MIN_TOTAL_ITERATIONS <= total <= _PBKDF2_MAX_TOTAL_ITERATIONS
        ):
            msg = "PBKDF2 login challenge iteration count out of range"
            raise FritzWebUiError(msg)
        salt1 = bytes.fromhex(salt1_hex)
        salt2 = bytes.fromhex(salt2_hex)

        hash1 = hashlib.pbkdf2_hmac("sha256", password.encode(), salt1, iter1, 32)
        hash2 = hashlib.pbkdf2_hmac("sha256", hash1, salt2, iter2, 32)
        # AVM expects "<salt2>$<hash2>", not the whole challenge.
        return f"{salt2_hex}${hash2.hex()}"

    def _challenge_response(self, challenge: str) -> str:
        """Pick the login scheme from the challenge and compute the response."""
        if challenge.startswith("2$"):
            return self._pbkdf2_response(challenge, self.password)
        if "$" in challenge:
            msg = "unsupported login challenge version"
            raise FritzWebUiError(msg)
        # A forged reply can push any box onto MD5, so refuse it unless allowed.
        if _LEGACY_CHALLENGE_RE.fullmatch(challenge):
            if not self.allow_unsafe_md5_password:
                raise FritzWebUiMd5DisabledError(self.name)
            logger.warning("Using the unsafe legacy MD5 web login for %s", self.name)
        return self._md5_response(challenge, self.password)

    def _get_session_info(self) -> dict[str, str]:
        """Fetch the login challenge / SID from login_sid.lua."""
        params = {**_LOGIN_VERSION, "username": self.username} if self.username else _LOGIN_VERSION
        try:
            resp = self.session.get(
                f"{self.base_url}/login_sid.lua", params=params, timeout=self.timeout
            )
        except requests.RequestException as e:
            msg = f"login_sid.lua request failed: {_failure_text(e)}"
            raise FritzWebUiError(msg) from None

        if not resp.ok:
            msg = f"login_sid.lua request failed: {_http_error_text(resp)}"
            raise FritzWebUiError(msg)

        try:
            root = ElementTree.fromstring(resp.text)
        except ElementTree.ParseError as e:
            msg = f"could not parse login_sid.lua response: {e}"
            raise FritzWebUiError(msg) from e

        def _find(tag: str) -> str:
            elem = root.find(tag)
            return elem.text if elem is not None and elem.text else ""

        return {
            "sid": _find("SID"),
            "challenge": _find("Challenge"),
            "block_time": _find("BlockTime"),
        }

    @staticmethod
    def _check_block_time(block_time: str) -> None:
        """Raise if the box blocks logins after too many failed attempts."""
        if not block_time:
            return
        if not _BLOCK_TIME_RE.fullmatch(block_time):
            msg = "login blocked (too many failed attempts)"
            raise FritzWebUiLoginError(msg)
        if int(block_time) > 0:
            msg = f"login blocked for {int(block_time)} seconds (too many failed attempts)"
            raise FritzWebUiLoginError(msg, int(block_time))

    def _login(self) -> str:
        """Perform the challenge-response login and return a valid SID."""
        info = self._get_session_info()

        # Already have a valid session (e.g. running without auth).
        if info["sid"] and info["sid"] != "0000000000000000":
            return info["sid"]

        self._check_block_time(info["block_time"])

        challenge = info["challenge"]
        if not challenge:
            msg = "no login challenge in login_sid.lua response"
            raise FritzWebUiError(msg)
        try:
            response = self._challenge_response(challenge)
        except FritzWebUiError:
            # Users can report this so the bounds can follow real firmware.
            logger.debug(
                "Rejected login challenge from %s: %r",
                self.name,
                challenge[:_CHALLENGE_LOG_LIMIT],
            )
            raise

        try:
            resp = self.session.post(
                f"{self.base_url}/login_sid.lua",
                params=_LOGIN_VERSION,
                data={"username": self.username, "response": response},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            msg = f"login POST failed: {_failure_text(e)}"
            raise FritzWebUiError(msg) from None

        if not resp.ok:
            msg = f"login POST failed: {_http_error_text(resp)}"
            raise FritzWebUiError(msg)

        try:
            root = ElementTree.fromstring(resp.text)
        except ElementTree.ParseError as e:
            msg = f"could not parse login response: {e}"
            raise FritzWebUiError(msg) from e

        sid_elem = root.find("SID")
        sid = sid_elem.text if sid_elem is not None else ""
        if not sid or sid == "0000000000000000":
            msg = "login failed: wrong password or username"
            raise FritzWebUiLoginError(msg)

        logger.debug("Web login to %s successful, SID obtained", self.name)
        return sid

    def _ensure_sid(self) -> str:
        """Return a cached valid SID, logging in if necessary.

        After a refused login, no new attempt is made for ``LOGIN_RETRY_DELAY``
        seconds, or as long as the device blocks logins: every failed attempt
        makes the device block logins longer, for the web interface too.
        """
        if self._sid and time.monotonic() < self._sid_expiry:
            return self._sid
        if self._login_refused is not None and time.monotonic() < self._login_retry_at:
            msg = (
                f"login not retried for {self._login_retry_at - time.monotonic():.0f} seconds "
                f"after: {self._login_refused}"
            )
            raise FritzWebUiError(msg)
        try:
            self._sid = self._login()
        except FritzWebUiLoginError as e:
            self._login_refused = str(e)
            self._login_retry_at = time.monotonic() + max(LOGIN_RETRY_DELAY, e.block_time)
            raise
        self._login_refused = None
        # SIDs are valid for ~18 minutes on the box; refresh well before that.
        self._sid_expiry = time.monotonic() + 15 * 60
        return self._sid

    def _invalidate_sid(self) -> None:
        self._sid = None
        self._sid_expiry = 0.0

    # ------------------------------------------------------------------
    # data.lua page fetching
    # ------------------------------------------------------------------

    def _fetch_page(self, sid: str, page: str) -> dict[str, Any]:
        """POST to data.lua and return the parsed JSON document."""
        payload = {"sid": sid, "page": page, "xhrId": "all", "xhr": "1"}
        try:
            resp = self.session.post(
                f"{self.base_url}/data.lua", data=payload, timeout=self.timeout
            )
        except requests.RequestException as e:
            msg = f"data.lua request failed: {_failure_text(e)}"
            raise FritzWebUiError(msg) from None

        if not resp.ok:
            msg = f"data.lua request failed: {_http_error_text(resp)}"
            raise FritzWebUiError(msg)

        text = resp.text
        content_type = str(resp.headers.get("Content-Type", "")) if resp.headers else ""
        is_html = "text/html" in content_type or text.lstrip().startswith(_HTML_RESPONSE_PREFIXES)
        if not text or is_html:
            msg = f"Fritz!Box returned HTML instead of JSON {_HTML_RESPONSE_HINT}"
            raise FritzWebUiError(msg)

        try:
            return resp.json()
        except ValueError as e:
            msg = f"could not parse data.lua JSON response: {e}"
            raise FritzWebUiError(msg) from e

    def fetch_page(self, page: str) -> dict[str, Any]:
        """Fetch a ``data.lua`` page, re-authenticating once if the session expired.

        Each HTTP request is bounded by the client ``timeout``, so the retry
        path (re-login + re-fetch) is bounded to roughly twice that.
        """
        sid = self._ensure_sid()
        try:
            return self._fetch_page(sid, page)
        except FritzWebUiError:
            # Session may have expired server-side; try re-logging in once.
            logger.debug("data.lua fetch from %s failed, re-authenticating...", self.name)
            self._invalidate_sid()
            sid = self._ensure_sid()
            return self._fetch_page(sid, page)

    # ------------------------------------------------------------------
    # AHA HTTP interface (homeautoswitch.lua) fetching
    # ------------------------------------------------------------------

    def _fetch_aha(self, sid: str, command: str, params: dict[str, str]) -> str:
        """GET an AHA command from homeautoswitch.lua and return the response text."""
        payload = {**params, "sid": sid, "switchcmd": command}
        try:
            resp = self.session.get(
                f"{self.base_url}/webservices/homeautoswitch.lua",
                params=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            msg = f"AHA request failed for {command}: {_failure_text(e)}"
            raise FritzWebUiError(msg) from None

        if not resp.ok:
            msg = f"AHA request failed for {command}: {_http_error_text(resp)}"
            raise FritzWebUiError(msg)

        if not resp.text:
            msg = f"Fritz!Box returned an empty AHA response for {command}"
            raise FritzWebUiError(msg)
        return resp.text

    def fetch_aha(self, command: str, **params: str) -> str:
        """Run an AHA command (e.g. ``getdevicelistinfos``), re-authenticating once if needed.

        The box answers an invalid or expired SID with HTTP 403.
        """
        sid = self._ensure_sid()
        try:
            return self._fetch_aha(sid, command, params)
        except FritzWebUiError:
            logger.debug("AHA fetch from %s failed, re-authenticating...", self.name)
            self._invalidate_sid()
            sid = self._ensure_sid()
            return self._fetch_aha(sid, command, params)

    # ------------------------------------------------------------------
    # REST API (/api/v0/...) fetching
    # ------------------------------------------------------------------

    def _fetch_api(self, sid: str, path: str) -> dict[str, Any]:
        """GET a Fritz!OS REST API path and return the parsed JSON document.

        ``path`` is the API path (e.g. ``/api/v0/generic/box``). The REST API
        authenticates via an ``Authorization: AVM-SID <sid>`` header; passing
        the SID as a query parameter is rejected with HTTP 400.
        """
        headers = {"Authorization": f"AVM-SID {sid}"}
        try:
            resp = self.session.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            msg = f"REST API request failed for {path}: {_failure_text(e)}"
            raise FritzWebUiError(msg) from None

        if not resp.ok:
            msg = f"REST API request failed for {path}: {_http_error_text(resp)}"
            raise FritzWebUiError(msg)

        text = resp.text
        content_type = str(resp.headers.get("Content-Type", "")) if resp.headers else ""
        is_html = "text/html" in content_type or text.lstrip().startswith(_HTML_RESPONSE_PREFIXES)
        if not text or is_html:
            msg = f"Fritz!Box returned HTML instead of JSON for {path} {_HTML_RESPONSE_HINT}"
            raise FritzWebUiError(msg)

        try:
            return resp.json()
        except ValueError as e:
            msg = f"could not parse REST API JSON response for {path}: {e}"
            raise FritzWebUiError(msg) from e

    def fetch_api(self, path: str) -> dict[str, Any]:
        """Fetch a Fritz!OS REST API path, re-authenticating once if needed.

        ``path`` is the API path below the host, e.g. ``/api/v0/generic/box``.
        """
        sid = self._ensure_sid()
        try:
            return self._fetch_api(sid, path)
        except FritzWebUiError:
            logger.debug("REST API fetch from %s failed, re-authenticating...", self.name)
            self._invalidate_sid()
            sid = self._ensure_sid()
            return self._fetch_api(sid, path)


# Copyright 2019-2026 Patrick Dreker <patrick@dreker.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
