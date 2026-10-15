"""Fibre optical levels from the Fritz!Box web interface.

On some fibre boxes the TR-064 action ``X_AVM-DE_WANFiber1 GetInfo`` answers
``0`` for ``NewOpticalSignalLevel`` and ``NewTransmitOpticalLevel`` although the
web UI shows real values (seen on a FRITZ!Box 5530 Fiber with an AON SFP,
Fritz!OS 8.25). The web UI reads them from ``data.lua?page=fiberStats``: one
value per hour for the last week, newest first, in tenths of a dBm. The generic
web login and the ``data.lua`` fetching are in :mod:`fritzexporter.fritz_webui`;
this module holds only the page name and the response parser.
"""

from __future__ import annotations

from typing import Any, Final, TypedDict

__all__ = [
    "FIBER_STATS_PAGE",
    "FiberLevels",
    "parse_fiber_stats_response",
]

#: The ``data.lua`` page that carries the hourly optical level history.
FIBER_STATS_PAGE: Final[str] = "fiberStats"


class FiberLevels(TypedDict):
    """Newest optical levels in dBm; ``None`` where the page has no value."""

    receive_dbm: float | None
    transmit_dbm: float | None


def _newest(series: Any) -> float | None:  # noqa: ANN401
    """Return the newest entry of a ``[[weekday, hour, value], ...]`` series in dBm."""
    row_len = 3
    if not isinstance(series, list) or not series:
        return None
    row = series[0]
    if not isinstance(row, list) or len(row) < row_len:
        return None
    value = row[2]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value / 10


def parse_fiber_stats_response(raw: dict[str, Any]) -> FiberLevels:
    """Extract the newest receive (``opt_ds``) and transmit (``opt_us``) level."""
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        data = {}
    return {
        "receive_dbm": _newest(data.get("opt_ds")),
        "transmit_dbm": _newest(data.get("opt_us")),
    }
