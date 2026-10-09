"""DOCSIS cable channel data collection from the Fritz!Box web interface.

The TR-064 API does not expose DOCSIS channel statistics on AVM cable boxes.
They are available from an internal page the Fritz!Box web UI itself uses
(``data.lua?page=docInfo``). The generic web login and the ``data.lua``/REST
fetching are implemented in :mod:`fritzexporter.fritz_webui` (which is
technology-agnostic); this module holds only the DOCSIS-specific page name,
the normalized channel types and the response parser.

The endpoint and JSON structure were reverse-engineered from the Fritz!Box
web UI and cross-checked against the ``fritzbox-cable-api`` project and the
neobiker.de / dc8wan.de shell scripts.
"""

from __future__ import annotations

import logging
from typing import Any, Final, TypedDict

__all__ = [
    "DOC_INFO_PAGE",
    "DocsisData",
    "DownstreamChannel",
    "UpstreamChannel",
    "parse_docsis_response",
]

logger = logging.getLogger("fritzexporter.fritz_docsis")

#: The ``data.lua`` page that carries the DOCSIS channel statistics.
DOC_INFO_PAGE: Final[str] = "docInfo"


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


def _to_float(value: Any) -> float | None:  # noqa: ANN401
    """Safely convert a value (string or number) to float, None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _to_int(value: Any) -> int | None:  # noqa: ANN401
    """Convert a value to int, None on failure (unknown)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _as_dict(value: Any, rejected: list[int]) -> dict[str, Any]:  # noqa: ANN401
    """Return ``value`` if it is a dict, else an empty dict (counted if not None)."""
    if isinstance(value, dict):
        return value
    if value is not None:
        rejected[0] += 1
    return {}


def _dict_entries(value: Any, rejected: list[int]) -> list[dict[str, Any]]:  # noqa: ANN401
    """Return the dict entries of a list; count what is dropped."""
    if value is None:
        return []
    if not isinstance(value, list):
        rejected[0] += 1
        return []
    entries = [entry for entry in value if isinstance(entry, dict)]
    rejected[0] += len(value) - len(entries)
    return entries


def parse_docsis_response(raw: Any) -> DocsisData:  # noqa: ANN401
    """Parse the raw data.lua?page=docInfo JSON into a normalized structure.

    Returns a :class:`DocsisData` with ``ready_state``, ``downstream`` and
    ``upstream`` lists of channel dicts. Downstream channels carry the quality
    fields (MER/MSE, errors, latency); upstream channels carry
    power/modulation/frequency.
    """
    rejected = [0]
    outer = _as_dict(raw, rejected)
    inner = _as_dict(outer.get("data"), rejected)
    ready_state = inner.get("readyState")
    result: DocsisData = {
        "ready_state": ready_state if isinstance(ready_state, str) else "unknown",
        "downstream": [],
        "upstream": [],
    }

    ch_ds = _as_dict(inner.get("channelDs"), rejected)
    for standard, channels in (
        ("DOCSIS 3.1", _dict_entries(ch_ds.get("docsis31"), rejected)),
        ("DOCSIS 3.0", _dict_entries(ch_ds.get("docsis30"), rejected)),
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

    ch_us = _as_dict(inner.get("channelUs"), rejected)
    for standard, channels in (
        ("DOCSIS 3.1", _dict_entries(ch_us.get("docsis31"), rejected)),
        ("DOCSIS 3.0", _dict_entries(ch_us.get("docsis30"), rejected)),
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

    if rejected[0]:
        logger.warning(
            "DOCSIS reply had an unexpected structure; ignored %d malformed element(s)",
            rejected[0],
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
