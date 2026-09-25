"""Technology-agnostic Fritz!Box REST API (/api/v0/...) data collection.

The Fritz!OS ``/api/v0`` REST endpoints expose a number of statistics -
per-WAN-connection state and network-utilization monitor segments - that are
not available over the TR-064 API. They require a normal web login via the
:class:`~fritzexporter.fritz_webui.FritzWebUiClient`, which is technology-
agnostic: unlike the DOCSIS channel data, these endpoints are not specific to
cable boxes and work on any Fritz!OS box with Fritz!OS 7+.

This module holds the domain types and parsers for those endpoint responses.
"""

from __future__ import annotations

from typing import Any, TypedDict

__all__ = [
    "SegmentSeries",
    "MonitorSegmentData",
    "ConnectionInfo",
    "parse_monitor_segment",
    "parse_connections_response",
]


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
    ip4_addr: str
    ip6_addr: str


def _to_float(value: Any) -> float | None:
    """Safely convert a value (string or number) to float, None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _to_int(value: Any) -> int | None:
    """Convert a value to int, None on failure (unknown)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def parse_monitor_segment(raw: dict[str, Any]) -> MonitorSegmentData:
    """Parse a ``/api/v0/monitor/segment/<n>`` response into normalized data.

    The response has a top-level ``lastSampleTime`` (Unix epoch seconds of the
    newest bucket) and a ``data`` list holding one series per ``type`` — ``own``
    (traffic this box generates) and ``total`` (traffic of every subscriber in
    the shared segment). Each series carries ``downstream`` and ``upstream``
    utilization series in percent; the newest sample is the last element of
    each list. Unknown/non-numeric samples become ``None``.
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

    ``raw`` is the list of connection entries. Uptime fields are numeric
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
                "ip4_addr": str(entry.get("ip4_masqaddr") or ""),
                "ip6_addr": str(entry.get("ip6_addr") or ""),
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
