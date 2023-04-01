import logging

from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import (
    FritzActionError,
    FritzConnectionException,
    FritzServiceError,
)

from fritzexporter.exceptions import FritzDeviceHasNoCapabilitiesError
from fritzexporter.fritzcapabilities import FritzCapabilities
from fritzexporter.fritzmetric import FritzDeviceMetrics, FritzMetric

logger = logging.getLogger("fritzexporter.fritzdevice")


class FritzDevice:
    def __init__(
        self, host: str, user: str, password: str, name: str, host_info: bool = False
    ) -> None:
        self.host: str = host
        self.serial: str = "n/a"
        self.model: str = "n/a"
        self.friendly_name: str = name
        self.host_info: bool = host_info

        if len(password) > 32:
            logger.warning(
                "Password is longer than 32 characters! Login may not succeed, please see README!"
            )

        try:
            self.fc: FritzConnection = FritzConnection(address=host, user=user, password=password)
        except FritzConnectionException as e:
            logger.exception(f"unable to connect to {host}: {str(e)}", exc_info=True)
            raise e

        logger.info(f"Connection to {host} successful, reading capabilities")
        self.capabilities = FritzCapabilities(self, host_info)

        self.get_device_info()
        logger.info(
            f"Reading capabilities for {host}, got serial {self.serial}, "
            f"model name {self.model} completed"
        )
        if host_info:
            logger.info(
                f"HostInfo Capability enabled on device {host}. "
                "This will cause slow responses from the exporter. "
                "Ensure prometheus is configured appropriately."
            )
        if self.capabilities.empty():
            logger.critical(f"Device {host} has no detected capabilities. Exiting.")
            raise FritzDeviceHasNoCapabilitiesError

    def get_device_info(self):
        try:
            device_info: dict[str, str] = self.fc.call_action("DeviceInfo1", "GetInfo")
            self.serial: str = device_info["NewSerialNumber"]
            self.model: str = device_info["NewModelName"]

        except (FritzServiceError, FritzActionError):
            logger.error(
                f"Fritz Device {self.host} does not provide basic device "
                "info (Service: DeviceInfo1, Action: GetInfo)."
                "Serial number and model name will be unavailable.",
                exc_info=True,
            )

    def _call_action(self, service: str, action: str, **kwargs) -> dict[str, str]:
        """_summary_: Calls the given action on the given service and returns the result."""

        try:
            return self.fc.call_action(service, action, **kwargs)
        except (FritzServiceError, FritzActionError):
            logger.error(
                f"Fritz Device {self.host} returned error querying Service: {service}, "
                f"Action: {action}. Skipping this metric.",
                exc_info=True,
            )
            return {}

    def collect_metrics(self) -> FritzDeviceMetrics:
        """_summary_: Collects all metrics from the device and returns them as a list of
        FritzMetric objects.
        """

        metrics: FritzDeviceMetrics = FritzDeviceMetrics(
            serial=self.serial, model=self.model, friendly_name=self.friendly_name
        )

        metrics_collectors = [
            method for method in dir(self) if method.startswith("_collect_metrics_")
        ]

        for collector in metrics_collectors:
            metrics.metrics.extend(getattr(self, collector)())

        return metrics

    def _collect_metrics_uptime(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the DeviceInfo1 service and returns them as a list of
        FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action("DeviceInfo1", "GetInfo")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="uptime",
                value=float(response["NewUpTime"]),
                unit="seconds",
            )
        )
        return metrics

    def _collect_metrics_known_devices(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the Hosts1 service and returns them as a list of
        FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action("Hosts1", "GetHostNumberOfEntries")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="known_devices",
                value=float(response["NewHostNumberOfEntries"]),
                unit="hosts",
            )
        )
        return metrics

    def _collect_metrics_update_available(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the UserInterface1 service and returns them as a list of
        FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action("UserInterface1", "GetInfo")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="update_available",
                value=1 if response["NewUpdateAvailable"] else 0,
                unit="updates",
                attributes={
                    "new_software_version": response["NewSoftwareVersion"]
                    if response["NewUpdateAvailable"]
                    else "n/a"
                },
            )
        )
        return metrics

    def _collect_metrics_lan_status(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the LANEthernetInterfaceConfig1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action("LANEthernetInterfaceConfig1", "GetInfo")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="lan_status_enabled",
                value=1 if response["NewEnable"] else 0,
                unit="",
            )
        )
        metrics.append(
            FritzMetric(
                name="lan_status",
                value=1 if response["NewStatus"] == "Up" else 0,
                unit="",
            )
        )
        return metrics

    def _collect_metrics_lan_statistics(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the LANEthernetInterfaceConfig1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action("LANEthernetInterfaceConfig1", "GetStatistics")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="lan_bytes_sent",
                value=float(response["NewBytesSent"]),
                unit="bytes",
                attributes={"direction": "tx"},
            )
        )
        metrics.append(
            FritzMetric(
                name="lan_bytes_received",
                value=float(response["NewBytesReceived"]),
                unit="bytes",
                attributes={"direction": "rx"},
            )
        )
        metrics.append(
            FritzMetric(
                name="lan_packets_sent",
                value=float(response["NewPacketsSent"]),
                unit="total",
                attributes={"direction": "tx"},
            )
        )
        metrics.append(
            FritzMetric(
                name="lan_packets_received",
                value=float(response["NewPacketsReceived"]),
                unit="total",
                attributes={"direction": "rx"},
            )
        )
        return metrics

    def _collect_metrics_wan_dsl_interface_config(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the WANDSLInterfaceConfig1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action("WANDSLInterfaceConfig1", "GetInfo")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="dsl_status_enabled",
                value=float(response["NewEnable"]),
                unit="",
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_datarate",
                value=float(response["NewUpstreamCurrRate"]),
                unit="kbps",
                attributes={"direction": "tx", "type": "curr"},
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_datarate",
                value=float(response["NewDownstreamCurrRate"]),
                unit="kbps",
                attributes={"direction": "rx", "type": "curr"},
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_datarate",
                value=float(response["NewUpstreamMaxRate"]),
                unit="kbps",
                attributes={"direction": "tx", "type": "max"},
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_datarate",
                value=float(response["NewDownstreamMaxRate"]),
                unit="kbps",
                attributes={"direction": "rx", "type": "max"},
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_noise_margin",
                value=float(response["NewUpstreamNoiseMargin"]) / 10,
                unit="dB",
                attributes={"direction": "tx"},
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_noise_margin",
                value=float(response["NewDownstreamNoiseMargin"]) / 10,
                unit="dB",
                attributes={"direction": "rx"},
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_attenuation",
                value=float(response["NewUpstreamAttenuation"]) / 10,
                unit="dB",
                attributes={"direction": "tx"},
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_attenuation",
                value=float(response["NewDownstreamAttenuation"]) / 10,
                unit="dB",
                attributes={"direction": "rx"},
            )
        )

        return metrics

    def _collect_metrics_wan_dsl_interface_config_avm(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the AVM-DE_WANDSLInterfaceConfig1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action(
            "WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo"
        )
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="dsl_fec_errors",
                value=float(response["NewFECErrors"]),
                unit="count",
            )
        )
        metrics.append(
            FritzMetric(
                name="dsl_crc_errors",
                value=float(response["NewCRCErrors"]),
                unit="count",
            )
        )

        return metrics

    def _collect_metrics_wan_ppp_connection_status(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the WANPPPConnection1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action("WANPPPConnection1", "GetInfo")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="ppp_connection_uptime",
                value=float(response["NewUptime"]),
                unit="seconds",
            )
        )
        metrics.append(
            FritzMetric(
                name="ppp_connection_state",
                value=1 if response["NewConnectionStatus"] == "Connected" else 0,
                unit="",
                attributes={"last_error": response["NewLastConnectionError"]},
            )
        )
        return metrics

    def _collect_metrics_wan_common_interface_config(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the WANCommonInterfaceConfig1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action(
            "WANCommonInterfaceConfig1", "GetCommonLinkProperties"
        )
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="wan_max_bitrate",
                value=float(response["NewLayer1DownstreamMaxBitRate"]),
                unit="bps",
                attributes={"direction": "rx", "wantype": response["NewWANAccessType"]},
            )
        )
        metrics.append(
            FritzMetric(
                name="wan_datarate",
                value=float(response["NewLayer1UpstreamMaxBitRate"]),
                unit="kbps",
                attributes={"direction": "tx", "wantype": response["NewWANAccessType"]},
            )
        )

        metrics.append(
            FritzMetric(
                name="wan_phys_link_status",
                value=1 if response["NewPhysicalLinkStatus"] == "Up" else 0,
                unit="",
                attributes={"wantype": response["NewWANAccessType"]},
            )
        )

        return metrics

    def _collect_metrics_wan_common_interface_data_bytes(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the WANCommonInterfaceConfig1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action(
            "WANCommonInterfaceConfig1", "GetTotalBytesSent"
        )
        if response:
            metrics.append(
                FritzMetric(
                    name="wan_data",
                    value=float(response["NewTotalBytesSent"]),
                    unit="bytes",
                    attributes={"direction": "tx"},
                )
            )

        response = self._call_action("WANCommonInterfaceConfig1", "GetTotalBytesReceived")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="wan_data",
                value=float(response["NewTotalBytesReceived"]),
                unit="bytes",
                attributes={"direction": "rx"},
            )
        )

        return metrics

    def _collect_metrics_wan_common_interface_byte_rate(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the WANCommonIFC1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action("WANCommonIFC1", "GetAddonInfos")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="wan_datarate",
                value=float(response["NewByteSendRate"]),
                unit="bytespersecond",
                attributes={"direction": "tx"},
            )
        )
        metrics.append(
            FritzMetric(
                name="wan_datarate",
                value=float(response["NewByteReceiveRate"]),
                unit="bytespersecond",
                attributes={"direction": "rx"},
            )
        )

        return metrics

    def _collect_metrics_wan_common_interface_data_packets(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the WANCommonInterfaceConfig1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        response: dict[str, str] = self._call_action(
            "WANCommonInterfaceConfig1", "GetTotalPacketsSent"
        )
        if response:
            metrics.append(
                FritzMetric(
                    name="wan_data",
                    value=float(response["NewTotalPacketsSent"]),
                    unit="packets",
                    attributes={"direction": "tx"},
                )
            )

        response = self._call_action("WANCommonInterfaceConfig1", "GetTotalPacketsReceived")
        if not response:
            return metrics

        metrics.append(
            FritzMetric(
                name="wan_data",
                value=float(response["NewTotalPacketsReceived"]),
                unit="packets",
                attributes={"direction": "rx"},
            )
        )

        return metrics

    def _collect_metrics_wifi_config(self) -> list[FritzMetric]:
        """_summary_: Checks which WiFi interfaces are present and return a list of
        FritzMetric objects.
        """

        wifi_names = ["2.4GHz", "5GHz", "Guest", "WLAN4"]
        metrics: list[FritzMetric] = []

        for index, wifi_name in enumerate(wifi_names):
            response = self._call_action("WLANConfiguration" + str(index + 1), "GetInfo")
            if not response:
                continue

            wlan_enabled = 1 if response["NewEnable"] else 0
            wlan_standard = response["NewStandard"]
            wlan_ssid = response["NewSSID"]

            metrics.append(
                FritzMetric(
                    name="wifi_status",
                    value=1 if response["NewEnable"] == "1" else 0,
                    unit="",
                    attributes={
                        "wifi_index": str(index + 1),
                        "wifi_name": wifi_name,
                        "standard": wlan_standard,
                        "ssid": wlan_ssid,
                        "enabled": str(wlan_enabled),
                    },
                )
            )
            metrics.append(
                FritzMetric(
                    name="wifi_channel",
                    value=float(response["NewChannel"]),
                    unit="",
                    attributes={
                        "wifi_index": str(index + 1),
                        "wifi_name": wifi_name,
                        "standard": wlan_standard,
                        "ssid": wlan_ssid,
                        "enabled": str(wlan_enabled),
                    },
                )
            )

            response = self._call_action(
                "WLANConfiguration" + str(index + 1), "GetTotalAssociations"
            )
            if response:
                metrics.append(
                    FritzMetric(
                        name="wifi_associations",
                        value=float(response["NewTotalAssociations"]),
                        unit="",
                        attributes={
                            "wifi_index": str(index + 1),
                            "wifi_name": wifi_name,
                            "standard": wlan_standard,
                            "ssid": wlan_ssid,
                            "enabled": str(wlan_enabled),
                        },
                    )
                )

            response = self._call_action(
                "WLANConfiguration" + str(index + 1), "GetPacketStatistics"
            )
            if response:
                metrics.append(
                    FritzMetric(
                        name="wifi_packets",
                        value=float(response["NewTotalPacketsSent"]),
                        unit="total",
                        attributes={
                            "wifi_index": str(index + 1),
                            "wifi_name": wifi_name,
                            "standard": wlan_standard,
                            "ssid": wlan_ssid,
                            "enabled": str(wlan_enabled),
                            "direction": "tx",
                        },
                    )
                )
                metrics.append(
                    FritzMetric(
                        name="wifi_packets",
                        value=float(response["NewTotalPacketsReceived"]),
                        unit="bytes",
                        attributes={
                            "wifi_index": str(index + 1),
                            "wifi_name": wifi_name,
                            "standard": wlan_standard,
                            "ssid": wlan_ssid,
                            "enabled": str(wlan_enabled),
                            "direction": "rx",
                        },
                    )
                )

        return metrics

    def _collect_metrics_host_infos(self) -> list[FritzMetric]:
        """_summary_: Collects metrics from the Hosts1 service and returns
        them as a list of FritzMetric objects.
        """

        metrics: list[FritzMetric] = []

        if not self.host_info:
            return metrics

        response = self._call_action("Hosts1", "GetHostNumberOfEntries")
        if not response:
            return metrics

        host_count = int(response["NewHostNumberOfEntries"])
        for index in range(host_count):
            response = self._call_action("Hosts1", "GetGenericHostEntry", NewIndex=index)
            if not response:
                continue

            host_ip = response["NewIPAddress"]
            host_mac = response["NewMACAddress"]
            host_name = response["NewHostName"]
            host_active = 1.0 if response["NewActive"] else 0.0

            if host_ip != "":
                logger.debug(
                    "Fetching extended AVM host information for "
                    f"host number {index} by IP {host_ip}"
                )
                avm_host_result = self._call_action(
                    "Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP", NewIPAddress=host_ip
                )
                host_interface = avm_host_result["NewInterfaceType"]
                host_port = str(avm_host_result["NewX_AVM-DE_Port"])
                host_model = avm_host_result["NewX_AVM-DE_Model"]
                host_speed = float(avm_host_result["NewX_AVM-DE_Speed"])
            else:
                logger.debug(
                    "Unable to fetch extended AVM host information for host "
                    f"number {index}: no IP found"
                )
                host_interface = "n/a"
                host_port = "n/a"
                host_model = "n/a"
                host_speed = 0

            metrics.append(
                FritzMetric(
                    name="host_active",
                    value=host_active,
                    unit="",
                    attributes={
                        "ip_address": host_ip,
                        "mac_address": host_mac,
                        "hostname": host_name,
                        "interface": host_interface,
                        "port": host_port,
                        "model": host_model,
                    },
                )
            )
            metrics.append(
                FritzMetric(
                    name="host_speed",
                    value=host_speed,
                    unit="",
                    attributes={
                        "ip_address": host_ip,
                        "mac_address": host_mac,
                        "hostname": host_name,
                        "interface": host_interface,
                        "port": host_port,
                        "model": host_model,
                    },
                )
            )

        return metrics


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
