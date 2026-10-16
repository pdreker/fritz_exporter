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

from typing import Final, TypedDict

from fritzexporter.reply import DataError, Issues, Reply, to_float

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


def _newest(data: Reply, key: str) -> float | None:
    """Return the newest entry of a ``[[weekday, hour, value], ...]`` series in dBm."""
    rows = data.items(key)
    if not rows:
        return None
    row = rows[0]
    row_len = 3
    if not isinstance(row, list) or len(row) < row_len:
        msg = f"fiberStats {key}[0]: expected [weekday, hour, value]"
        raise DataError(msg)
    value = to_float(row[2], f"fiberStats {key}[0] value")
    return None if value is None else value / 10


def parse_fiber_stats_response(raw: object, issues: Issues | None = None) -> FiberLevels:
    """Extract the newest receive (``opt_ds``) and transmit (``opt_us``) level.

    A missing series gives ``None``; a defective one too, recorded in
    ``issues``. A reply of the wrong shape raises
    :class:`~fritzexporter.reply.DataError`.
    """
    issues = issues if issues is not None else Issues()
    data = Reply(raw, "fiberStats").opt_obj("data") or Reply({}, "fiberStats data")
    return {
        "receive_dbm": issues.value(lambda: _newest(data, "opt_ds")),
        "transmit_dbm": issues.value(lambda: _newest(data, "opt_us")),
    }
