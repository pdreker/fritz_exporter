"""Fritz!Box event log: parsing, de-duplication and log-line formatting.

The box keeps its event log in RAM and clears it on every restart. TR-064
``DeviceInfo:X_AVM-DE_GetDeviceLogPath`` returns the URL of the complete log as
XML (``<DeviceLog><Event><id/><group/><date/><time/><msg/></Event>...``). This
module holds everything that does not touch the network: the strict parser,
the tracker that picks out entries not yet emitted, the resolution of the
box-local timestamps to a UTC offset and the line formatter. Fetching the XML
is done by the ``EventLog`` capability in :mod:`fritzexporter.fritzcapabilities`.

Every field comes from the box and may contain text typed by a third party
(for example a user name in a failed login), so fields are validated and the
free-text message is escaped before it is written.
"""

from __future__ import annotations

import calendar
import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import Element

from attrs import define
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

__all__ = [
    "Event",
    "EventLogParseError",
    "EventLogTracker",
    "ParsedEventLog",
    "format_event",
    "parse_event_log",
    "resolve_utc_offset",
]

logger = logging.getLogger("fritzexporter.fritz_eventlog")

_GROUP_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
_ID_RE = re.compile(r"[0-9]{1,9}")
_DATE_RE = re.compile(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}")
_TIME_RE = re.compile(r"[0-9]{2}:[0-9]{2}:[0-9]{2}")
# The DynDNS update URL in a message carries the account token as ``q=<token>``.
_TOKEN_RE = re.compile(r"(?<!\w)q=[^&\s]+")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_MAX_REJECT_REPR = 80


class EventLogParseError(Exception):
    """Raised when the event log document as a whole cannot be parsed."""


@define(frozen=True)
class Event:
    """One validated event log entry; ``timestamp`` is the box-local wall-clock time."""

    id: int
    group: str
    timestamp: datetime
    msg: str


@define(frozen=True)
class ParsedEventLog:
    """Valid events in document order (newest first on the box) and rejected entries."""

    events: list[Event]
    rejected: list[str]


def _parse_event(node: Element) -> Event:
    """Validate one ``<Event>`` node, raising ``ValueError`` naming the bad field."""
    fields = {name: node.findtext(name) for name in ("id", "group", "date", "time", "msg")}
    missing = [name for name, value in fields.items() if value is None]
    if missing:
        msg = f"missing {', '.join(missing)}"
        raise ValueError(msg)
    event_id = (fields["id"] or "").strip()
    group = (fields["group"] or "").strip()
    date = (fields["date"] or "").strip()
    time = (fields["time"] or "").strip()
    if not _ID_RE.fullmatch(event_id):
        msg = "invalid id"
        raise ValueError(msg)
    if not _GROUP_RE.fullmatch(group):
        msg = "invalid group"
        raise ValueError(msg)
    if not (_DATE_RE.fullmatch(date) and _TIME_RE.fullmatch(time)):
        msg = "invalid date or time"
        raise ValueError(msg)
    timestamp = datetime.strptime(f"{date} {time}", "%d.%m.%y %H:%M:%S")  # noqa: DTZ007
    return Event(int(event_id), group, timestamp, fields["msg"] or "")


def parse_event_log(xml: str | bytes) -> ParsedEventLog:
    """Parse the ``<DeviceLog>`` document.

    An entry that fails validation is not emitted with a guessed value; it is
    listed in ``rejected`` instead. A document that is not a ``DeviceLog`` raises
    :class:`EventLogParseError`.
    """
    try:
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, DefusedXmlException) as err:
        msg = f"event log is not valid XML: {err}"
        raise EventLogParseError(msg) from err
    if root.tag != "DeviceLog":
        msg = f"unexpected root element {root.tag!r}"
        raise EventLogParseError(msg)

    events: list[Event] = []
    rejected: list[str] = []
    for index, node in enumerate(root.findall("Event")):
        try:
            events.append(_parse_event(node))
        except ValueError as err:
            rejected.append(f"entry {index}: {err}")
    return ParsedEventLog(events, rejected)


class EventLogTracker:
    """Remembers which entries were emitted and returns only the new ones.

    The box returns its whole buffer on every call. Entries are identified by
    (time, message) and counted, so identical entries in the same second are
    each emitted; a folded row, whose time moved and whose message gained a
    counter, is a new entry. :meth:`commit` replaces the counts with the current
    buffer's, so they never outgrow what the box holds, and is called only after
    the entries were emitted: a failure in between loses nothing. After a restart
    of the exporter the buffer is emitted once more: a duplicate, never a loss.
    """

    def __init__(self) -> None:
        self._emitted: Counter[tuple[datetime, str]] = Counter()
        self._rejected_seen: set[str] = set()

    def new_events(self, parsed: ParsedEventLog) -> list[Event]:
        """Return the entries not emitted before, oldest first; does not change state."""
        in_buffer = Counter((e.timestamp, e.msg) for e in parsed.events)
        budget = {key: count - self._emitted[key] for key, count in in_buffer.items()}
        new: list[Event] = []
        # The box lists newest first; walk it backwards to keep equal times in order.
        for event in reversed(parsed.events):
            key = (event.timestamp, event.msg)
            if budget[key] > 0:
                new.append(event)
                budget[key] -= 1
        return sorted(new, key=lambda e: e.timestamp)

    def commit(self, parsed: ParsedEventLog) -> None:
        """Record the buffer as emitted and warn about newly seen rejected entries."""
        self._emitted = Counter((e.timestamp, e.msg) for e in parsed.events)

        rejected = set(parsed.rejected)
        if rejected - self._rejected_seen:
            logger.warning(
                "skipping %d event log entries that failed validation (first: %s)",
                len(rejected),
                min(rejected)[:_MAX_REJECT_REPR],
            )
        self._rejected_seen = rejected


def _escape(text: str) -> str:
    out: list[str] = []
    for char in text:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif unicodedata.category(char) in _ESCAPED_CATEGORIES:
            out.append(f"\\u{ord(char):04x}" if ord(char) <= 0xFFFF else f"\\U{ord(char):08x}")  # noqa: PLR2004
        else:
            out.append(char)
    return "".join(out)


def format_event(event: Event, utc_offset: timedelta | None) -> str:
    """Format an entry as one ``key=value`` line.

    ``event_time`` is ISO 8601 with the UTC offset in force at that time, or
    without an offset when the box's time zone is unknown.
    """
    stamp = event.timestamp
    if utc_offset is not None:
        stamp = stamp.replace(tzinfo=timezone(utc_offset))
    msg = _escape(_TOKEN_RE.sub("q=<redacted>", event.msg))
    return f'event_time={stamp.isoformat()} group={event.group} id={event.id} msg="{msg}"'


# --- POSIX TZ rule (``CET-1CEST,M3.5.0,M10.5.0/3``) -------------------------------------------

_NAME = r"(?:[A-Za-z]{3,}|<[A-Za-z0-9+-]+>)"
_OFFSET = r"[+-]?(?:2[0-3]|[01]?[0-9])(?::[0-9]{2}(?::[0-9]{2})?)?"
_RULE = r"M(1[0-2]|[1-9])\.([1-5])\.([0-6])(?:/([+-]?[0-9]{1,3}(?::[0-9]{2}(?::[0-9]{2})?)?))?"
_TZ_RE = re.compile(
    rf"(?P<std>{_NAME})(?P<std_off>{_OFFSET})"
    rf"(?:(?P<dst>{_NAME})(?P<dst_off>{_OFFSET})?"
    rf",(?P<start>{_RULE}),(?P<end>{_RULE}))?"
)
_DEFAULT_SWITCH_TIME = timedelta(hours=2)


def _hms(text: str) -> timedelta:
    sign = -1 if text.startswith("-") else 1
    parts = [int(p) for p in text.lstrip("+-").split(":")]
    parts += [0] * (3 - len(parts))
    return sign * timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])


def _switch_wall_time(year: int, rule: str) -> datetime:
    """Local wall-clock time at which an ``Mm.w.d[/time]`` rule switches in ``year``."""
    match = re.fullmatch(_RULE, rule)
    if match is None:  # pragma: no cover - checked by the caller's regex
        raise ValueError(rule)
    month, week, weekday = int(match[1]), int(match[2]), int(match[3])
    first_weekday = (datetime(year, month, 1).weekday() + 1) % 7  # noqa: DTZ001
    day = 1 + (weekday - first_weekday) % 7 + (week - 1) * 7
    if day > calendar.monthrange(year, month)[1]:
        day -= 7
    switch = _hms(match[4]) if match[4] else _DEFAULT_SWITCH_TIME
    return datetime(year, month, day) + switch  # noqa: DTZ001


def resolve_utc_offset(
    local: datetime, tz_rule: str | None, fallback: timedelta | None
) -> timedelta | None:
    """UTC offset in force at the box-local wall-clock time ``local``.

    ``tz_rule`` is the POSIX TZ string the box reports as ``NewLocalTimeZoneName``,
    in the ``Mm.w.d`` form (``zoneinfo`` cannot read these). ``fallback`` (the
    current offset) is returned when the rule is missing or does not parse. A time
    in the repeated hour at the end of DST is resolved to the first occurrence (the
    DST offset); a time skipped at the start of DST gets the standard-time offset.
    """
    match = _TZ_RE.fullmatch(tz_rule or "")
    if match is None:
        return fallback
    # POSIX offsets count west of UTC, so the sign is inverted.
    std_offset = -_hms(match["std_off"])
    if match["dst"] is None:
        return std_offset
    dst_offset = -_hms(match["dst_off"]) if match["dst_off"] else std_offset + timedelta(hours=1)

    # Transition instants as naive UTC: the start is read on the standard-time clock,
    # the end on the DST clock.
    start = _switch_wall_time(local.year, match["start"]) - std_offset
    end = _switch_wall_time(local.year, match["end"]) - dst_offset
    as_dst = local - dst_offset
    is_dst = start <= as_dst < end if start < end else as_dst >= start or as_dst < end
    return dst_offset if is_dst else std_offset
