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

from typing import Final, TypedDict

from fritzexporter.reply import Reply

__all__ = [
    "DOC_INFO_PAGE",
    "DocsisData",
    "DownstreamChannel",
    "UpstreamChannel",
    "parse_docsis_response",
]

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


def _channels(section: Reply | None) -> list[tuple[str, Reply]]:
    """The channels of a ``channelDs``/``channelUs`` section with their standard."""
    if section is None:
        return []
    return [
        (standard, channel)
        for standard, key in (("DOCSIS 3.1", "docsis31"), ("DOCSIS 3.0", "docsis30"))
        for channel in section.objects(key)
    ]


def parse_docsis_response(raw: object) -> DocsisData:
    """Parse the raw data.lua?page=docInfo JSON into a normalized structure.

    Returns a :class:`DocsisData` with ``ready_state``, ``downstream`` and
    ``upstream`` lists of channel dicts. Downstream channels carry the quality
    fields (MER/MSE, errors, latency); upstream channels carry
    power/modulation/frequency. Empty values become ``None``; malformed data
    raises :class:`~fritzexporter.reply.DataError`.
    """
    inner = Reply(raw, "docInfo").opt_obj("data") or Reply({}, "docInfo data")
    return {
        "ready_state": inner.text("readyState", "unknown"),
        "downstream": [
            {
                "channel_id": ch.opt_integer("channelID") or 0,
                "standard": standard,
                "modulation": ch.text("modulation", ""),
                "frequency": ch.text("frequency", ""),
                "power_dbmv": ch.opt_num("powerLevel"),
                "mer_db": ch.opt_num("mer"),
                "mse_db": ch.opt_num("mse"),
                "corrected_errors": ch.opt_integer("corrErrors"),
                "uncorrected_errors": ch.opt_integer("nonCorrErrors"),
                "latency_ms": ch.opt_num("latency"),
                "fft": ch.text("fft", ""),
            }
            for standard, ch in _channels(inner.opt_obj("channelDs"))
        ],
        "upstream": [
            {
                "channel_id": ch.opt_integer("channelID") or 0,
                "standard": standard,
                "modulation": ch.text("modulation", ""),
                "frequency": ch.text("frequency", ""),
                "power_dbmv": ch.opt_num("powerLevel"),
            }
            for standard, ch in _channels(inner.opt_obj("channelUs"))
        ],
    }


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
