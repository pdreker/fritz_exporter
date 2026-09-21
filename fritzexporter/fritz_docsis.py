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
    "parse_monitor_segment",
    "parse_connections_response",
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


class SegmentSeries(TypedDict):
    """One ``/api/v0/monitor/segment/<n>`` series (own or total).

    ``downstream`` and ``upstream`` are the raw per-sample utilization lists
    (percent). The newest sample is the last element of each list.
    """

    media_type: str
    type: str
    downstream: list[float | None]
    upstream: list[float | None]


class MonitorSegmentData(TypedDict):
    """Normalized ``/api/v0/monitor/segment/<n>`` response."""

    last_sample_time: int | None
    series: list[SegmentSeries]


class ConnectionInfo(TypedDict):
    """Normalized ``/api/v0/generic/connections`` entry for one connection.

    Uptimes are in seconds; ``None`` means the field was empty/absent in the
    response (e.g. a disabled connection reports an empty uptime).
    """

    uid: str
    name: str
    media_type: str
    ip4_connstatus: str
    ip6_connstatus: str
    ip4_uptime: int | None
    ip6_uptime: int | None


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

    def _fetch_page(self, sid: str, page: str) -> dict[str, Any]:
        """POST to data.lua and return the parsed JSON document."""
        payload = {"sid": sid, "page": page, "xhrId": "all", "xhr": "1"}
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

    def fetch_page(self, page: str) -> dict[str, Any]:
        """Fetch a ``data.lua`` page, re-authenticating once if the session expired.

        Each HTTP request is bounded by the client ``timeout``, so the retry
        path (re-login + re-fetch) is bounded to roughly twice that.
        """
        sid = self._ensure_sid()
        try:
            return self._fetch_page(sid, page)
        except FritzDocsisError:
            # Session may have expired server-side; try re-logging in once.
            logger.debug("data.lua fetch failed, re-authenticating...")
            self._invalidate_sid()
            sid = self._ensure_sid()
            return self._fetch_page(sid, page)

    def fetch_docsis_data(self) -> dict[str, Any]:
        """Fetch fresh DOCSIS data (the ``docInfo`` page)."""
        return self.fetch_page("docInfo")

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
            raise FritzDocsisError(f"REST API request failed for {path}: {e}") from e

        text = resp.text
        content_type = str(resp.headers.get("Content-Type", "")) if resp.headers else ""
        is_html = (
            "text/html" in content_type
            or text.lstrip().startswith(_HTML_RESPONSE_PREFIXES)
        )
        if not text or is_html:
            raise FritzDocsisError(
                f"Fritz!Box returned HTML instead of JSON for {path} "
                "(SID invalid or no permission)"
            )

        try:
            return resp.json()
        except ValueError as e:
            raise FritzDocsisError(
                f"could not parse REST API JSON response for {path}: {e}"
            ) from e

    def fetch_api(self, path: str) -> dict[str, Any]:
        """Fetch a Fritz!OS REST API path, re-authenticating once if needed.

        ``path`` is the API path below the host, e.g. ``/api/v0/generic/box``.
        """
        sid = self._ensure_sid()
        try:
            return self._fetch_api(sid, path)
        except FritzDocsisError:
            logger.debug("REST API fetch failed, re-authenticating...")
            self._invalidate_sid()
            sid = self._ensure_sid()
            return self._fetch_api(sid, path)

    def fetch_monitor_segment(self, segment: int = 0) -> dict[str, Any]:
        """Fetch a network-utilization monitor segment (shared cable medium).

        Segment ``0`` covers the last hour, split into minute-granular average
        samples; higher indices are longer-horizon aggregates. Returns the raw
        JSON document (see :func:`parse_monitor_segment`).
        """
        return self.fetch_api(f"/api/v0/monitor/segment/{segment}")

    def fetch_connections(self) -> list[dict[str, Any]]:
        """Fetch the ``/api/v0/generic/connections`` list.

        Returns the raw ``connection`` list from the response. Each entry
        describes one configured WAN connection (e.g. the active cable
        ``internet`` connection and disabled fallbacks) and carries
        ``ip4_uptime``/``ip6_uptime`` (seconds) and the
        ``ip4_connstatus``/``ip6_connstatus`` state strings.
        """
        raw = self.fetch_api("/api/v0/generic/connections")
        return raw.get("connection", [])


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


def parse_monitor_segment(raw: dict[str, Any]) -> MonitorSegmentData:
    """Parse a ``/api/v0/monitor/segment/<n>`` response into normalized data.

    The response has a top-level ``lastSampleTime`` (Unix epoch seconds of the
    newest bucket) and a ``data`` list holding one series per ``type`` — ``own``
    (traffic this box generates) and ``total`` (traffic of every subscriber in
    the shared cable segment). Each series carries ``downstream`` and
    ``upstream`` utilization series in percent; the newest sample is the last
    element of each list. Unknown/non-numeric samples become ``None``.
    """
    series: list[SegmentSeries] = []
    for entry in raw.get("data", []):
        series.append(
            {
                "media_type": str(entry.get("mediaType") or ""),
                "type": str(entry.get("type") or ""),
                "downstream": [_to_float(v) for v in entry.get("downstream", [])],
                "upstream": [_to_float(v) for v in entry.get("upstream", [])],
            }
        )
    result: MonitorSegmentData = {
        "last_sample_time": _to_int(raw.get("lastSampleTime")),
        "series": series,
    }
    return result


def parse_connections_response(raw: list[dict[str, Any]]) -> list[ConnectionInfo]:
    """Parse the ``connection`` list from ``/api/v0/generic/connections``.

    ``raw`` is the list of connection entries (see
    :meth:`FritzDocsisClient.fetch_connections`). Uptime fields are numeric
    strings in seconds; empty or missing values (reported for disabled
    connections) become ``None`` so callers can skip them.
    """
    connections: list[ConnectionInfo] = []
    for entry in raw:
        connections.append(
            {
                "uid": str(entry.get("UID") or ""),
                "name": str(entry.get("name") or ""),
                "media_type": str(entry.get("media_type") or ""),
                "ip4_connstatus": str(entry.get("ip4_connstatus") or ""),
                "ip6_connstatus": str(entry.get("ip6_connstatus") or ""),
                "ip4_uptime": _to_int(entry.get("ip4_uptime")),
                "ip6_uptime": _to_int(entry.get("ip6_uptime")),
            }
        )
    return connections


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
