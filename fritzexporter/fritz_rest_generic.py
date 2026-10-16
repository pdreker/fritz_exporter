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

from typing import TypedDict

from fritzexporter.reply import Issues, Reply, to_float

__all__ = [
    "ConnectionInfo",
    "MonitorSegmentData",
    "SegmentSeries",
    "parse_connections_response",
    "parse_monitor_segment",
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


def _segment_series(entry: Reply, issues: Issues) -> SegmentSeries:
    return {
        "media_type": entry.text("mediaType", ""),
        "type": entry.text("type", ""),
        "downstream": [
            issues.convert(v, to_float, entry.where("downstream"))
            for v in entry.items("downstream")
        ],
        "upstream": [
            issues.convert(v, to_float, entry.where("upstream")) for v in entry.items("upstream")
        ],
    }


def parse_monitor_segment(raw: object, issues: Issues) -> MonitorSegmentData:
    """Parse a ``/api/v0/monitor/segment/<n>`` response into normalized data.

    The response has a top-level ``lastSampleTime`` (Unix epoch seconds of the
    newest bucket) and a ``data`` list holding one series per ``type`` — ``own``
    (traffic this box generates) and ``total`` (traffic of every subscriber in
    the shared segment). Each series carries ``downstream`` and ``upstream``
    utilization series in percent; the newest sample is the last element of
    each list.

    Empty samples become ``None``. A defective sample also becomes ``None`` and
    a defective series is left out, both recorded in ``issues``; data of the
    wrong shape, or without ``data``, raises :class:`~fritzexporter.reply.DataError`.
    An empty ``data`` (a box on a medium that is not shared) gives no series.
    """
    reply = Reply(raw, "monitor segment").require_key("data")
    series: list[SegmentSeries] = []
    for index, entry in issues.objects(reply, "data"):
        with issues.item(f"monitor segment series {index}"):
            series.append(_segment_series(entry, issues))
    return {
        "last_sample_time": issues.opt_integer(reply, "lastSampleTime"),
        "series": series,
    }


def _connection(entry: Reply, issues: Issues) -> ConnectionInfo:
    return {
        # Labels of every sample of the connection, so they are read strictly.
        "uid": entry.text("UID", ""),
        "name": entry.text("name", ""),
        "media_type": entry.text("media_type", ""),
        "ip4_connstatus": entry.text("ip4_connstatus", ""),
        "ip6_connstatus": entry.text("ip6_connstatus", ""),
        "ip4_uptime": issues.opt_integer(entry, "ip4_uptime"),
        "ip6_uptime": issues.opt_integer(entry, "ip6_uptime"),
        "ip4_addr": entry.text("ip4_masqaddr", ""),
        "ip6_addr": entry.text("ip6_addr", ""),
    }


def parse_connections_response(raw: object, issues: Issues) -> list[ConnectionInfo]:
    """Parse the ``connection`` list of an ``/api/v0/generic/connections`` response.

    Uptime fields are numeric strings in seconds; empty or missing values
    (reported for disabled connections) become ``None`` so callers can skip them.
    A defective uptime also becomes ``None`` and a connection with defective
    labels is left out, both recorded in ``issues``; data of the wrong shape, or
    without ``connection``, raises :class:`~fritzexporter.reply.DataError`.
    """
    connections: list[ConnectionInfo] = []
    reply = Reply(raw, "connections").require_key("connection")
    for index, entry in issues.objects(reply, "connection"):
        with issues.item(f"connection {index}"):
            connections.append(_connection(entry, issues))
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
