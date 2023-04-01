import logging
import sys

from fritzexporter.fritzcapabilities import FritzCapabilities
from fritzexporter.fritzdevice import FritzDevice

logger = logging.getLogger("fritzexporter.fritzdevice")


class FritzCollector:
    def __init__(self):
        self.devices: list[FritzDevice] = []
        self.capabilities: FritzCapabilities = FritzCapabilities(host_info=True)

    def register(self, fritzdev):
        self.devices.append(fritzdev)
        logger.debug(f"registered device {fritzdev.host} ({fritzdev.model}) to collector")
        self.capabilities.merge(fritzdev.capabilities)

    def collect(self):
        if not self.devices:
            logger.critical("No devices registered in collector! Exiting.")
            sys.exit(1)

        for name, capa in self.capabilities.items():
            capa.createMetrics()
            yield from capa.getMetrics(self.devices, name)


# Copyright 2019-2023 Patrick Dreker <patrick@dreker.de>
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
