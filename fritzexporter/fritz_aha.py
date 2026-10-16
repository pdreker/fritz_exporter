"""Smart home (AHA) device list parsing.

Values are read with the helpers of :mod:`fritzexporter.reply`: an empty or
missing element is absent (``None``); a malformed one is ``None`` too and is
recorded in the :class:`~fritzexporter.reply.Issues` passed in. A document that
is not XML raises :class:`~fritzexporter.reply.DataError`.
"""

from typing import TypedDict
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException, ElementTree

from fritzexporter.reply import DataError, Issues, to_int


class AhaSwitch(TypedDict):
    state: str | None
    mode: str | None
    lock: str | None


class AhaPowermeter(TypedDict):
    power: int | None  # mW
    energy: int | None  # Wh


class AhaTemperature(TypedDict):
    celsius: int | None  # tenths of a degree
    offset: int | None


class AhaHeater(TypedDict):
    """Temperatures in half degrees (253/254 mean off/on)."""

    tist: int | None
    tsoll: int | None
    absenk: int | None
    komfort: int | None
    battery_level: int | None
    battery_low: str | None


class AhaDevice(TypedDict):
    ain: str
    device_id: str
    manufacturer: str
    productname: str
    device_name: str
    present: bool
    battery_level: int | None
    battery_low: str | None
    switch: AhaSwitch | None
    powermeter: AhaPowermeter | None
    temperature: AhaTemperature | None
    hkr: AhaHeater | None


def _findtext(elem: Element, tag: str) -> str | None:
    text = elem.findtext(tag)
    if text is None:
        return None
    return text.strip() or None


def _find_int(elem: Element, tag: str, issues: Issues, where: str) -> int | None:
    """``tag`` of ``elem`` as an int; ``where`` names the device in the issue text."""
    part = "" if elem.tag == "device" else f"{elem.tag} "
    return issues.convert(_findtext(elem, tag), to_int, f"{where} {part}{tag}")


def _parse_switch(device: Element) -> AhaSwitch | None:
    switch = device.find("switch")
    if switch is None:
        return None
    return {
        "state": _findtext(switch, "state"),
        "mode": _findtext(switch, "mode"),
        "lock": _findtext(switch, "lock"),
    }


def _parse_powermeter(device: Element, issues: Issues, where: str) -> AhaPowermeter | None:
    powermeter = device.find("powermeter")
    if powermeter is None:
        return None
    return {
        "power": _find_int(powermeter, "power", issues, where),
        "energy": _find_int(powermeter, "energy", issues, where),
    }


def _parse_temperature(device: Element, issues: Issues, where: str) -> AhaTemperature | None:
    temperature = device.find("temperature")
    if temperature is None:
        return None
    return {
        "celsius": _find_int(temperature, "celsius", issues, where),
        "offset": _find_int(temperature, "offset", issues, where),
    }


def _parse_hkr(device: Element, issues: Issues, where: str) -> AhaHeater | None:
    hkr = device.find("hkr")
    if hkr is None:
        return None
    return {
        "tist": _find_int(hkr, "tist", issues, where),
        "tsoll": _find_int(hkr, "tsoll", issues, where),
        "absenk": _find_int(hkr, "absenk", issues, where),
        "komfort": _find_int(hkr, "komfort", issues, where),
        "battery_level": _find_int(hkr, "battery", issues, where),
        "battery_low": _findtext(hkr, "batterylow"),
    }


def parse_aha_devicelist_xml(content: str, issues: Issues) -> list[AhaDevice]:
    try:
        devicelist: Element = ElementTree.fromstring(content)
    except ElementTree.ParseError, DefusedXmlException:
        msg = "AHA device list: not valid XML"
        raise DataError(msg) from None
    if devicelist.tag != "devicelist":
        msg = "AHA device list: no <devicelist> element"
        raise DataError(msg)

    devices: list[AhaDevice] = []
    for device in devicelist.findall("device"):
        ain = device.attrib.get("identifier", "")
        where = f"AHA device {ain}"
        hkr = _parse_hkr(device, issues, where)
        # On some firmware versions battery data is only reported nested
        # inside <hkr> instead of as a direct child of <device>.
        battery_level = _find_int(device, "battery", issues, where)
        battery_low = _findtext(device, "batterylow")
        if hkr is not None:
            if battery_level is None:
                battery_level = hkr["battery_level"]
            if battery_low is None:
                battery_low = hkr["battery_low"]

        devices.append(
            {
                "ain": ain,
                "device_id": device.attrib.get("id", ""),
                "manufacturer": device.attrib.get("manufacturer", ""),
                "productname": device.attrib.get("productname", ""),
                "device_name": _findtext(device, "name") or "",
                "present": _findtext(device, "present") == "1",
                "battery_level": battery_level,
                "battery_low": battery_low,
                "switch": _parse_switch(device),
                "powermeter": _parse_powermeter(device, issues, where),
                "temperature": _parse_temperature(device, issues, where),
                "hkr": hkr,
            }
        )
    return devices


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
