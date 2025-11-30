import collections
import logging
import sys
import threading
from typing import NamedTuple

from fritzconnection import FritzConnection  # type: ignore[import]
from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzAuthorizationError,
    FritzConnectionException,
    FritzServiceError,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

from fritzexporter.exceptions import FritzDeviceHasNoCapabilitiesError
from fritzexporter.fritzcapabilities import FritzCapabilities

logger = logging.getLogger("fritzexporter.fritzdevice")


FRITZ_MAX_PASSWORD_LENGTH = 32


class FritzCredentials(NamedTuple):
    host: str
    user: str
    password: str


class FritzDevice:
    def __init__(self, creds: FritzCredentials, name: str, *, host_info: bool = False) -> None:
        self.host: str = creds.host
        self.serial: str = "n/a"
        self.model: str = "n/a"
        self.friendly_name: str = name
        self.host_info: bool = host_info

        if len(creds.password) > FRITZ_MAX_PASSWORD_LENGTH:
            logger.warning(
                "Password is longer than %d characters! Login may not succeed, please see README!",
                FRITZ_MAX_PASSWORD_LENGTH,
            )

        try:
            self.fc: FritzConnection = FritzConnection(
                address=creds.host, user=creds.user, password=creds.password
            )
        except FritzConnectionException:
            logger.exception("unable to connect to %s.", creds.host)
            raise

        self.get_device_info()

        logger.info("Connection to %s successful, reading capabilities", creds.host)
        self.capabilities = FritzCapabilities(self)

        logger.info(
            "Reading capabilities for %s, got serial %s, model name %s completed",
            creds.host,
            self.serial,
            self.model,
        )
        if host_info:
            logger.info(
                "HostInfo Capability enabled on device %s. "
                "This will cause slow responses from the exporter. "
                "Ensure prometheus is configured appropriately.",
                creds.host,
            )
        if self.capabilities.empty():
            logger.critical("Device %s has no detected capabilities. Exiting.", creds.host)
            raise FritzDeviceHasNoCapabilitiesError

    def get_device_info(self) -> None:
        try:
            device_info: dict[str, str] = self.fc.call_action("DeviceInfo1", "GetInfo")
            self.serial = device_info["NewSerialNumber"]
            self.model = device_info["NewModelName"]

        except (FritzServiceError, FritzActionError):
            logger.exception(
                "Fritz Device %s does not provide basic device "
                "info (Service: DeviceInfo1, Action: GetInfo)."
                "Serial number and model name will be unavailable.",
                self.host,
            )
        except FritzAuthorizationError:
            logger.exception(
                "Not authorized to get device info from %s. Check username/password.", self.host
            )
            raise

    def get_connection_mode(self) -> GaugeMetricFamily | None:
        """
        Returns a metric to detect whether device is in DSL, mobile fallback or offline mode.
        """
        try:
            resp = self.fc.call_action("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            link_status = resp.get("NewPhysicalLinkStatus")
            access_type = resp.get("NewWANAccessType")
        except FritzConnectionException:
            logger.warning("Failed to retrieve connection mode info from %s", self.host)
            return None

        if link_status == "Up" and access_type == "DSL":
            mode = 1  # DSL connection active
        elif link_status == "Down" and access_type == "X_AVM-DE_Mobile":
            mode = 2  # DSL disconnected -> Fallback mobile conenction active
        elif link_status == "Up" and access_type == "X_AVM-DE_Mobile":
            mode = 3  # DSL disabled, only mobile connection active
        else:
            mode = 0  # Disconnected or not available

        m = GaugeMetricFamily(
            "fritz_connection_mode",
            "Connection mode: 1=DSL, 2=Mobile fallback, 3=Mobile-only, 0=offline/unknown",
            labels=["access_type", "friendly_name"],
        )
        m.add_metric([access_type, self.friendly_name], mode)
        return m


class FritzCollector(Collector):
    def __init__(self) -> None:
        self.devices: list[FritzDevice] = []
        self.capabilities: FritzCapabilities = FritzCapabilities()  # host_info=True??? FIXME
        self._collect_lock = threading.RLock()

    def register(self, fritzdev: FritzDevice) -> None:
        self.devices.append(fritzdev)
        logger.debug("registered device %s (%s) to collector", fritzdev.host, fritzdev.model)
        self.capabilities.merge(fritzdev.capabilities)

    def collect(self) -> collections.abc.Iterable[CounterMetricFamily | GaugeMetricFamily]:
        with self._collect_lock:
            if not self.devices:
                logger.critical("No devices registered in collector! Exiting.")
                sys.exit(1)

            # Custom mode metric
            for dev in self.devices:
                mode_metric = dev.get_connection_mode()
                if mode_metric:
                    yield mode_metric

            for name, capa in self.capabilities.items():
                capa.create_metrics()
                yield from capa.get_metrics(self.devices, name)


# Copyright 2019-2025 Patrick Dreker <patrick@dreker.de>
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
