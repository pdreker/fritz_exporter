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
import json
import logging
import re
import time
from collections.abc import Callable
from http import HTTPStatus
from typing import Any, NamedTuple

import requests
from defusedxml import DefusedXmlException, ElementTree

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

# Largest reply read from the web interface. The biggest real pages (DOCSIS
# channels, a full smart home device list) stay well below 1 MiB; the cap keeps a
# hostile or broken reply from filling the memory.
MAX_RESPONSE_BYTES: int = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024

# Statuses the device answers an invalid or expired SID with (AHA, REST API).
_SESSION_REJECTED_STATUSES = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)

# data.lua (and the REST API) return HTML (the login page) instead of JSON
# when the SID is invalid or the session has expired.
_HTML_RESPONSE_PREFIXES = ("<", "\ufeff<")
_HTML_RESPONSE_HINT = "(SID invalid or no permission)"
# Leading bytes looked at to tell HTML from JSON.
_HTML_SNIFF_BYTES = 256


class FritzWebUiError(Exception):
    """Raised when data cannot be retrieved from the Fritz!Box web interface."""


class FritzWebUiLoginError(FritzWebUiError):
    """The device refused the login: wrong credentials, or blocked after failures.

    ``block_time`` is how long the device says it blocks logins, in seconds.
    """

    def __init__(self, msg: str, block_time: int = 0) -> None:
        super().__init__(msg)
        self.block_time = block_time


class FritzWebUiMd5DisabledError(FritzWebUiError):
    """The device offers only the legacy MD5 login, and MD5 is not allowed for it."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"{name} offers only the MD5 web login, not allowed by allow_unsafe_md5_password, "
            "web interface metrics disabled until restart"
        )


class _SessionRejectedError(FritzWebUiError):
    """The device did not accept the SID; logging in again may help."""


class _Reply(NamedTuple):
    body: bytes
    content_type: str


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
    """Describe a failed request by class names only, worded like TR-064's
    ``transport_failure_text``.

    ``requests`` puts the full request URL into its exception messages, and
    that URL carries the SID (AHA) or the user name (login).
    """
    return f"Fritz device did not answer ({': '.join(_cause_names(exc))})"


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
        # Pages the device refused right after a fresh login: no permission, not
        # an expired session, so a new login would not help. See _with_session().
        self._forbidden: set[str] = set()
        # The last refused login, and when the next attempt may be made.
        self._login_refused: str | None = None
        self._login_retry_at: float = 0.0
        # Set when the device offers only the MD5 login and MD5 is not allowed:
        # the web interface stays unused until the exporter is restarted.
        self._md5_refused: FritzWebUiMd5DisabledError | None = None
        # Why a request of the current scrape could not reach the device; the
        # following requests of the scrape fail at once instead of each waiting
        # for its own timeout. See start_scrape().
        self._unreachable: str | None = None

    def start_scrape(self) -> None:
        """Try the device again: called once per scrape, before the first request."""
        self._unreachable = None

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, failed: str, **kwargs: Any) -> _Reply:  # noqa: ANN401
        """Send one request and read the reply, at most ``MAX_RESPONSE_BYTES`` of it.

        ``failed`` starts the error message, e.g. ``"data.lua request failed"``. A
        device that does not answer fails the remaining requests of this scrape at
        once.
        """
        if self._unreachable is not None:
            msg = f"{failed}: not sent, the device did not answer earlier ({self._unreachable})"
            raise FritzWebUiError(msg)
        send = self.session.post if method == "POST" else self.session.get
        try:
            resp = send(f"{self.base_url}{path}", timeout=self.timeout, stream=True, **kwargs)
            try:
                if resp.status_code in _SESSION_REJECTED_STATUSES:
                    msg = f"{failed}: {_http_error_text(resp)}"
                    raise _SessionRejectedError(msg)
                if not resp.ok:
                    msg = f"{failed}: {_http_error_text(resp)}"
                    raise FritzWebUiError(msg)
                body = self._read_body(resp, failed)
            finally:
                resp.close()
        except requests.RequestException as e:
            self._unreachable = _failure_text(e)
            msg = f"{failed}: {self._unreachable}"
            raise FritzWebUiError(msg) from None
        content_type = str(resp.headers.get("Content-Type", "")) if resp.headers else ""
        return _Reply(body, content_type)

    @staticmethod
    def _read_body(resp: requests.Response, failed: str) -> bytes:
        too_large = f"{failed}: reply larger than {MAX_RESPONSE_BYTES} bytes"
        length = resp.headers.get("Content-Length", "") if resp.headers else ""
        if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
            raise FritzWebUiError(too_large)
        body = bytearray()
        for chunk in resp.iter_content(_READ_CHUNK_BYTES):
            body += chunk
            if len(body) > MAX_RESPONSE_BYTES:
                raise FritzWebUiError(too_large)
        return bytes(body)

    @staticmethod
    def _xml(reply: _Reply, what: str) -> Any:  # noqa: ANN401
        try:
            return ElementTree.fromstring(reply.body)
        except (ElementTree.ParseError, DefusedXmlException) as e:
            msg = f"could not parse {what}: {type(e).__name__}"
            raise FritzWebUiError(msg) from None

    @staticmethod
    def _json(reply: _Reply, what: str) -> object:
        """Parse a JSON reply, of any shape; the HTML login page means the SID was not accepted."""
        start = reply.body[:_HTML_SNIFF_BYTES].decode("utf-8", "replace").lstrip()
        is_html = "text/html" in reply.content_type or start.startswith(_HTML_RESPONSE_PREFIXES)
        if not reply.body or is_html:
            msg = f"Fritz!Box returned HTML instead of JSON for {what} {_HTML_RESPONSE_HINT}"
            raise _SessionRejectedError(msg)
        try:
            return json.loads(reply.body)
        except json.JSONDecodeError as e:
            msg = f"could not parse JSON from {what}: {e.msg} at position {e.pos}"
            raise FritzWebUiError(msg) from None
        except (ValueError, RecursionError) as e:
            # UnicodeDecodeError would quote a byte of the reply; deep nesting
            # exhausts the parser's recursion, which no caller expects.
            msg = f"could not parse JSON from {what}: {type(e).__name__}"
            raise FritzWebUiError(msg) from None

    def _with_session[T](self, page: str, fetch: Callable[[str], T]) -> T:
        """Run ``fetch`` with a SID, logging in again once if the device rejects it.

        The device answers "no permission" the same way as "session expired". A
        page it refuses right after a fresh login is taken as no permission and
        gets no more logins until it works again: one login per page and scrape
        would fill the device's event log.
        """
        cached = self._sid is not None
        sid = self._ensure_sid()
        try:
            result = fetch(sid)
        except _SessionRejectedError as e:
            if not cached or page in self._forbidden:
                self._forbidden.add(page)
                raise
            logger.debug("%s rejected the web session (%s), logging in again", self.name, e)
            self._invalidate_sid()
            try:
                result = fetch(self._ensure_sid())
            except _SessionRejectedError:
                self._forbidden.add(page)
                raise
        self._forbidden.discard(page)
        return result

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
        reply = self._request(
            "GET", "/login_sid.lua", "login_sid.lua request failed", params=params
        )
        root = self._xml(reply, "login_sid.lua response")

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
        except FritzWebUiMd5DisabledError:
            raise
        except FritzWebUiError:
            # Users can report this so the bounds can follow real firmware.
            logger.debug(
                "Rejected login challenge from %s: %r",
                self.name,
                challenge[:_CHALLENGE_LOG_LIMIT],
            )
            raise

        reply = self._request(
            "POST",
            "/login_sid.lua",
            "login POST failed",
            params=_LOGIN_VERSION,
            data={"username": self.username, "response": response},
        )
        sid_elem = self._xml(reply, "login response").find("SID")
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
        makes the device block logins longer, for the web interface too. A
        refused MD5 login is not tried again until the exporter is restarted.
        """
        if self._md5_refused is not None:
            raise self._md5_refused
        # The session lasts while it is used; the device says when it is not
        # valid any more (see _with_session).
        if self._sid:
            return self._sid
        if self._login_refused is not None and time.monotonic() < self._login_retry_at:
            msg = (
                f"login not retried for {self._login_retry_at - time.monotonic():.0f} seconds "
                f"after: {self._login_refused}"
            )
            raise FritzWebUiError(msg)
        try:
            self._sid = self._login()
        except FritzWebUiMd5DisabledError as e:
            self._md5_refused = e
            # No traceback: the message says all there is.
            logger.error(  # noqa: TRY400
                "%s. Upgrade Fritz!OS to 7.24 or later, or, to accept that anyone who "
                "can read the traffic to the device can recover the password, set "
                "allow_unsafe_md5_password (env: FRITZ_ALLOW_UNSAFE_MD5_PASSWORD) "
                "for this device.",
                e,
            )
            raise
        except FritzWebUiLoginError as e:
            self._login_refused = str(e)
            self._login_retry_at = time.monotonic() + max(LOGIN_RETRY_DELAY, e.block_time)
            raise
        self._login_refused = None
        return self._sid

    def _invalidate_sid(self) -> None:
        self._sid = None

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def fetch_page(self, page: str) -> object:
        """Fetch a ``data.lua`` page as JSON.

        The device answers an invalid or expired SID with its HTML login page.
        """

        def fetch(sid: str) -> object:
            payload = {"sid": sid, "page": page, "xhrId": "all", "xhr": "1"}
            reply = self._request("POST", "/data.lua", "data.lua request failed", data=payload)
            return self._json(reply, "data.lua")

        return self._with_session(f"data.lua {page}", fetch)

    def fetch_aha(self, command: str, **params: str) -> str:
        """Run an AHA command (e.g. ``getdevicelistinfos``) and return the reply text.

        The device answers an invalid or expired SID with HTTP 403.
        """
        failed = f"AHA request failed for {command}"

        def fetch(sid: str) -> str:
            payload = {**params, "sid": sid, "switchcmd": command}
            reply = self._request("GET", "/webservices/homeautoswitch.lua", failed, params=payload)
            if not reply.body:
                msg = f"Fritz!Box returned an empty AHA response for {command}"
                raise FritzWebUiError(msg)
            return reply.body.decode("utf-8", "replace")

        return self._with_session(f"AHA {command}", fetch)

    def fetch_api(self, path: str) -> object:
        """Fetch a Fritz!OS REST API path as JSON.

        ``path`` is the API path below the host, e.g. ``/api/v0/generic/box``. The
        REST API takes the SID in an ``Authorization: AVM-SID <sid>`` header only;
        as a query parameter it is rejected with HTTP 400.
        """

        def fetch(sid: str) -> object:
            headers = {"Authorization": f"AVM-SID {sid}"}
            reply = self._request(
                "GET", path, f"REST API request failed for {path}", headers=headers
            )
            return self._json(reply, path)

        return self._with_session(path, fetch)


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
