"""Typed reading of device replies.

Every value the exporter takes from a device - a TR-064 action result, a JSON
object from ``data.lua`` or the REST API, an element of the AHA XML - is read
through the helpers in this module, never by indexing the raw data. One rule
applies everywhere:

* A value that is missing, ``None``, ``""``, ``[]`` or ``{}`` is *absent*.
  Required reads raise :class:`DataError`, optional reads (``opt_*``) return
  ``None``. Empty containers count as absent because JSON encoders for Lua
  (which the web interface is written in) cannot tell an empty list from an
  empty object and send either.
* A value that is present but cannot be read as the requested type raises
  :class:`DataError`.

Numbers come back as ``int`` or ``float`` and labels as ``str``, so nothing of
the wrong type can reach a metric and fail later, when Prometheus renders the
scrape.

A failure stays at the level it affects: :class:`Issues` contains a
:class:`DataError` at the value or item level and records it, so a defective
value costs only the samples built from it and a defective item (a host, a
channel) only that item. Whatever a level depends on - the structure of a
reply, a label shared by all its samples - is read outside, so its failure
moves up to the capability (see ``FritzCapability.get_metrics``).

A :class:`DataError` names the source and the field, never the value: values
can carry addresses, names or tokens.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fritzexporter.fritzdevice import FritzDevice

__all__ = [
    "DataError",
    "Issues",
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
    if isinstance(value, list | dict):
        return not value
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

    def where(self, key: str) -> str:
        """``source key``, as used in error messages."""
        return f"{self._source} {key}"

    def _required[T](self, value: T | None, key: str) -> T:
        if value is None:
            msg = f"{self.where(key)}: missing"
            raise DataError(msg)
        return value

    def opt_num(self, key: str) -> float | None:
        return to_float(self._data.get(key), self.where(key))

    def num(self, key: str) -> float:
        return self._required(self.opt_num(key), key)

    def opt_integer(self, key: str) -> int | None:
        return to_int(self._data.get(key), self.where(key))

    def integer(self, key: str) -> int:
        return self._required(self.opt_integer(key), key)

    def opt_flag(self, key: str) -> bool | None:
        return to_bool(self._data.get(key), self.where(key))

    def flag(self, key: str) -> bool:
        return self._required(self.opt_flag(key), key)

    def opt_text(self, key: str) -> str | None:
        return to_str(self._data.get(key), self.where(key))

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
        return Reply(value, self.where(key))

    def items(self, key: str) -> list[Any]:
        """Read a list of unconverted items; an absent list is empty."""
        value = self._data.get(key)
        if _is_absent(value):
            return []
        if not isinstance(value, list):
            raise _fail(self.where(key), "a list", value)
        return value


class Issues:
    """Defects contained at the value or item level during one collection.

    The value readers behave like the :class:`Reply` methods of the same name,
    but record a :class:`DataError` and return ``None`` instead of raising.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def value[T](self, read: Callable[[], T | None]) -> T | None:
        """Run ``read``; a :class:`DataError` is recorded and gives ``None``."""
        try:
            return read()
        except DataError as e:
            self.messages.append(str(e))
            return None

    def num(self, reply: Reply, key: str, divide_by: float = 1) -> float | None:
        value = self.value(lambda: reply.num(key))
        return None if value is None else value / divide_by

    def opt_num(self, reply: Reply, key: str, divide_by: float = 1) -> float | None:
        value = self.value(lambda: reply.opt_num(key))
        return None if value is None else value / divide_by

    def num_or(self, reply: Reply, key: str, default: float) -> float | None:
        """``default`` if the value is absent, ``None`` if it is defective."""
        try:
            value = reply.opt_num(key)
        except DataError as e:
            self.messages.append(str(e))
            return None
        return default if value is None else value

    def text(self, reply: Reply, key: str, default: str) -> str | None:
        """A label; ``default`` if absent, ``None`` if defective."""
        return self.value(lambda: reply.text(key, default))

    def opt_integer(self, reply: Reply, key: str) -> int | None:
        return self.value(lambda: reply.opt_integer(key))

    def flag(self, reply: Reply, key: str) -> int | None:
        """A required flag as 1 or 0."""
        value = self.value(lambda: reply.flag(key))
        return None if value is None else int(value)

    def equals(self, reply: Reply, key: str, expected: str) -> int | None:
        """1 if the text at ``key`` is ``expected``, else 0 (also when absent)."""
        value = self.value(lambda: reply.opt_text(key) or "")
        return None if value is None else int(value == expected)

    def convert[T](
        self, value: object, convert: Callable[[object, str], T | None], what: str
    ) -> T | None:
        """Convert a single value (a list item, XML text) with ``to_float`` and friends."""
        return self.value(lambda: convert(value, what))

    def objects(self, reply: Reply, key: str) -> Iterator[tuple[int, Reply]]:
        """The objects of the list at ``key``; an element that is no object is skipped.

        An absent list is empty; a value at ``key`` that is no list at all raises.
        """
        for index, element in enumerate(reply.items(key)):
            what = f"{reply.where(key)}[{index}]"
            try:
                obj = Reply(element, what)
            except DataError as e:
                self.messages.append(f"{what} skipped: {e}")
                continue
            yield index, obj

    @contextmanager
    def item(self, what: str, on_skip: Callable[[], None] | None = None) -> Iterator[None]:
        """Skip the rest of the block on a :class:`DataError` and record it.

        ``on_skip`` undoes what the block did before it failed.
        """
        try:
            yield
        except DataError as e:
            if on_skip is not None:
                on_skip()
            self.messages.append(f"{what} skipped: {e}")


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
