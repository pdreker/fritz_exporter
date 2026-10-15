"""Typed reading of device replies.

Every value the exporter takes from a device - a TR-064 action result, a JSON
object from ``data.lua`` or the REST API, an element of the AHA XML - is read
through the helpers in this module, never by indexing the raw data. One rule
applies everywhere:

* A value that is missing, ``None`` or ``""`` is *absent*. Required reads raise
  :class:`DataError`, optional reads (``opt_*``) return ``None``.
* A value that is present but cannot be read as the requested type raises
  :class:`DataError`.

Numbers come back as ``int`` or ``float`` and labels as ``str``, so nothing of
the wrong type can reach a metric and fail later, when Prometheus renders the
scrape. ``FritzCapability.get_metrics`` catches :class:`DataError` and skips
that capability for that device in that scrape.

A :class:`DataError` names the source and the field, never the value: values
can carry addresses, names or tokens.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fritzexporter.fritzdevice import FritzDevice

__all__ = [
    "DataError",
    "Reply",
    "read_action",
    "to_bool",
    "to_float",
    "to_int",
    "to_str",
]

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class DataError(ValueError):
    """A device reply lacks a required value or has one of the wrong type."""


def _is_absent(value: object) -> bool:
    return value is None or value == ""


def _fail(what: str, expected: str, value: object) -> DataError:
    return DataError(f"{what}: expected {expected}, got {type(value).__name__}")


def to_float(value: object, what: str) -> float | None:
    """Read a number; ``None`` if absent. Numeric strings are accepted."""
    if _is_absent(value):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise _fail(what, "a number", value)
    try:
        return float(value)
    except ValueError:
        raise _fail(what, "a number", value) from None


def to_int(value: object, what: str) -> int | None:
    """Read a whole number; ``None`` if absent. Integral floats are accepted."""
    if _is_absent(value):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise _fail(what, "a whole number", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not (math.isfinite(value) and value.is_integer()):
            raise _fail(what, "a whole number", value)
        return int(value)
    try:
        return int(value.strip())
    except ValueError:
        raise _fail(what, "a whole number", value) from None


def to_bool(value: object, what: str) -> bool | None:
    """Read a flag (bool, 0/1 or a word such as "true"); ``None`` if absent."""
    if _is_absent(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
    raise _fail(what, "a flag", value)


def to_str(value: object, what: str) -> str | None:
    """Read a scalar as text (for labels); ``None`` if absent."""
    if _is_absent(value):
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str | int | float):
        return str(value)
    raise _fail(what, "text", value)


class Reply:
    """Read-only, typed view on one object of a device reply."""

    __slots__ = ("_data", "_source")

    def __init__(self, data: object, source: str) -> None:
        if not isinstance(data, Mapping):
            raise _fail(source, "an object", data)
        self._data: Mapping[str, Any] = data
        self._source = source

    def __repr__(self) -> str:
        return f"Reply({self._source}, keys={sorted(self._data)})"

    def _what(self, key: str) -> str:
        return f"{self._source} {key}"

    def raw(self, key: str) -> Any:  # noqa: ANN401
        """The unconverted value; only for values that are passed on as they are."""
        return self._data.get(key)

    def has(self, key: str) -> bool:
        """Whether ``key`` holds a value that is not absent."""
        return not _is_absent(self._data.get(key))

    def _required[T](self, value: T | None, key: str) -> T:
        if value is None:
            msg = f"{self._what(key)}: missing"
            raise DataError(msg)
        return value

    def opt_num(self, key: str) -> float | None:
        return to_float(self._data.get(key), self._what(key))

    def num(self, key: str) -> float:
        return self._required(self.opt_num(key), key)

    def opt_integer(self, key: str) -> int | None:
        return to_int(self._data.get(key), self._what(key))

    def integer(self, key: str) -> int:
        return self._required(self.opt_integer(key), key)

    def opt_flag(self, key: str) -> bool | None:
        return to_bool(self._data.get(key), self._what(key))

    def flag(self, key: str) -> bool:
        return self._required(self.opt_flag(key), key)

    def opt_text(self, key: str) -> str | None:
        return to_str(self._data.get(key), self._what(key))

    def text(self, key: str, default: str | None = None) -> str:
        """Read a label; ``default`` replaces an absent value, else it is an error."""
        value = self.opt_text(key)
        if value is None and default is not None:
            return default
        return self._required(value, key)

    def opt_obj(self, key: str) -> Reply | None:
        value = self._data.get(key)
        if _is_absent(value):
            return None
        return Reply(value, self._what(key))

    def obj(self, key: str) -> Reply:
        return self._required(self.opt_obj(key), key)

    def objects(self, key: str) -> list[Reply]:
        """Read a list of objects; an absent list is empty."""
        return [
            Reply(item, f"{self._what(key)}[{index}]") for index, item in enumerate(self.items(key))
        ]

    def items(self, key: str) -> list[Any]:
        """Read a list of unconverted items; an absent list is empty."""
        value = self._data.get(key)
        if _is_absent(value):
            return []
        if not isinstance(value, list):
            raise _fail(self._what(key), "a list", value)
        return value


def read_action(device: FritzDevice, service: str, action: str, **arguments: Any) -> Reply:  # noqa: ANN401
    """Call a TR-064 action and return its result as a :class:`Reply`.

    fritzconnection converts the documented TR-064 types (``ui4``, ``boolean``,
    ...), but passes others such as ``ui8`` on as strings, and a box may send an
    empty string for any of them; the accessors cover both.
    """
    result = device.fc.call_action(service, action, **arguments)
    return Reply(result, f"{service}.{action}")


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
