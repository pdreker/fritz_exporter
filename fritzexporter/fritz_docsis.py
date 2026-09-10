"""DOCSIS cable channel data collection from the Fritz!Box web interface.

The TR-064 API does not expose DOCSIS channel statistics on AVM cable boxes.
This module collects them from the internal web endpoint the Fritz!Box web UI
itself uses (``data.lua?page=docInfo``), which requires a normal web login
(challenge-response) rather than the TR-064 session used elsewhere in the
exporter.

The endpoint and JSON structure were reverse-engineered from the Fritz!Box
web UI and cross-checked against the ``fritzbox-cable-api`` project and the
neobiker.de / dc8wan.de shell scripts.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, TypedDict

import requests

logger = logging.getLogger("fritzexporter.fritz_docsis")

__all__ = [
    "FritzDocsisError",
    "FritzDocsisClient",
    "parse_docsis_response",
]

# The login_sid.lua challenge-response scheme changed in Fritz!OS 7.24.
# Newer firmware uses PBKDF2-SHA256 ("2$iter1$salt1$iter2$salt2"), older
# firmware uses a simple MD5 over the UTF-16LE encoded challenge-password.
_PBKDF2_CHALLENGE_RE = re.compile(r"^2\$(\d+)\$([0-9a-f]+)\$(\d+)\$([0-9a-f]+)$")

# data.lua returns HTML (the login page) instead of JSON when the SID is
# invalid or the session has expired.
_HTML_RESPONSE_PREFIXES = ("<", "\ufeff<")


class FritzDocsisError(Exception):
    """Raised when DOCSIS data cannot be retrieved from the Fritz!Box."""


class DownstreamChannel(TypedDict):
    """Normalized DOCSIS downstream channel."""

    channel_id: int
    standard: str
    modulation: str
    frequency: str
    power_dbmv: float | None
    mer_db: float | None
    mse_db: float | None
    corrected_errors: int | None
    uncorrected_errors: int | None
    latency_ms: float | None
    fft: str


class UpstreamChannel(TypedDict):
    """Normalized DOCSIS upstream channel."""

    channel_id: int
    standard: str
    modulation: str
    frequency: str
    power_dbmv: float | None


class DocsisData(TypedDict):
    """Normalized DOCSIS channel data."""

    ready_state: str
    downstream: list[DownstreamChannel]
    upstream: list[UpstreamChannel]


class FritzDocsisClient:
    """Fetches DOCSIS channel data from a Fritz!Box Cable via its web UI."""

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
            raise FritzDocsisError(f"unexpected PBKDF2 challenge format: {challenge!r}")
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
            raise FritzDocsisError(f"login_sid.lua request failed: {e}") from e

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            raise FritzDocsisError(f"could not parse login_sid.lua response: {e}") from e

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
            raise FritzDocsisError(
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
            raise FritzDocsisError(f"login POST failed: {e}") from e

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            raise FritzDocsisError(f"could not parse login response: {e}") from e

        sid_elem = root.find("SID")
        sid = sid_elem.text if sid_elem is not None else ""
        if not sid or sid == "0000000000000000":
            raise FritzDocsisError("login failed: wrong password or username")

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
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_docinfo(self, sid: str) -> dict[str, Any]:
        """POST to data.lua and return the parsed JSON document."""
        payload = {"sid": sid, "page": "docInfo", "xhrId": "all", "xhr": "1"}
        try:
            resp = self.session.post(f"{self.base_url}/data.lua", data=payload)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FritzDocsisError(f"data.lua request failed: {e}") from e

        text = resp.text
        content_type = str(resp.headers.get("Content-Type", "")) if resp.headers else ""
        is_html = (
            "text/html" in content_type
            or text.lstrip().startswith(_HTML_RESPONSE_PREFIXES)
        )
        if not text or is_html:
            raise FritzDocsisError(
                "Fritz!Box returned HTML instead of JSON (SID invalid or no permission)"
            )

        try:
            return resp.json()
        except ValueError as e:
            raise FritzDocsisError(f"could not parse data.lua JSON response: {e}") from e

    def fetch_docsis_data(self) -> dict[str, Any]:
        """Fetch fresh DOCSIS data, re-authenticating once if the session expired.

        Each HTTP request is bounded by the client ``timeout``, so the retry
        path (re-login + re-fetch) is bounded to roughly twice that.
        """
        sid = self._ensure_sid()
        try:
            return self._fetch_docinfo(sid)
        except FritzDocsisError:
            # Session may have expired server-side; try re-logging in once.
            logger.debug("DOCSIS data fetch failed, re-authenticating...")
            self._invalidate_sid()
            sid = self._ensure_sid()
            return self._fetch_docinfo(sid)


def _to_float(value: Any) -> float | None:
    """Safely convert a value (string or number) to float, None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    """Convert a value to int, None on failure (unknown)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_docsis_response(raw: dict[str, Any]) -> DocsisData:
    """Parse the raw data.lua?page=docInfo JSON into a normalized structure.

    Returns a :class:`DocsisData` with ``ready_state``, ``downstream`` and
    ``upstream`` lists of channel dicts. Downstream channels carry the quality
    fields (MER/MSE, errors, latency); upstream channels carry
    power/modulation/frequency.
    """
    inner = raw.get("data", {})
    result: DocsisData = {
        "ready_state": inner.get("readyState", "unknown"),
        "downstream": [],
        "upstream": [],
    }

    ch_ds = inner.get("channelDs", {})
    for standard, channels in (
        ("DOCSIS 3.1", ch_ds.get("docsis31", [])),
        ("DOCSIS 3.0", ch_ds.get("docsis30", [])),
    ):
        for ch in channels:
            result["downstream"].append(
                {
                    "channel_id": _to_int(ch.get("channelID")) or 0,
                    "standard": standard,
                    "modulation": str(ch.get("modulation") or ""),
                    "frequency": str(ch.get("frequency") or ""),
                    "power_dbmv": _to_float(ch.get("powerLevel")),
                    "mer_db": _to_float(ch.get("mer")),
                    "mse_db": _to_float(ch.get("mse")),
                    "corrected_errors": _to_int(ch.get("corrErrors")),
                    "uncorrected_errors": _to_int(ch.get("nonCorrErrors")),
                    "latency_ms": _to_float(ch.get("latency")),
                    "fft": str(ch.get("fft") or ""),
                }
            )

    ch_us = inner.get("channelUs", {})
    for standard, channels in (
        ("DOCSIS 3.1", ch_us.get("docsis31", [])),
        ("DOCSIS 3.0", ch_us.get("docsis30", [])),
    ):
        for ch in channels:
            result["upstream"].append(
                {
                    "channel_id": _to_int(ch.get("channelID")) or 0,
                    "standard": standard,
                    "modulation": str(ch.get("modulation") or ""),
                    "frequency": str(ch.get("frequency") or ""),
                    "power_dbmv": _to_float(ch.get("powerLevel")),
                }
            )

    return result


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
