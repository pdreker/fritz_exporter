from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree


def _findtext(elem: Element, tag: str) -> str | None:
    text = elem.findtext(tag)
    return text.strip() if text is not None else None


def _find_int(elem: Element, tag: str) -> int | None:
    text = _findtext(elem, tag)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_switch(device: Element) -> dict[str, Any] | None:
    switch = device.find("switch")
    if switch is None:
        return None
    return {
        "state": _findtext(switch, "state"),
        "mode": _findtext(switch, "mode"),
        "lock": _findtext(switch, "lock"),
    }


def _parse_powermeter(device: Element) -> dict[str, Any] | None:
    powermeter = device.find("powermeter")
    if powermeter is None:
        return None
    return {
        "power": _find_int(powermeter, "power"),
        "energy": _find_int(powermeter, "energy"),
    }


def _parse_temperature(device: Element) -> dict[str, Any] | None:
    temperature = device.find("temperature")
    if temperature is None:
        return None
    return {
        "celsius": _find_int(temperature, "celsius"),
        "offset": _find_int(temperature, "offset"),
    }


def _parse_hkr(device: Element) -> dict[str, Any] | None:
    hkr = device.find("hkr")
    if hkr is None:
        return None
    return {
        "tist": _find_int(hkr, "tist"),
        "tsoll": _find_int(hkr, "tsoll"),
        "absenk": _find_int(hkr, "absenk"),
        "komfort": _find_int(hkr, "komfort"),
        "battery_level": _find_int(hkr, "battery"),
        "battery_low": _findtext(hkr, "batterylow"),
    }


def parse_aha_devicelist_xml(content: str) -> list[dict[str, Any]]:
    try:
        devicelist: Element = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return []

    devices = []
    for device in devicelist.findall("device"):
        hkr = _parse_hkr(device)
        # On some firmware versions battery data is only reported nested
        # inside <hkr> instead of as a direct child of <device>.
        battery_level = _find_int(device, "battery")
        battery_low = _findtext(device, "batterylow")
        if battery_level is None and hkr is not None:
            battery_level = hkr["battery_level"]
            battery_low = hkr["battery_low"]

        devices.append(
            {
                "ain": device.attrib.get("identifier", ""),
                "device_id": device.attrib.get("id", ""),
                "manufacturer": device.attrib.get("manufacturer", ""),
                "productname": device.attrib.get("productname", ""),
                "device_name": _findtext(device, "name") or "",
                "present": _findtext(device, "present") == "1",
                "battery_level": battery_level,
                "battery_low": battery_low,
                "switch": _parse_switch(device),
                "powermeter": _parse_powermeter(device),
                "temperature": _parse_temperature(device),
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
