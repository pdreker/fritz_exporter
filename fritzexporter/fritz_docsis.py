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

from fritzexporter.reply import Issues, Reply

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
    modulation: str | None
    frequency: str | None
    power_dbmv: float | None
    mer_db: float | None
    mse_db: float | None
    corrected_errors: int | None
    uncorrected_errors: int | None
    latency_ms: float | None
    fft: str | None


class UpstreamChannel(TypedDict):
    """Normalized DOCSIS upstream channel."""

    channel_id: int
    standard: str
    modulation: str | None
    frequency: str | None
    power_dbmv: float | None


class DocsisData(TypedDict):
    """Normalized DOCSIS channel data."""

    ready_state: str
    downstream: list[DownstreamChannel]
    upstream: list[UpstreamChannel]


def _channels(section: Reply | None, issues: Issues) -> list[tuple[str, Reply]]:
    """The channels of a ``channelDs``/``channelUs`` section with their standard."""
    if section is None:
        return []
    return [
        (standard, channel)
        for standard, key in (("DOCSIS 3.1", "docsis31"), ("DOCSIS 3.0", "docsis30"))
        for _, channel in issues.objects(section, key)
    ]


def _upstream(ch: Reply, standard: str, issues: Issues) -> UpstreamChannel:
    return {
        # Every metric of a channel carries its id, so it is read strictly.
        "channel_id": ch.opt_integer("channelID") or 0,
        "standard": standard,
        "modulation": issues.text(ch, "modulation", ""),
        "frequency": issues.text(ch, "frequency", ""),
        "power_dbmv": issues.opt_num(ch, "powerLevel"),
    }


def _downstream(ch: Reply, standard: str, issues: Issues) -> DownstreamChannel:
    """The upstream fields plus the downstream quality fields."""
    return {
        **_upstream(ch, standard, issues),
        "mer_db": issues.opt_num(ch, "mer"),
        "mse_db": issues.opt_num(ch, "mse"),
        "corrected_errors": issues.opt_integer(ch, "corrErrors"),
        "uncorrected_errors": issues.opt_integer(ch, "nonCorrErrors"),
        "latency_ms": issues.opt_num(ch, "latency"),
        "fft": issues.text(ch, "fft", ""),
    }


def parse_docsis_response(raw: object, issues: Issues | None = None) -> DocsisData:
    """Parse the raw data.lua?page=docInfo JSON into a normalized structure.

    Returns a :class:`DocsisData` with ``ready_state``, ``downstream`` and
    ``upstream`` lists of channel dicts. Downstream channels carry the quality
    fields (MER/MSE, errors, latency); upstream channels carry
    power/modulation/frequency.

    Empty values become ``None``. A defective value also becomes ``None`` and a
    channel with a defective id is left out, both recorded in ``issues``; data
    of the wrong shape raises :class:`~fritzexporter.reply.DataError`.
    """
    issues = issues if issues is not None else Issues()
    inner = Reply(raw, "docInfo").opt_obj("data") or Reply({}, "docInfo data")
    result: DocsisData = {
        "ready_state": issues.text(inner, "readyState", "unknown") or "unknown",
        "downstream": [],
        "upstream": [],
    }
    for standard, ch in _channels(inner.opt_obj("channelDs"), issues):
        with issues.item(f"downstream channel {ch.where('channelID')}"):
            result["downstream"].append(_downstream(ch, standard, issues))
    for standard, ch in _channels(inner.opt_obj("channelUs"), issues):
        with issues.item(f"upstream channel {ch.where('channelID')}"):
            result["upstream"].append(_upstream(ch, standard, issues))
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
