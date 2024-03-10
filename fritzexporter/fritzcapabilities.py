from __future__ import annotations

import collections
import logging
from abc import ABC, abstractmethod
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, ClassVar

from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzArgumentError,
    FritzArrayIndexError,
    FritzHttpInterfaceError,
    FritzInternalError,
    FritzServiceError,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from fritzexporter.fritz_aha import parse_aha_device_xml

if TYPE_CHECKING:
    from fritzexporter.fritzdevice import FritzDevice

logger = logging.getLogger("fritzexporter.fritzcapability")


class FritzCapability(ABC):
    capabilities: ClassVar[list[type[FritzCapability]]] = []
    subclasses: ClassVar[list[type[FritzCapability]]] = []

    def __init__(self) -> None:
        self.present: bool = False
        self.requirements: list[tuple[str, str]] = []
        self.metrics: dict[str, CounterMetricFamily | GaugeMetricFamily] = {}
        FritzCapability.register()

    def __init_subclass__(cls, **kwargs: dict[str, Any]) -> None:
        super().__init_subclass__(**kwargs)
        logger.debug("Capability subclass %s registered", cls.__name__)
        FritzCapability.subclasses.append(cls)

    @classmethod
    def register(cls) -> None:
        FritzCapability.capabilities.append(cls)

    def check_capability(self, device: FritzDevice) -> None:
        self.present = all(
            (service in device.fc.services) and (action in device.fc.services[service].actions)
            for (service, action) in self.requirements
        )
        logger.debug(
            "Capability %s set to %s on device %s", type(self).__name__, self.present, device.host
        )

        # It seems some boxes report service/actions they don't actually support.
        # So try calling the requirements, and if it throws "InvalidService",
        # "InvalidAction" or "FritzInternalError" disable this again.
        if self.present:
            for svc, action in self.requirements:
                try:
                    device.fc.call_action(svc, action)
                except (
                    FritzServiceError,
                    FritzActionError,
                    FritzInternalError,
                    FritzArgumentError,
                ) as e:
                    logger.warning(
                        "disabling metrics at service %s, action %s - fritzconnection.call_action "
                        "returned %s",
                        svc,
                        action,
                        str(e),
                    )
                    self.present = False

    def get_metrics(
        self, devices: list[FritzDevice], name: str
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        for device in devices:
            logger.debug(
                "Fetching %s metrics for %s: %s",
                name,
                device.host,
                device.capabilities[name].present,
            )
            if device.capabilities[name].present:
                self._generate_metric_values(device)
        yield from self._get_metric_values()

    @abstractmethod
    def create_metrics(self) -> None:
        pass

    @abstractmethod
    def _generate_metric_values(self, device: FritzDevice) -> None:
        pass

    @abstractmethod
    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        pass


class FritzCapabilities:
    def __init__(self, device: FritzDevice | None = None) -> None:
        self.capabilities: dict[str, FritzCapability] = {
            subclass.__name__: subclass() for subclass in FritzCapability.subclasses
        }
        if device:
            self.check_present(device)

    def __iter__(self) -> collections.abc.Iterator[str]:
        return iter(self.capabilities)

    def __len__(self) -> int:
        return len(self.capabilities)

    def __getitem__(self, index: str) -> FritzCapability:
        return self.capabilities[index]

    def items(self) -> collections.abc.ItemsView[str, FritzCapability]:
        return self.capabilities.items()

    def merge(self, other_caps: FritzCapabilities) -> None:
        for cap in self.capabilities:
            self.capabilities[cap].present = (
                self.capabilities[cap].present or other_caps.capabilities[cap].present
            )

    def empty(self) -> bool:
        return not any(cap.present for cap in list(self.capabilities.values()))

    def check_present(self, device: FritzDevice) -> None:
        for c in self.capabilities:
            self.capabilities[c].check_capability(device)


class DeviceInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("DeviceInfo1", "GetInfo"))

    def create_metrics(self) -> None:
        self.metrics["uptime"] = CounterMetricFamily(
            "fritz_uptime",
            "FritzBox uptime, system info in labels",
            labels=["modelname", "softwareversion", "serial", "friendly_name"],
            unit="seconds",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
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

    def _get_metric_values(self) -> Generator[CounterMetricFamily | GaugeMetricFamily, None, None]:
        yield self.metrics["uptime"]


class HostNumberOfEntries(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("Hosts1", "GetHostNumberOfEntries"))

    def create_metrics(self) -> None:
        self.metrics["numhosts"] = GaugeMetricFamily(
            "fritz_known_devices",
            "Number of devices in hosts table",
            labels=["serial", "friendly_name"],
            unit="count",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        num_hosts_result = device.fc.call_action("Hosts1", "GetHostNumberOfEntries")
        self.metrics["numhosts"].add_metric(
            [device.serial, device.friendly_name],
            num_hosts_result["NewHostNumberOfEntries"],
        )

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["numhosts"]


class UserInterface(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("UserInterface1", "GetInfo"))

    def create_metrics(self) -> None:
        self.metrics["update"] = GaugeMetricFamily(
            "fritz_update_available",
            "FritzBox update available",
            labels=["serial", "friendly_name", "newsoftwareversion"],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
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

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["update"]


class LanInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("LANEthernetInterfaceConfig1", "GetInfo"))

    def create_metrics(self) -> None:
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

    def _generate_metric_values(self, device: FritzDevice) -> None:
        lanstatus_result = device.fc.call_action("LANEthernetInterfaceConfig1", "GetInfo")
        self.metrics["lanenable"].add_metric(
            [device.serial, device.friendly_name], lanstatus_result["NewEnable"]
        )

        lanstatus = 1 if lanstatus_result["NewStatus"] == "Up" else 0
        self.metrics["lanstatus"].add_metric([device.serial, device.friendly_name], lanstatus)

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["lanenable"]
        yield self.metrics["lanstatus"]


class LanInterfaceConfigStatistics(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("LANEthernetInterfaceConfig1", "GetStatistics"))

    def create_metrics(self) -> None:
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

    def _generate_metric_values(self, device: FritzDevice) -> None:
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

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["lanbytes"]
        yield self.metrics["lanpackets"]


class WanDSLInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANDSLInterfaceConfig1", "GetInfo"))

    def create_metrics(self) -> None:
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

    def _generate_metric_values(self, device: FritzDevice) -> None:
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

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["enable"]
        yield self.metrics["status"]
        yield self.metrics["datarate"]
        yield self.metrics["noisemargin"]
        yield self.metrics["attenuation"]


class WanDSLInterfaceConfigAVM(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo"))

    def create_metrics(self) -> None:
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

    def _generate_metric_values(self, device: FritzDevice) -> None:
        fritz_avm_dsl_result = device.fc.call_action(
            "WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo"
        )
        self.metrics["fec"].add_metric(
            [device.serial, device.friendly_name], fritz_avm_dsl_result["NewFECErrors"]
        )
        self.metrics["crc"].add_metric(
            [device.serial, device.friendly_name], fritz_avm_dsl_result["NewCRCErrors"]
        )

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["fec"]
        yield self.metrics["crc"]


class WanPPPConnectionStatus(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANPPPConnection1", "GetStatusInfo"))

    def create_metrics(self) -> None:
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

    def _generate_metric_values(self, device: FritzDevice) -> None:
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

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["uptime"]
        yield self.metrics["connected"]


class WanCommonInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetCommonLinkProperties"))

    def create_metrics(self) -> None:
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

    def _generate_metric_values(self, device: FritzDevice) -> None:
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

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["wanconfig"]
        yield self.metrics["wanlinkstatus"]


class WanCommonInterfaceDataBytes(FritzCapability):
    WAN_COMMON_INTERFACE_SERVICE: str = "WANCommonInterfaceConfig1"

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalBytesReceived"))
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalBytesSent"))

    def create_metrics(self) -> None:
        self.metrics["wanbytes"] = CounterMetricFamily(
            "fritz_wan_data",
            "WAN data in bytes",
            labels=["serial", "friendly_name", "direction"],
            unit="bytes",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
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

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["wanbytes"]


class WanCommonInterfaceByteRate(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonIFC1", "GetAddonInfos"))

    def create_metrics(self) -> None:
        self.metrics["wanbyterate"] = GaugeMetricFamily(
            "fritz_wan_datarate",
            "Current WAN data rate in bytes/s",
            labels=["serial", "friendly_name", "direction"],
            unit="bytes",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        fritz_wan_result = device.fc.call_action("WANCommonIFC1", "GetAddonInfos")
        wan_byterate_rx = fritz_wan_result["NewByteReceiveRate"]
        wan_byterate_tx = fritz_wan_result["NewByteSendRate"]
        self.metrics["wanbyterate"].add_metric(
            [device.serial, device.friendly_name, "rx"], wan_byterate_rx
        )
        self.metrics["wanbyterate"].add_metric(
            [device.serial, device.friendly_name, "tx"], wan_byterate_tx
        )

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["wanbyterate"]


class WanCommonInterfaceDataPackets(FritzCapability):
    WAN_COMMON_INTERFACE_SERVICE: str = "WANCommonInterfaceConfig1"

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalPacketsReceived"))
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalPacketsSent"))

    def create_metrics(self) -> None:
        self.metrics["wanpackets"] = CounterMetricFamily(
            "fritz_wan_data_packets",
            "WAN data in packets",
            labels=["serial", "friendly_name", "direction"],
            unit="count",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
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

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["wanpackets"]


class WlanConfigurationInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.wifi_type = ["2.4GHz", "5GHz", "Guest", "WLAN4"]
        self.wifi_present = [False, False, False, False]

    def check_capability(self, device: FritzDevice) -> None:
        for wlan in range(len(self.wifi_present)):
            service = f"WLANConfiguration{wlan + 1}"
            requirements = [
                (service, "GetInfo"),
                (service, "GetTotalAssociations"),
                (service, "GetPacketStatistics"),
            ]
            logger.debug(
                "Capability %s checking %s on %s", type(self).__name__, service, device.host
            )
            self.wifi_present[wlan] = all(
                (service in device.fc.services) and (action in device.fc.services[service].actions)
                for (service, action) in requirements
            )
            logger.debug(
                "Capability %s in WLAN %d set to %s on device %s",
                type(self).__name__,
                wlan + 1,
                self.wifi_present[wlan],
                device.host,
            )
            if self.wifi_present[wlan]:
                for svc, action in requirements:
                    try:
                        device.fc.call_action(svc, action)
                    except (
                        FritzServiceError,
                        FritzActionError,
                        FritzInternalError,
                    ) as e:
                        logger.warning(
                            "disabling metrics at service %s, action %s - "
                            "fritzconnection.call_action returned %s",
                            svc,
                            action,
                            str(e),
                        )
                        self.wifi_present[wlan] = False
        self.present = any(self.wifi_present)

    def create_metrics(self) -> None:
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

    def _generate_metric_values(self, device: FritzDevice) -> None:
        logger.debug(
            "WLANConfigurationInfo._generateMetricValues called: %s - %s",
            device.host,
            self.__class__.__name__,
        )
        for index, wlan in enumerate(device.capabilities[self.__class__.__name__].wifi_present):
            logger.debug(
                "WLANConfigurationInfo._generateMetricValues checking WLAN "
                "%s (enabled: %s) on %s",
                index,
                wlan,
                device.host,
            )
            if wlan:
                logger.debug(
                    "WLANCapability._generateMetricValues fetching metrics for %s: %s",
                    device.host,
                    index,
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

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
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

    def check_capability(self, device: FritzDevice) -> None:
        self.present = device.host_info and all(
            (service in device.fc.services) and (action in device.fc.services[service].actions)
            for (service, action) in self.requirements
        )
        logger.debug(
            "Capability %s set to %s on device %s", type(self).__name__, self.present, device.host
        )

        # It seems some boxes report service/actions they don't actually support.
        # So try calling the requirements, and if it throws "InvalidService",
        # "InvalidAction" or "FritzInternalError" disable this again.
        if self.present:
            for svc, action in self.requirements:
                try:
                    if action == "GetHostNumberOfEntries":
                        device.fc.call_action(svc, action)
                    elif action == "GetGenericHostEntry":
                        device.fc.call_action(svc, action, arguments={"NewIndex": 1})
                except (FritzServiceError, FritzActionError, FritzInternalError) as e:
                    logger.warning(
                        "disabling metrics at service %s, action %s - "
                        "fritzconnection.call_action returned %s}",
                        svc,
                        action,
                        str(e),
                    )
                    self.present = False

    def create_metrics(self) -> None:
        self.metrics["hostactive"] = GaugeMetricFamily(
            "fritz_host_active",
            "Indicates that the device is curently active",
            labels=[
                "serial",
                "friendly_name",
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
                "friendly_name",
                "ip_address",
                "mac_address",
                "hostname",
                "interface",
                "port",
                "model",
            ],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        num_hosts_result = device.fc.call_action("Hosts1", "GetHostNumberOfEntries")
        logger.debug(
            "Fetching host information for device serial %s (hosts found: %s",
            device.serial,
            num_hosts_result["NewHostNumberOfEntries"],
        )
        for host_index in range(num_hosts_result["NewHostNumberOfEntries"]):
            logger.debug("Fetching generic host information for host number %s", host_index)
            host_result = device.fc.call_action(
                "Hosts1", "GetGenericHostEntry", NewIndex=host_index
            )
            host_ip = host_result["NewIPAddress"]
            host_mac = host_result["NewMACAddress"]
            host_name = host_result["NewHostName"]
            if host_ip != "":
                logger.debug(
                    "Fetching extended AVM host information for host number %s by IP %s",
                    host_index,
                    host_ip,
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
                    "number %s: no IP found",
                    host_index,
                )
                host_interface = "n/a"
                host_port = "n/a"
                host_model = "n/a"
                host_speed = 0
            host_active = 1.0 if host_result["NewActive"] else 0.0
            self.metrics["hostactive"].add_metric(
                [
                    device.serial,
                    device.friendly_name,
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
                    device.friendly_name,
                    host_ip,
                    host_mac,
                    host_name,
                    host_interface,
                    host_port,
                    host_model,
                ],
                host_speed,
            )

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["hostactive"]
        yield self.metrics["hostspeed"]


class HomeAutomation(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_AVM-DE_Homeauto1", "GetInfo"))

    def create_metrics(self) -> None:
        self.metrics["devicepresent"] = GaugeMetricFamily(
            "fritz_ha_device_present",
            "Indicates that the device is present",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )

        self.metrics["battery_level"] = GaugeMetricFamily(
            "fritz_ha_battery_level_percent",
            "Battery level in percent",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )

        self.metrics["battery_low"] = GaugeMetricFamily(
            "fritz_ha_battery_low",
            "Indicates that the battery is low",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )

        self.metrics["multimeter_power"] = GaugeMetricFamily(
            "fritz_ha_multimeter_power_W",
            "Power in W",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["multimeter_energy"] = GaugeMetricFamily(
            "fritz_ha_multimeter_energy_Wh",
            "Energy in Wh",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["temperature"] = GaugeMetricFamily(
            "fritz_ha_temperature_C",
            "Temperature in °C",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["temperature_offset"] = GaugeMetricFamily(
            "fritz_ha_temperature_offset_C",
            "Temperature offset in °C",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["switch_state"] = GaugeMetricFamily(
            "fritz_ha_switch_state",
            "Switch state",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["switch_mode"] = GaugeMetricFamily(
            "fritz_ha_switch_mode",
            "Switch mode",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["switch_lock"] = GaugeMetricFamily(
            "fritz_ha_switch_lock",
            "Switch lock",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["heater_temperature"] = GaugeMetricFamily(
            "fritz_ha_heater_temperature_C",
            "Heater temperature in °C",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["heater_set_temperature"] = GaugeMetricFamily(
            "fritz_ha_heater_set_temperature_C",
            "Heater set temperature in °C",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["heater_valve_set_state"] = GaugeMetricFamily(
            "fritz_ha_heater_valve_set_state",
            "Heater valve set state",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["heater_reduced_temperature"] = GaugeMetricFamily(
            "fritz_ha_heater_reduced_temperature_C",
            "Heater reduced temperature in °C",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["heater_comfort_temperature"] = GaugeMetricFamily(
            "fritz_ha_heater_comfort_temperature_C",
            "Heater comfort temperature in °C",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["heater_reduced_valve_state"] = GaugeMetricFamily(
            "fritz_ha_heater_reduced_valve_state",
            "Heater reduced valve state",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )
        self.metrics["heater_comfort_valve_state"] = GaugeMetricFamily(
            "fritz_ha_heater_comfort_valve_state",
            "Heater comfort valve state",
            labels=[
                "serial",
                "friendly_name",
                "ain",
                "device_name",
                "device_id",
                "manufacturer",
                "productname",
            ],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:  # noqa: C901
        # There is no way to get a list or the number of home automation devices, so we just try
        # do a while loop until we get an error
        index = 0

        device_present_map = {
            "DISCONNECTED": 0,
            "REGISTERED": 1,
            "CONNECTED": 2,
            "UNKNOWN": 3,
        }

        switch_mode_map = {"MANUAL": 0, "AUTO": 1, "UNDEFINED": 2}
        switch_state_map = {"OFF": 0, "ON": 1, "TOGGLE": 2, "UNDEFINED": 3}
        hkr_valve_map = {"CLOSED": 0, "OPEN": 1, "TEMP": 2}

        while True:
            logger.debug("Fetching home automation device information for index %d", index)
            try:
                ha_result = device.fc.call_action(
                    "X_AVM-DE_Homeauto1", "GetGenericDeviceInfos", NewIndex=index
                )
            except FritzArrayIndexError:
                logger.debug("Got IndexError for index %d, stopping", index)
                break

            ain = ha_result["NewAIN"]
            device_name = ha_result["NewDeviceName"]
            manufacturer = ha_result["NewManufacturer"]
            productname = ha_result["NewProductName"]

            self.metrics["devicepresent"].add_metric(
                [device.serial, device.friendly_name, ain, device_name, manufacturer, productname],
                device_present_map[ha_result["NewPresent"]],
            )

            if (
                ha_result["NewMultimeterIsEnabled"] == "ENABLED"
                and ha_result["NewMultimeterIsValid"] == "VALID"
            ):
                self.metrics["multimeter_power"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    ha_result["NewMultimeterPower"] / 100.0,
                )
                self.metrics["multimeter_energy"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    ha_result["NewMultimeterEnergy"],
                )

            if (
                ha_result["NewTemperatureIsEnabled"] == "ENABLED"
                and ha_result["NewTemperatureIsValid"] == "VALID"
            ):
                self.metrics["temperature"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    ha_result["NewTemperatureCelsius"] / 10.0,
                )
                self.metrics["temperature_offset"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    ha_result["NewTemperatureOffset"] / 10.0,
                )

            if (
                ha_result["NewSwitchIsEnabled"] == "ENABLED"
                and ha_result["NewSwitchIsValid"] == "VALID"
            ):
                self.metrics["switch_state"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    switch_state_map[ha_result["NewSwitchState"]],
                )
                self.metrics["switch_mode"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    switch_mode_map[ha_result["NewSwitchMode"]],
                )
                self.metrics["switch_lock"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    1 if ha_result["NewSwitchLock"] else 0,
                )

            if ha_result["NewHkrIsEnabled"] == "ENABLED" and ha_result["NewHkrIsValid"] == "VALID":
                self.metrics["heater_temperature"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    ha_result["NewHkrIsTemperature"] / 10.0,
                )
                self.metrics["heater_set_temperature"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    ha_result["NewHkrSetTemperature"] / 10.0,
                )
                self.metrics["heater_valve_set_state"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    hkr_valve_map[ha_result["NewHkrSetVentilStatus"]],
                )
                self.metrics["heater_reduced_temperature"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    ha_result["NewHkrReduceTemperature"] / 10.0,
                )
                self.metrics["heater_comfort_temperature"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    ha_result["NewHkrComfortTemperature"] / 10.0,
                )
                self.metrics["heater_reduced_valve_state"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    hkr_valve_map[ha_result["NewHkrReduceVentilStatus"]],
                )
                self.metrics["heater_comfort_valve_state"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        ain,
                        device_name,
                        manufacturer,
                        productname,
                    ],
                    hkr_valve_map[ha_result["NewHkrComfortVentilStatus"]],
                )

            index += 1

            try:
                http_result = device.fc.call_http("getdeviceinfos", ain)
            except FritzHttpInterfaceError:
                logger.debug("Got FritzHttpInterfaceError for ain %s, skipping", ain)
                continue

            if "content" in http_result:
                http_data = parse_aha_device_xml(http_result["content"])
                if "battery" in http_data:
                    self.metrics["battery_level"].add_metric(
                        [
                            device.serial,
                            device.friendly_name,
                            ain,
                            device_name,
                            manufacturer,
                            productname,
                        ],
                        float(http_data["battery"]),
                    )

                if "battery_low" in http_data:
                    self.metrics["battery_low"].add_metric(
                        [
                            device.serial,
                            device.friendly_name,
                            ain,
                            device_name,
                            manufacturer,
                            productname,
                        ],
                        1 if http_data["battery_low"] == "1" else 0,
                    )

    def _get_metric_values(
        self,
    ) -> collections.abc.iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["devicepresent"]
        yield self.metrics["multimeter_power"]
        yield self.metrics["multimeter_energy"]
        yield self.metrics["temperature"]
        yield self.metrics["temperature_offset"]
        yield self.metrics["switch_state"]
        yield self.metrics["switch_mode"]
        yield self.metrics["switch_lock"]
        yield self.metrics["heater_temperature"]
        yield self.metrics["heater_set_temperature"]
        yield self.metrics["heater_valve_set_state"]
        yield self.metrics["heater_reduced_temperature"]
        yield self.metrics["heater_comfort_temperature"]
        yield self.metrics["heater_reduced_valve_state"]
        yield self.metrics["heater_comfort_valve_state"]
        yield self.metrics["battery_level"]
        yield self.metrics["battery_low"]


# Copyright 2019-2024 Patrick Dreker <patrick@dreker.de>
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
