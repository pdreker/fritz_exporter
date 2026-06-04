from xml.etree.ElementTree import Element

from defusedxml import ElementTree


def parse_aha_device_xml(deviceinfo: str) -> dict[str, str]:
    try:
        device: Element = ElementTree.fromstring(deviceinfo)

        battery_level = device.find("battery")
        battery_low = device.find("batterylow")

        result = {}

        if battery_level is not None:
            result["battery_level"] = battery_level.text or ""

        if battery_low is not None:
            result["battery_low"] = battery_low.text or ""

    except ElementTree.ParseError:
        return {}
    else:
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
