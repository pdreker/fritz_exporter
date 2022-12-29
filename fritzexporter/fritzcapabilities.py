from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional, Type, Union

from fritzconnection.core.exceptions import (
    FritzActionError,
    FritzInternalError,
    FritzServiceError,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

if TYPE_CHECKING:
    from fritzexporter.fritzdevice import FritzDevice

logger = logging.getLogger("fritzexporter.fritzcapability")


class FritzCapability(ABC):
    capabilities: list[FritzCapability] = []
    subclasses: list[Type[FritzCapability]] = []

    def __init__(self) -> None:
        self.present: bool = False
        self.requirements: list[tuple[str, str]] = []
        self.metrics: dict[str, Union[CounterMetricFamily, GaugeMetricFamily]] = {}
        FritzCapability.register()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        logger.debug(f"Capability subclass {cls.__name__} registered")
        FritzCapability.subclasses.append(cls)

    @classmethod
    def register(cls):
        FritzCapability.capabilities.append(cls)

    def checkCapability(self, device: FritzDevice):
        self.present = all(
            [
                (service in device.fc.services) and (action in device.fc.services[service].actions)
                for (service, action) in self.requirements
            ]
        )
        logger.debug(
            f"Capability {type(self).__name__} set to {self.present} on device {device.host}"
        )

        # It seems some boxes report service/actions they don't actually support.
        # So try calling the requirements, and if it throws "InvalidService",
        # "InvalidAction" or "FritzInternalError" disable this again.
        if self.present:
            for (svc, action) in self.requirements:
                try:
                    device.fc.call_action(svc, action)
                except (FritzServiceError, FritzActionError, FritzInternalError) as e:
                    logger.warning(
                        f"disabling metrics at service {svc}, action {action} - "
                        f"fritzconnection.call_action returned {str(e)}"
                    )
                    self.present = False

    def getMetrics(self, devices: list[FritzDevice], name: str):
        for device in devices:
            logger.debug(
                f"Fetching {name} metrics for {device.host}: {device.capabilities[name].present}"
            )
            if device.capabilities[name].present:
                yield from self._getMetricValues(device)

    @abstractmethod
    def createMetrics(self):
        pass

    @abstractmethod
    def _getMetricValues(self, device: FritzDevice):
        pass


class FritzCapabilities:
    def __init__(self, device: Optional[FritzDevice] = None, host_info=False) -> None:
        self.capabilities = {
            subclass.__name__: subclass() for subclass in FritzCapability.subclasses
        }
        if device:
            self.check_present(device)

    def __iter__(self):
        return iter(self.capabilities)

    def __len__(self):
        return len(self.capabilities)

    def __getitem__(self, index):
        return self.capabilities[index]

    def items(self):
        return self.capabilities.items()

    def merge(self, other_caps: FritzCapabilities):
        for cap in self.capabilities:
            self.capabilities[cap].present = (
                self.capabilities[cap].present or other_caps.capabilities[cap].present
            )

    def empty(self):
        return not any([cap.present for cap in list(self.capabilities.values())])

    def check_present(self, device: FritzDevice):
        for c in self.capabilities:
            self.capabilities[c].checkCapability(device)


class DeviceInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("DeviceInfo1", "GetInfo"))

    def createMetrics(self):
        self.metrics["uptime"] = CounterMetricFamily(
            "fritz_uptime",
            "FritzBox uptime, system info in labels",
            labels=["modelname", "softwareversion", "serial", "friendly_name"],
            unit="seconds",
        )

    def _getMetricValues(self, device: FritzDevice):
        info_result = device.fc.call_action("DeviceInfo1", "GetInfo")
        self.metrics["uptime"].add_metric(
            [
                info_result["NewModelName"],
                info_result["NewSoftwareVersion"],
                info_result["NewSerialNumber"],
                device.friendly_name,
            ],
            info_result["NewUpTime"],
        )
        yield self.metrics["uptime"]


class HostNumberOfEntries(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("Hosts1", "GetHostNumberOfEntries"))

    def createMetrics(self):
        self.metrics["numhosts"] = GaugeMetricFamily(
            "fritz_known_devices",
            "Number of devices in hosts table",
            labels=["serial", "friendly_name"],
            unit="count",
        )

    def _getMetricValues(self, device):
        num_hosts_result = device.fc.call_action("Hosts1", "GetHostNumberOfEntries")
        self.metrics["numhosts"].add_metric(
            [device.serial, device.friendly_name],
            num_hosts_result["NewHostNumberOfEntries"],
        )
        yield self.metrics["numhosts"]


class UserInterface(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("UserInterface1", "GetInfo"))

    def createMetrics(self):
        self.metrics["update"] = GaugeMetricFamily(
            "fritz_update_available",
            "FritzBox update available",
            labels=["serial", "friendly_name", "newsoftwareversion"],
        )

    def _getMetricValues(self, device):
        update_result = device.fc.call_action("UserInterface1", "GetInfo")
        upd_available = 1 if update_result["NewUpgradeAvailable"] else 0
        new_software_version = (
            update_result["NewX_AVM-DE_Version"]
            if (update_result["NewUpgradeAvailable"])
            else "n/a"
        )
        self.metrics["update"].add_metric(
            [device.serial, device.friendly_name, new_software_version], upd_available
        )
        yield self.metrics["update"]


class LanInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("LANEthernetInterfaceConfig1", "GetInfo"))

    def createMetrics(self):
        self.metrics["lanenable"] = GaugeMetricFamily(
            "fritz_lan_status_enabled",
            "LAN Interface enabled",
            labels=["serial", "friendly_name"],
        )
        self.metrics["lanstatus"] = GaugeMetricFamily(
            "fritz_lan_status",
            "LAN Interface status",
            labels=["serial", "friendly_name"],
        )

    def _getMetricValues(self, device):
        lanstatus_result = device.fc.call_action("LANEthernetInterfaceConfig1", "GetInfo")
        self.metrics["lanenable"].add_metric(
            [device.serial, device.friendly_name], lanstatus_result["NewEnable"]
        )

        lanstatus = 1 if lanstatus_result["NewStatus"] == "Up" else 0
        self.metrics["lanstatus"].add_metric([device.serial, device.friendly_name], lanstatus)
        yield self.metrics["lanenable"]
        yield self.metrics["lanstatus"]


class LanInterfaceConfigStatistics(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("LANEthernetInterfaceConfig1", "GetStatistics"))

    def createMetrics(self):
        self.metrics["lanbytes"] = CounterMetricFamily(
            "fritz_lan_data",
            "LAN bytes received",
            labels=["serial", "friendly_name", "direction"],
            unit="bytes",
        )
        self.metrics["lanpackets"] = CounterMetricFamily(
            "fritz_lan_packet",
            "LAN packets transmitted",
            labels=["serial", "friendly_name", "direction"],
            unit="count",
        )

    def _getMetricValues(self, device):
        lanstats_result = device.fc.call_action("LANEthernetInterfaceConfig1", "GetStatistics")
        self.metrics["lanbytes"].add_metric(
            [device.serial, device.friendly_name, "rx"],
            lanstats_result["NewBytesReceived"],
        )
        self.metrics["lanbytes"].add_metric(
            [device.serial, device.friendly_name, "tx"], lanstats_result["NewBytesSent"]
        )
        self.metrics["lanpackets"].add_metric(
            [device.serial, device.friendly_name, "rx"],
            lanstats_result["NewPacketsReceived"],
        )
        self.metrics["lanpackets"].add_metric(
            [device.serial, device.friendly_name, "tx"],
            lanstats_result["NewPacketsSent"],
        )
        yield self.metrics["lanbytes"]
        yield self.metrics["lanpackets"]


class WanDSLInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANDSLInterfaceConfig1", "GetInfo"))

    def createMetrics(self):
        self.metrics["enable"] = GaugeMetricFamily(
            "fritz_dsl_status_enabled",
            "DSL enabled",
            labels=["serial", "friendly_name"],
        )
        self.metrics["datarate"] = GaugeMetricFamily(
            "fritz_dsl_datarate",
            "DSL datarate in kbps",
            labels=["serial", "friendly_name", "direction", "type"],
            unit="kbps",
        )
        self.metrics["noisemargin"] = GaugeMetricFamily(
            "fritz_dsl_noise_margin",
            "Noise Margin in dB",
            labels=["serial", "friendly_name", "direction"],
            unit="dB",
        )
        self.metrics["attenuation"] = GaugeMetricFamily(
            "fritz_dsl_attenuation",
            "Line attenuation in dB",
            labels=["serial", "friendly_name", "direction"],
            unit="dB",
        )
        self.metrics["status"] = GaugeMetricFamily(
            "fritz_dsl_status", "DSL status", labels=["serial", "friendly_name"]
        )

    def _getMetricValues(self, device):
        fritz_dslinfo_result = device.fc.call_action("WANDSLInterfaceConfig1", "GetInfo")
        self.metrics["enable"].add_metric(
            [device.serial, device.friendly_name], fritz_dslinfo_result["NewEnable"]
        )

        dslstatus = 1 if fritz_dslinfo_result["NewStatus"] == "Up" else 0
        self.metrics["status"].add_metric([device.serial, device.friendly_name], dslstatus)
        self.metrics["datarate"].add_metric(
            [device.serial, device.friendly_name, "tx", "curr"],
            fritz_dslinfo_result["NewUpstreamCurrRate"],
        )
        self.metrics["datarate"].add_metric(
            [device.serial, device.friendly_name, "rx", "curr"],
            fritz_dslinfo_result["NewDownstreamCurrRate"],
        )
        self.metrics["datarate"].add_metric(
            [device.serial, device.friendly_name, "tx", "max"],
            fritz_dslinfo_result["NewUpstreamMaxRate"],
        )
        self.metrics["datarate"].add_metric(
            [device.serial, device.friendly_name, "rx", "max"],
            fritz_dslinfo_result["NewDownstreamMaxRate"],
        )
        self.metrics["noisemargin"].add_metric(
            [device.serial, device.friendly_name, "tx"],
            fritz_dslinfo_result["NewUpstreamNoiseMargin"] / 10,
        )
        self.metrics["noisemargin"].add_metric(
            [device.serial, device.friendly_name, "rx"],
            fritz_dslinfo_result["NewDownstreamNoiseMargin"] / 10,
        )
        self.metrics["attenuation"].add_metric(
            [device.serial, device.friendly_name, "tx"],
            fritz_dslinfo_result["NewUpstreamAttenuation"] / 10,
        )
        self.metrics["attenuation"].add_metric(
            [device.serial, device.friendly_name, "rx"],
            fritz_dslinfo_result["NewDownstreamAttenuation"] / 10,
        )

        yield self.metrics["enable"]
        yield self.metrics["status"]
        yield self.metrics["datarate"]
        yield self.metrics["noisemargin"]
        yield self.metrics["attenuation"]


class WanDSLInterfaceConfigAVM(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo"))

    def createMetrics(self):
        self.metrics["fec"] = CounterMetricFamily(
            "fritz_dsl_fec_errors_count",
            "Number of Forward Error Correction Errors",
            labels=["serial", "friendly_name"],
        )
        self.metrics["crc"] = CounterMetricFamily(
            "fritz_dsl_crc_errors_count",
            "Number of CRC Errors",
            labels=["serial", "friendly_name"],
        )

    def _getMetricValues(self, device):
        fritz_avm_dsl_result = device.fc.call_action(
            "WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo"
        )
        self.metrics["fec"].add_metric(
            [device.serial, device.friendly_name], fritz_avm_dsl_result["NewFECErrors"]
        )
        self.metrics["crc"].add_metric(
            [device.serial, device.friendly_name], fritz_avm_dsl_result["NewCRCErrors"]
        )

        yield self.metrics["fec"]
        yield self.metrics["crc"]


class WanPPPConnectionStatus(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANPPPConnection1", "GetStatusInfo"))

    def createMetrics(self):
        self.metrics["uptime"] = CounterMetricFamily(
            "fritz_ppp_connection_uptime",
            "PPP connection uptime",
            labels=["serial", "friendly_name"],
            unit="seconds",
        )
        self.metrics["connected"] = GaugeMetricFamily(
            "fritz_ppp_connection_state",
            "PPP connection state",
            labels=["serial", "friendly_name", "last_error"],
        )

    def _getMetricValues(self, device):
        fritz_pppstatus_result = device.fc.call_action("WANPPPConnection1", "GetStatusInfo")
        pppconnected = 1 if fritz_pppstatus_result["NewConnectionStatus"] == "Connected" else 0
        self.metrics["uptime"].add_metric(
            [device.serial, device.friendly_name], fritz_pppstatus_result["NewUptime"]
        )
        self.metrics["connected"].add_metric(
            [
                device.serial,
                device.friendly_name,
                fritz_pppstatus_result["NewLastConnectionError"],
            ],
            pppconnected,
        )
        yield self.metrics["uptime"]
        yield self.metrics["connected"]


class WanCommonInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetCommonLinkProperties"))

    def createMetrics(self):
        self.metrics["wanconfig"] = GaugeMetricFamily(
            "fritz_wan_max_bitrate",
            "max bitrate at the physical layer",
            labels=["serial", "friendly_name", "wantype", "direction"],
            unit="bps",
        )
        self.metrics["wanlinkstatus"] = GaugeMetricFamily(
            "fritz_wan_phys_link_status",
            "link status at the physical layer",
            labels=["serial", "friendly_name", "wantype"],
        )

    def _getMetricValues(self, device):
        wanstatus_result = device.fc.call_action(
            "WANCommonInterfaceConfig1", "GetCommonLinkProperties"
        )
        self.metrics["wanconfig"].add_metric(
            [
                device.serial,
                device.friendly_name,
                wanstatus_result["NewWANAccessType"],
                "tx",
            ],
            wanstatus_result["NewLayer1UpstreamMaxBitRate"],
        )
        self.metrics["wanconfig"].add_metric(
            [
                device.serial,
                device.friendly_name,
                wanstatus_result["NewWANAccessType"],
                "rx",
            ],
            wanstatus_result["NewLayer1DownstreamMaxBitRate"],
        )
        l1_status = wanstatus_result["NewPhysicalLinkStatus"]
        wanstatus = 1 if l1_status == "Up" else 0
        self.metrics["wanlinkstatus"].add_metric(
            [device.serial, device.friendly_name, wanstatus_result["NewWANAccessType"]],
            wanstatus,
        )

        yield self.metrics["wanconfig"]
        yield self.metrics["wanlinkstatus"]


class WanCommonInterfaceDataBytes(FritzCapability):
    WAN_COMMON_INTERFACE_SERVICE: str = "WANCommonInterfaceConfig1"

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalBytesReceived"))
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalBytesSent"))

    def createMetrics(self):
        self.metrics["wanbytes"] = CounterMetricFamily(
            "fritz_wan_data",
            "WAN data in bytes",
            labels=["serial", "friendly_name", "direction"],
            unit="bytes",
        )

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action(
            self.WAN_COMMON_INTERFACE_SERVICE, "GetTotalBytesReceived"
        )
        wan_bytes_rx = fritz_wan_result["NewTotalBytesReceived"]
        fritz_wan_result = device.fc.call_action(
            self.WAN_COMMON_INTERFACE_SERVICE, "GetTotalBytesSent"
        )
        wan_bytes_tx = fritz_wan_result["NewTotalBytesSent"]
        self.metrics["wanbytes"].add_metric(
            [device.serial, device.friendly_name, "tx"], wan_bytes_tx
        )
        self.metrics["wanbytes"].add_metric(
            [device.serial, device.friendly_name, "rx"], wan_bytes_rx
        )
        yield self.metrics["wanbytes"]


class WanCommonInterfaceByteRate(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonIFC1", "GetAddonInfos"))

    def createMetrics(self):
        self.metrics["wanbyterate"] = GaugeMetricFamily(
            "fritz_wan_datarate",
            "Current WAN data rate in bytes/s",
            labels=["serial", "friendly_name", "direction"],
            unit="bytes",
        )

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action("WANCommonIFC1", "GetAddonInfos")
        wan_byterate_rx = fritz_wan_result["NewByteReceiveRate"]
        wan_byterate_tx = fritz_wan_result["NewByteSendRate"]
        self.metrics["wanbyterate"].add_metric(
            [device.serial, device.friendly_name, "rx"], wan_byterate_rx
        )
        self.metrics["wanbyterate"].add_metric(
            [device.serial, device.friendly_name, "tx"], wan_byterate_tx
        )
        yield self.metrics["wanbyterate"]


class WanCommonInterfaceDataPackets(FritzCapability):
    WAN_COMMON_INTERFACE_SERVICE: str = "WANCommonInterfaceConfig1"

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalPacketsReceived"))
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalPacketsSent"))

    def createMetrics(self):
        self.metrics["wanpackets"] = CounterMetricFamily(
            "fritz_wan_data_packets",
            "WAN data in packets",
            labels=["serial", "friendly_name", "direction"],
            unit="count",
        )

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action(
            self.WAN_COMMON_INTERFACE_SERVICE, "GetTotalPacketsReceived"
        )
        wan_packets_rx = fritz_wan_result["NewTotalPacketsReceived"]
        fritz_wan_result = device.fc.call_action(
            self.WAN_COMMON_INTERFACE_SERVICE, "GetTotalPacketsSent"
        )
        wan_packets_tx = fritz_wan_result["NewTotalPacketsSent"]
        self.metrics["wanpackets"].add_metric(
            [device.serial, device.friendly_name, "tx"], wan_packets_tx
        )
        self.metrics["wanpackets"].add_metric(
            [device.serial, device.friendly_name, "rx"], wan_packets_rx
        )
        yield self.metrics["wanpackets"]


class WlanConfigurationInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.wifi_type = ["2.4GHz", "5GHz", "Guest", "WLAN4"]
        self.wifi_present = [False, False, False, False]

    def checkCapability(self, device):
        for wlan in range(len(self.wifi_present)):
            service = f"WLANConfiguration{wlan + 1}"
            requirements = [
                (service, "GetInfo"),
                (service, "GetTotalAssociations"),
                (service, "GetPacketStatistics"),
            ]
            logger.debug(f"Capability {type(self).__name__} checking {service} on {device.host}")
            self.wifi_present[wlan] = all(
                [
                    (service in device.fc.services)
                    and (action in device.fc.services[service].actions)
                    for (service, action) in requirements
                ]
            )
            logger.debug(
                f"Capability {type(self).__name__} in WLAN {wlan+1} set "
                f"to {self.wifi_present[wlan]} on device {device.host}"
            )
            if self.wifi_present[wlan]:
                for (svc, action) in requirements:
                    try:
                        device.fc.call_action(svc, action)
                    except (FritzServiceError, FritzActionError, FritzInternalError) as e:
                        logger.warning(
                            f"disabling metrics at service {svc}, action {action} - "
                            f"fritzconnection.call_action returned {e}"
                        )
                        self.wifi_present[wlan] = False
        self.present = any(self.wifi_present)

    def createMetrics(self):
        self.metrics["wlanstatus"] = GaugeMetricFamily(
            "fritz_wifi_status",
            "Status of WiFi",
            labels=[
                "serial",
                "friendly_name",
                "enabled",
                "standard",
                "ssid",
                "wifi_index",
                "wifi_name",
            ],
        )
        self.metrics["wlanchannel"] = GaugeMetricFamily(
            "fritz_wifi_channel",
            "Channel of WiFi",
            labels=[
                "serial",
                "friendly_name",
                "enabled",
                "standard",
                "ssid",
                "wifi_index",
                "wifi_name",
            ],
        )
        self.metrics["wlanassocs"] = GaugeMetricFamily(
            "fritz_wifi_associations",
            "Number of associations (devices) in WiFi",
            labels=[
                "serial",
                "friendly_name",
                "enabled",
                "standard",
                "ssid",
                "wifi_index",
                "wifi_name",
            ],
            unit="count",
        )
        self.metrics["wlanpackets"] = CounterMetricFamily(
            "fritz_wifi_packets",
            "Amount of packets in WiFi",
            labels=[
                "serial",
                "friendly_name",
                "enabled",
                "standard",
                "ssid",
                "direction",
                "wifi_index",
                "wifi_name",
            ],
            unit="count",
        )

    def _getMetricValues(self, device):
        logger.debug(
            "WLANConfigurationInfo._getMetricValues called: "
            f"{device.host} - {self.__class__.__name__}"
        )
        for index, wlan in enumerate(device.capabilities[self.__class__.__name__].wifi_present):
            logger.debug(
                "WLANConfigurationInfo._getMetricValues checking WLAN "
                f"{index} (enabled: {wlan}) on {device.host}"
            )
            if wlan:
                logger.debug(
                    f"WLANCapability._getMetrics fetching metrics for {device.host}: {index}"
                )
                wlan_result = device.fc.call_action(f"WLANConfiguration{index+1}", "GetInfo")
                wlan_status = 1 if wlan_result["NewStatus"] == "Up" else 0
                wlan_enabled = "1" if wlan_result["NewEnable"] else "0"
                self.metrics["wlanstatus"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        wlan_enabled,
                        wlan_result["NewStandard"],
                        wlan_result["NewSSID"],
                        str(index + 1),
                        self.wifi_type[index],
                    ],
                    wlan_status,
                )
                self.metrics["wlanchannel"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        wlan_enabled,
                        wlan_result["NewStandard"],
                        wlan_result["NewSSID"],
                        str(index + 1),
                        self.wifi_type[index],
                    ],
                    wlan_result["NewChannel"],
                )

                assoc_results = device.fc.call_action(
                    f"WLANConfiguration{index+1}", "GetTotalAssociations"
                )
                self.metrics["wlanassocs"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        wlan_enabled,
                        wlan_result["NewStandard"],
                        wlan_result["NewSSID"],
                        str(index + 1),
                        self.wifi_type[index],
                    ],
                    assoc_results["NewTotalAssociations"],
                )

                packet_stats_result = device.fc.call_action(
                    f"WLANConfiguration{index+1}", "GetPacketStatistics"
                )
                self.metrics["wlanpackets"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        wlan_enabled,
                        wlan_result["NewStandard"],
                        wlan_result["NewSSID"],
                        "rx",
                        str(index + 1),
                        self.wifi_type[index],
                    ],
                    packet_stats_result["NewTotalPacketsReceived"],
                )
                self.metrics["wlanpackets"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        wlan_enabled,
                        wlan_result["NewStandard"],
                        wlan_result["NewSSID"],
                        "tx",
                        str(index + 1),
                        self.wifi_type[index],
                    ],
                    packet_stats_result["NewTotalPacketsSent"],
                )

        yield self.metrics["wlanstatus"]
        yield self.metrics["wlanchannel"]
        yield self.metrics["wlanassocs"]
        yield self.metrics["wlanpackets"]


class HostInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("Hosts1", "GetHostNumberOfEntries"))
        self.requirements.append(("Hosts1", "GetGenericHostEntry"))
        self.requirements.append(("Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP"))

    def checkCapability(self, device):
        self.present = device.host_info and all(
            [
                (service in device.fc.services) and (action in device.fc.services[service].actions)
                for (service, action) in self.requirements
            ]
        )
        logger.debug(
            f"Capability {type(self).__name__} set to {self.present} on device {device.host}"
        )

        # It seems some boxes report service/actions they don't actually support.
        # So try calling the requirements, and if it throws "InvalidService",
        # "InvalidAction" or "FritzInternalError" disable this again.
        if self.present:
            for (svc, action) in self.requirements:
                try:
                    if action == "GetHostNumberOfEntries":
                        device.fc.call_action(svc, action)
                    elif action == "GetGenericHostEntry":
                        device.fc.call_action(svc, action, arguments={"NewIndex": 1})
                except (FritzServiceError, FritzActionError, FritzInternalError) as e:
                    logger.warning(
                        f"disabling metrics at service {svc}, action {action} - "
                        f"fritzconnection.call_action returned {str(e)}"
                    )
                    self.present = False

    def createMetrics(self):
        self.metrics["hostactive"] = GaugeMetricFamily(
            "fritz_host_active",
            "Indicates that the device is curently active",
            labels=[
                "serial",
                "ip_address",
                "mac_address",
                "hostname",
                "interface",
                "port",
                "model",
            ],
        )
        self.metrics["hostspeed"] = GaugeMetricFamily(
            "fritz_host_speed",
            "Connection speed of the device",
            labels=[
                "serial",
                "ip_address",
                "mac_address",
                "hostname",
                "interface",
                "port",
                "model",
            ],
        )

    def _getMetricValues(self, device):
        num_hosts_result = device.fc.call_action("Hosts1", "GetHostNumberOfEntries")
        logger.debug(
            f"Fetching host information for device serial {device.serial} "
            f'(hosts found: {num_hosts_result["NewHostNumberOfEntries"]}'
        )
        for host_index in range(num_hosts_result["NewHostNumberOfEntries"]):
            logger.debug(f"Fetching generic host information for host number {host_index}")
            host_result = device.fc.call_action(
                "Hosts1", "GetGenericHostEntry", NewIndex=host_index
            )
            host_ip = host_result["NewIPAddress"]
            host_mac = host_result["NewMACAddress"]
            host_name = host_result["NewHostName"]
            if host_ip != "":
                logger.debug(
                    "Fetching extended AVM host information for "
                    f"host number {host_index} by IP {host_ip}"
                )
                avm_host_result = device.fc.call_action(
                    "Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP", NewIPAddress=host_ip
                )
                host_interface = avm_host_result["NewInterfaceType"]
                host_port = str(avm_host_result["NewX_AVM-DE_Port"])
                host_model = avm_host_result["NewX_AVM-DE_Model"]
                host_speed = avm_host_result["NewX_AVM-DE_Speed"]
            else:
                logger.debug(
                    "Unable to fetch extended AVM host information for host "
                    f"number {host_index}: no IP found"
                )
                host_interface = "n/a"
                host_port = "n/a"
                host_model = "n/a"
                host_speed = 0
            host_active = 1.0 if host_result["NewActive"] else 0.0
            self.metrics["hostactive"].add_metric(
                [
                    device.serial,
                    host_ip,
                    host_mac,
                    host_name,
                    host_interface,
                    host_port,
                    host_model,
                ],
                host_active,
            )
            self.metrics["hostspeed"].add_metric(
                [
                    device.serial,
                    host_ip,
                    host_mac,
                    host_name,
                    host_interface,
                    host_port,
                    host_model,
                ],
                host_speed,
            )
        yield self.metrics["hostactive"]
        yield self.metrics["hostspeed"]


# Copyright 2019-2022 Patrick Dreker <patrick@dreker.de>
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
