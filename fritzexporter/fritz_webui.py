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

import hashlib
import logging
import re
import time
from defusedxml import ElementTree as ET
from typing import Any

import requests

logger = logging.getLogger("fritzexporter.fritz_webui")

__all__ = [
    "FritzWebUiError",
    "FritzWebUiClient",
]

# The login_sid.lua challenge-response scheme changed in Fritz!OS 7.24.
# Newer firmware uses PBKDF2-SHA256 ("2$iter1$salt1$iter2$salt2"), older
# firmware uses a simple MD5 over the UTF-16LE encoded challenge-password.
_PBKDF2_CHALLENGE_RE = re.compile(r"^2\$(\d+)\$([0-9a-f]+)\$(\d+)\$([0-9a-f]+)$")

# data.lua (and the REST API) return HTML (the login page) instead of JSON
# when the SID is invalid or the session has expired.
_HTML_RESPONSE_PREFIXES = ("<", "\ufeff<")


class FritzWebUiError(Exception):
    """Raised when data cannot be retrieved from the Fritz!Box web interface."""


class FritzWebUiClient:
    """Fetches data from a Fritz!Box via its web interface (web login)."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        use_tls: bool = False,
        port: int | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout

        scheme = "https" if use_tls else "http"
        host_port = f"{host}:{port}" if port else host
        self.base_url = f"{scheme}://{host_port}"
        self.session = requests.Session()
        self.session.timeout = timeout
        self._sid: str | None = None
        self._sid_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @staticmethod
    def _md5_response(challenge: str, password: str) -> str:
        """Compute the legacy MD5 challenge-response (Fritz!OS < 7.24)."""
        combined = (challenge + "-" + password).encode("utf-16-le")
        return f"{challenge}-{hashlib.md5(combined).hexdigest()}"

    @staticmethod
    def _pbkdf2_response(challenge: str, password: str) -> str:
        """Compute the PBKDF2-SHA256 challenge-response (Fritz!OS >= 7.24)."""
        match = _PBKDF2_CHALLENGE_RE.match(challenge)
        if not match:
            raise FritzWebUiError(f"unexpected PBKDF2 challenge format: {challenge!r}")
        iter1, salt1_hex, iter2, salt2_hex = match.groups()
        iter1, iter2 = int(iter1), int(iter2)
        salt1 = bytes.fromhex(salt1_hex)
        salt2 = bytes.fromhex(salt2_hex)

        hash1 = hashlib.pbkdf2_hmac("sha256", password.encode(), salt1, iter1, 32)
        hash2 = hashlib.pbkdf2_hmac("sha256", hash1, salt2, iter2, 32)
        return f"{challenge}${hash2.hex()}"

    def _get_session_info(self) -> dict[str, str]:
        """Fetch the login challenge / SID from login_sid.lua."""
        params = {"username": self.username} if self.username else {}
        try:
            resp = self.session.get(f"{self.base_url}/login_sid.lua", params=params)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FritzWebUiError(f"login_sid.lua request failed: {e}") from e

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            raise FritzWebUiError(f"could not parse login_sid.lua response: {e}") from e

        def _find(tag: str) -> str:
            elem = root.find(tag)
            return elem.text if elem is not None and elem.text else ""

        return {
            "sid": _find("SID"),
            "challenge": _find("Challenge"),
            "block_time": _find("BlockTime"),
        }

    def _login(self) -> str:
        """Perform the challenge-response login and return a valid SID."""
        info = self._get_session_info()

        # Already have a valid session (e.g. running without auth).
        if info["sid"] and info["sid"] != "0000000000000000":
            return info["sid"]

        if info["block_time"] and info["block_time"] != "0":
            raise FritzWebUiError(
                f"login blocked for {info['block_time']} seconds (too many failed attempts)"
            )

        challenge = info["challenge"]
        if challenge.startswith("2$"):
            response = self._pbkdf2_response(challenge, self.password)
        else:
            response = self._md5_response(challenge, self.password)

        try:
            resp = self.session.post(
                f"{self.base_url}/login_sid.lua",
                data={"username": self.username, "response": response},
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FritzWebUiError(f"login POST failed: {e}") from e

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            raise FritzWebUiError(f"could not parse login response: {e}") from e

        sid_elem = root.find("SID")
        sid = sid_elem.text if sid_elem is not None else ""
        if not sid or sid == "0000000000000000":
            raise FritzWebUiError("login failed: wrong password or username")

        logger.debug("Fritz!Box web login successful, SID obtained")
        return sid

    def _ensure_sid(self) -> str:
        """Return a cached valid SID, logging in if necessary."""
        if self._sid and time.monotonic() < self._sid_expiry:
            return self._sid
        self._sid = self._login()
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
            resp = self.session.post(f"{self.base_url}/data.lua", data=payload)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FritzWebUiError(f"data.lua request failed: {e}") from e

        text = resp.text
        content_type = str(resp.headers.get("Content-Type", "")) if resp.headers else ""
        is_html = (
            "text/html" in content_type
            or text.lstrip().startswith(_HTML_RESPONSE_PREFIXES)
        )
        if not text or is_html:
            raise FritzWebUiError(
                "Fritz!Box returned HTML instead of JSON (SID invalid or no permission)"
            )

        try:
            return resp.json()
        except ValueError as e:
            raise FritzWebUiError(f"could not parse data.lua JSON response: {e}") from e

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
            logger.debug("data.lua fetch failed, re-authenticating...")
            self._invalidate_sid()
            sid = self._ensure_sid()
            return self._fetch_page(sid, page)

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
            resp = self.session.get(f"{self.base_url}{path}", headers=headers)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FritzWebUiError(f"REST API request failed for {path}: {e}") from e

        text = resp.text
        content_type = str(resp.headers.get("Content-Type", "")) if resp.headers else ""
        is_html = (
            "text/html" in content_type
            or text.lstrip().startswith(_HTML_RESPONSE_PREFIXES)
        )
        if not text or is_html:
            raise FritzWebUiError(
                f"Fritz!Box returned HTML instead of JSON for {path} "
                "(SID invalid or no permission)"
            )

        try:
            return resp.json()
        except ValueError as e:
            raise FritzWebUiError(
                f"could not parse REST API JSON response for {path}: {e}"
            ) from e

    def fetch_api(self, path: str) -> dict[str, Any]:
        """Fetch a Fritz!OS REST API path, re-authenticating once if needed.

        ``path`` is the API path below the host, e.g. ``/api/v0/generic/box``.
        """
        sid = self._ensure_sid()
        try:
            return self._fetch_api(sid, path)
        except FritzWebUiError:
            logger.debug("REST API fetch failed, re-authenticating...")
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
