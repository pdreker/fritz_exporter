from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Generator, ItemsView, Iterator
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar, cast

from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzArgumentError,
    FritzArrayIndexError,
    FritzConnectionException,
    FritzHttpInterfaceError,
    FritzInternalError,
    FritzLookUpError,
    FritzServiceError,
)
from fritzconnection.lib.fritzhosts import FritzHosts  # type: ignore[import]
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from fritzexporter.fritz_aha import parse_aha_devicelist_xml

if TYPE_CHECKING:
    from fritzexporter.fritzdevice import FritzDevice

logger = logging.getLogger("fritzexporter.fritzcapability")


class FritzCapability(ABC):
    subclasses: ClassVar[list[type[FritzCapability]]] = []

    def __init__(self) -> None:
        self.present: bool = False
        self.requirements: list[tuple[str, str]] = []
        self.metrics: dict[str, CounterMetricFamily | GaugeMetricFamily] = {}

    def __init_subclass__(cls, **kwargs: dict[str, Any]) -> None:
        super().__init_subclass__(**kwargs)
        logger.debug("Capability subclass %s registered", cls.__name__)
        FritzCapability.subclasses.append(cls)

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
                    FritzConnectionException,
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        self.create_metrics()
        for device in devices:
            logger.debug(
                "Fetching %s metrics for %s: %s",
                name,
                device.host,
                device.capabilities[name].present,
            )
            if device.capabilities[name].present and device.available:
                try:
                    self._generate_metric_values(device)
                except FritzConnectionException:
                    logger.exception(
                        "Device %s is unreachable, skipping %s metrics for this collection cycle",
                        device.host,
                        name,
                    )
                    device.available = False
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        pass


class FritzCapabilities:
    def __init__(self, device: FritzDevice | None = None) -> None:
        self.capabilities: dict[str, FritzCapability] = {
            subclass.__name__: subclass() for subclass in FritzCapability.subclasses
        }
        if device:
            self.check_present(device)

    def __iter__(self) -> Iterator[str]:
        return iter(self.capabilities)

    def __len__(self) -> int:
        return len(self.capabilities)

    def __getitem__(self, index: str) -> FritzCapability:
        return self.capabilities[index]

    def items(self) -> ItemsView[str, FritzCapability]:
        return self.capabilities.items()

    def empty(self) -> bool:
        return not any(cap.present for cap in self.capabilities.values())

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

    def _get_metric_values(self) -> Generator[CounterMetricFamily | GaugeMetricFamily]:
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["fec"]
        yield self.metrics["crc"]


class WanFiberInterfaceConfig(FritzCapability):
    """Optical / SFP metrics from X_AVM-DE_WANFiber.GetInfo."""

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_AVM-DE_WANFiber1", "GetInfo"))

    def create_metrics(self) -> None:
        self.metrics["optical_signal"] = GaugeMetricFamily(
            "fritz_fiber_optical_signal_level",
            "Current received optical signal level",
            labels=["serial", "friendly_name"],
            unit="dBm",
        )
        self.metrics["optical_threshold"] = GaugeMetricFamily(
            "fritz_fiber_optical_threshold",
            "Optical receive power threshold",
            labels=["serial", "friendly_name", "bound"],
            unit="dBm",
        )
        self.metrics["transmit_optical"] = GaugeMetricFamily(
            "fritz_fiber_transmit_optical_level",
            "Current transmit optical power level",
            labels=["serial", "friendly_name"],
            unit="dBm",
        )
        self.metrics["transmit_threshold"] = GaugeMetricFamily(
            "fritz_fiber_transmit_power_threshold",
            "Transmit optical power threshold",
            labels=["serial", "friendly_name", "bound"],
            unit="dBm",
        )
        self.metrics["tx_wavelength"] = GaugeMetricFamily(
            "fritz_fiber_tx_wavelength",
            "Fibre TX wavelength",
            labels=["serial", "friendly_name"],
            unit="nm",
        )
        self.metrics["info"] = GaugeMetricFamily(
            "fritz_fiber_info",
            "Fibre / SFP module information (always 1 if present)",
            labels=[
                "serial",
                "friendly_name",
                "fiber_mode",
                "sfp_vendor",
                "sfp_part_number",
                "sfp_serial_number",
                "sfp_type",
            ],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = device.fc.call_action("X_AVM-DE_WANFiber1", "GetInfo")
        labels = [device.serial, device.friendly_name]

        self.metrics["optical_signal"].add_metric(labels, result["NewOpticalSignalLevel"] / 1000)
        self.metrics["optical_threshold"].add_metric(
            [*labels, "lower"], result["NewLowerOpticalThreshold"] / 1000
        )
        self.metrics["optical_threshold"].add_metric(
            [*labels, "upper"], result["NewUpperOpticalThreshold"] / 1000
        )
        self.metrics["transmit_optical"].add_metric(
            labels, result["NewTransmitOpticalLevel"] / 1000
        )
        self.metrics["transmit_threshold"].add_metric(
            [*labels, "lower"], result["NewLowerTransmitPowerThreshold"] / 1000
        )
        self.metrics["transmit_threshold"].add_metric(
            [*labels, "upper"], result["NewUpperTransmitPowerThreshold"] / 1000
        )
        self.metrics["tx_wavelength"].add_metric(labels, result["NewTXWaveLength"])
        self.metrics["info"].add_metric(
            [
                *labels,
                str(result.get("NewFiberMode") or ""),
                str(result.get("NewSFPVendor") or ""),
                str(result.get("NewSFPPartNumber") or ""),
                str(result.get("NewSFPSerialNumber") or ""),
                str(result.get("NewSFPType") or ""),
            ],
            1,
        )

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["optical_signal"]
        yield self.metrics["optical_threshold"]
        yield self.metrics["transmit_optical"]
        yield self.metrics["transmit_threshold"]
        yield self.metrics["tx_wavelength"]
        yield self.metrics["info"]


class WanFiberGPONInfo(FritzCapability):
    """GPON identity metrics from X_AVM-DE_WANFiber.GetInfoGPON."""

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_AVM-DE_WANFiber1", "GetInfoGPON"))

    def create_metrics(self) -> None:
        self.metrics["gpon_info"] = GaugeMetricFamily(
            "fritz_fiber_gpon_info",
            "GPON identity information (always 1 if present)",
            labels=["serial", "friendly_name", "gpon_serial", "pon_id", "uni_type"],
        )
        self.metrics["onu_id"] = GaugeMetricFamily(
            "fritz_fiber_gpon_onu_id",
            "GPON ONU identifier",
            labels=["serial", "friendly_name"],
        )
        self.metrics["gem_ports"] = GaugeMetricFamily(
            "fritz_fiber_gpon_gem_port_count",
            "Number of GPON GEM ports",
            labels=["serial", "friendly_name"],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = device.fc.call_action("X_AVM-DE_WANFiber1", "GetInfoGPON")
        labels = [device.serial, device.friendly_name]
        self.metrics["gpon_info"].add_metric(
            [
                *labels,
                str(result.get("NewGPONSerial") or ""),
                str(result.get("NewPONId") or ""),
                str(result.get("NewUNIType") or ""),
            ],
            1,
        )
        self.metrics["onu_id"].add_metric(labels, result["NewONUId"])
        self.metrics["gem_ports"].add_metric(labels, result["NewGEMPortCount"])

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["gpon_info"]
        yield self.metrics["onu_id"]
        yield self.metrics["gem_ports"]


class WanFiberStatistics(FritzCapability):
    """Fibre link statistics from X_AVM-DE_WANFiber.GetStatistics."""

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_AVM-DE_WANFiber1", "GetStatistics"))

    def create_metrics(self) -> None:
        self.metrics["data"] = CounterMetricFamily(
            "fritz_fiber_data",
            "Fibre data transferred",
            labels=["serial", "friendly_name", "direction"],
            unit="bytes",
        )
        self.metrics["packets"] = CounterMetricFamily(
            "fritz_fiber_data_packets",
            "Fibre packets transferred",
            labels=["serial", "friendly_name", "direction"],
        )
        self.metrics["packet_errors"] = CounterMetricFamily(
            "fritz_fiber_packet_errors",
            "Fibre packet errors",
            labels=["serial", "friendly_name", "direction"],
        )
        self.metrics["multicast"] = CounterMetricFamily(
            "fritz_fiber_packets_multicast",
            "Fibre multicast packets",
            labels=["serial", "friendly_name"],
        )
        self.metrics["connection_rate"] = GaugeMetricFamily(
            "fritz_fiber_connection_rate",
            "Fibre connection rate as reported by the device (see docs for unit quirks)",
            labels=["serial", "friendly_name", "direction"],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = device.fc.call_action("X_AVM-DE_WANFiber1", "GetStatistics")
        labels = [device.serial, device.friendly_name]

        self.metrics["data"].add_metric([*labels, "tx"], result["NewBytesSent"])
        self.metrics["data"].add_metric([*labels, "rx"], result["NewBytesReceived"])
        self.metrics["packets"].add_metric([*labels, "tx"], result["NewPacketsSent"])
        self.metrics["packets"].add_metric([*labels, "rx"], result["NewPacketsReceived"])
        self.metrics["packet_errors"].add_metric([*labels, "tx"], result["NewPacketErrorsSent"])
        self.metrics["packet_errors"].add_metric([*labels, "rx"], result["NewPacketErrorsReceived"])
        self.metrics["multicast"].add_metric(labels, result["NewPacketsMulticast"])
        self.metrics["connection_rate"].add_metric([*labels, "rx"], result["NewConnectionRateDown"])
        self.metrics["connection_rate"].add_metric([*labels, "tx"], result["NewConnectionRateUp"])

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["data"]
        yield self.metrics["packets"]
        yield self.metrics["packet_errors"]
        yield self.metrics["multicast"]
        yield self.metrics["connection_rate"]


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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
        self.metrics["layer1max"] = GaugeMetricFamily(
            "fritz_wan_layer1_max_bitrate",
            "Layer1 max bitrate (64-bit; correct for multi-gig fibre/cable)",
            labels=["serial", "friendly_name", "direction"],
            unit="bps",
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

        # Classic Layer1*MaxBitRate is ui4 and saturates/misreports on multi-gig links.
        # Prefer the AVM 64-bit fields when the firmware provides them. On WAN
        # types that don't support them (observed on cable), FRITZ!OS reports
        # the field as an empty string rather than omitting it or returning
        # None, which crashes exposition when Prometheus scrapes (the client
        # library does float("") to format the sample) -- treat both as "not
        # provided".
        layer1_rx = fritz_wan_result.get("NewX_AVM_DE_Layer1DownstreamMaxBitRate64")
        layer1_tx = fritz_wan_result.get("NewX_AVM_DE_Layer1UpstreamMaxBitRate64")
        if layer1_rx not in (None, ""):
            self.metrics["layer1max"].add_metric(
                [device.serial, device.friendly_name, "rx"], layer1_rx
            )
        if layer1_tx not in (None, ""):
            self.metrics["layer1max"].add_metric(
                [device.serial, device.friendly_name, "tx"], layer1_tx
            )

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["wanbyterate"]
        yield self.metrics["layer1max"]


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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["wanpackets"]


class WlanConfigurationInfo(FritzCapability):
    WIFI_NAMES: ClassVar[list[str]] = ["2.4GHz", "5GHz", "Guest", "WLAN4"]

    def __init__(self) -> None:
        super().__init__()
        self.wifi_present: list[bool] = [False] * len(self.WIFI_NAMES)

    def check_capability(self, device: FritzDevice) -> None:
        for index in range(len(self.WIFI_NAMES)):
            service = f"WLANConfiguration{index + 1}"
            requirements = [
                (service, "GetInfo"),
                (service, "GetTotalAssociations"),
                (service, "GetPacketStatistics"),
            ]
            logger.debug(
                "Capability %s checking %s on %s", type(self).__name__, service, device.host
            )
            self.wifi_present[index] = all(
                (service in device.fc.services) and (action in device.fc.services[service].actions)
                for (service, action) in requirements
            )
            logger.debug(
                "Capability %s in WLAN %d set to %s on device %s",
                type(self).__name__,
                index + 1,
                self.wifi_present[index],
                device.host,
            )
            if self.wifi_present[index]:
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
                        self.wifi_present[index] = False
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
        device_wlan_cap = cast(WlanConfigurationInfo, device.capabilities[self.__class__.__name__])
        for index, wlan_present in enumerate(device_wlan_cap.wifi_present):
            logger.debug(
                "WLANConfigurationInfo._generateMetricValues checking WLAN %s (enabled: %s) on %s",
                index,
                wlan_present,
                device.host,
            )
            if wlan_present:
                logger.debug(
                    "WLANCapability._generateMetricValues fetching metrics for %s: %s",
                    device.host,
                    index,
                )
                wlan_result = device.fc.call_action(f"WLANConfiguration{index + 1}", "GetInfo")
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
                        self.WIFI_NAMES[index],
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
                        self.WIFI_NAMES[index],
                    ],
                    wlan_result["NewChannel"],
                )

                assoc_results = device.fc.call_action(
                    f"WLANConfiguration{index + 1}", "GetTotalAssociations"
                )
                self.metrics["wlanassocs"].add_metric(
                    [
                        device.serial,
                        device.friendly_name,
                        wlan_enabled,
                        wlan_result["NewStandard"],
                        wlan_result["NewSSID"],
                        str(index + 1),
                        self.WIFI_NAMES[index],
                    ],
                    assoc_results["NewTotalAssociations"],
                )

                packet_stats_result = device.fc.call_action(
                    f"WLANConfiguration{index + 1}", "GetPacketStatistics"
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
                        self.WIFI_NAMES[index],
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
                        self.WIFI_NAMES[index],
                    ],
                    packet_stats_result["NewTotalPacketsSent"],
                )

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["wlanstatus"]
        yield self.metrics["wlanchannel"]
        yield self.metrics["wlanassocs"]
        yield self.metrics["wlanpackets"]


class WlanAssociatedDevices(FritzCapability):
    """Per-station WiFi client metrics (signal strength + negotiated speed).

    Opt-in via the device ``wifi_client_info`` flag: it emits one time series per
    associated client (per-client cardinality), so it is disabled by default just
    like ``host_info``. Works on any device with radios — the box and (with the
    repeater availability fix) mesh repeaters alike.
    """

    WIFI_NAMES: ClassVar[list[str]] = ["2.4GHz", "5GHz", "Guest", "WLAN4"]

    def __init__(self) -> None:
        super().__init__()
        self.wifi_present: list[bool] = [False] * len(self.WIFI_NAMES)

    def check_capability(self, device: FritzDevice) -> None:
        if not device.wifi_client_info:
            self.present = False
            return
        for index in range(len(self.WIFI_NAMES)):
            service = f"WLANConfiguration{index + 1}"
            required = ("GetTotalAssociations", "GetGenericAssociatedDeviceInfo")
            present = service in device.fc.services and all(
                action in device.fc.services[service].actions for action in required
            )
            # Only GetTotalAssociations is safe to probe live; GetGenericAssociatedDeviceInfo
            # needs an index and raises on a radio with no clients.
            if present:
                try:
                    device.fc.call_action(service, "GetTotalAssociations")
                except (FritzServiceError, FritzActionError, FritzInternalError) as e:
                    logger.warning(
                        "disabling metrics at service %s, action GetTotalAssociations - "
                        "fritzconnection.call_action returned %s",
                        service,
                        str(e),
                    )
                    present = False
            self.wifi_present[index] = present
        self.present = any(self.wifi_present)

    def create_metrics(self) -> None:
        labels = ["serial", "friendly_name", "wifi_name", "client_mac", "client_ip"]
        self.metrics["signal"] = GaugeMetricFamily(
            "fritz_wifi_client_signal_strength",
            "Signal strength of an associated WiFi client in percent",
            labels=labels,
        )
        self.metrics["speed"] = GaugeMetricFamily(
            "fritz_wifi_client_speed",
            "Negotiated speed of an associated WiFi client in Mbit/s",
            labels=labels,
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        device_cap = cast(WlanAssociatedDevices, device.capabilities[self.__class__.__name__])
        for index, present in enumerate(device_cap.wifi_present):
            if not present:
                continue
            service = f"WLANConfiguration{index + 1}"
            try:
                assoc = device.fc.call_action(service, "GetTotalAssociations")
                total = int(assoc.get("NewTotalAssociations", 0))
            except (FritzServiceError, FritzActionError, FritzInternalError) as e:
                logger.warning(
                    "failed to read WiFi associations from %s on %s: %s",
                    service,
                    device.host,
                    str(e),
                )
                continue
            for client_index in range(total):
                try:
                    info = device.fc.call_action(
                        service,
                        "GetGenericAssociatedDeviceInfo",
                        NewAssociatedDeviceIndex=client_index,
                    )
                except (
                    FritzArrayIndexError,
                    FritzServiceError,
                    FritzActionError,
                    FritzInternalError,
                ) as e:
                    logger.debug(
                        "failed to read associated device info for %s index %d on %s: %s",
                        service,
                        client_index,
                        device.host,
                        str(e),
                    )
                    break
                labels = [
                    device.serial,
                    device.friendly_name,
                    self.WIFI_NAMES[index],
                    info["NewAssociatedDeviceMACAddress"],
                    info["NewAssociatedDeviceIPAddress"],
                ]
                self.metrics["signal"].add_metric(labels, info["NewX_AVM-DE_SignalStrength"])
                self.metrics["speed"].add_metric(labels, info["NewX_AVM-DE_Speed"])

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["signal"]
        yield self.metrics["speed"]


class MeshTopology(FritzCapability):
    """Mesh backhaul link quality, read from the mesh master.

    Exposes the current/peak data rate and the state of each backhaul link between
    mesh nodes (e.g. FRITZ!Box <-> repeater), parsed from the mesh topology
    (``Hosts1`` ``X_AVM-DE_GetMeshListPath``). Only the mesh master exposes the full
    topology, so the capability is present only there. Client links are intentionally
    excluded to keep cardinality bounded (one series per backhaul link, not per client).

    A node pair can have more than one concurrent backhaul link of the same
    ``type`` (e.g. simultaneous 2.4GHz + 5GHz WLAN backhaul) - the ``interface``
    label (the reporting side's own ``node_interfaces`` name, e.g. ``AP:5G:0``)
    distinguishes those. AVM also reports each physical link twice - once under
    each endpoint's own node record, with identical uid and data both times -
    which is deduped below; that's a genuine duplicate, not a second link.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("Hosts1", "X_AVM-DE_GetMeshListPath"))

    def create_metrics(self) -> None:
        link_labels = [
            "serial",
            "friendly_name",
            "node",
            "peer",
            "type",
            "interface",
            "direction",
        ]
        self.metrics["datarate"] = GaugeMetricFamily(
            "fritz_mesh_link_current_data_rate_kbps",
            "Current data rate of a mesh backhaul link in kbit/s",
            labels=link_labels,
        )
        self.metrics["maxdatarate"] = GaugeMetricFamily(
            "fritz_mesh_link_max_data_rate_kbps",
            "Maximum data rate of a mesh backhaul link in kbit/s",
            labels=link_labels,
        )
        self.metrics["available"] = GaugeMetricFamily(
            "fritz_mesh_link_available",
            "Mesh backhaul link state (1=connected, 0=otherwise)",
            labels=["serial", "friendly_name", "node", "peer", "type", "interface"],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        try:
            # get_mesh_topology is annotated dict | str (str only when raw=True);
            # with the default raw=False it always returns the parsed dict.
            topology = cast("dict[str, Any]", FritzHosts(fc=device.fc).get_mesh_topology())
        except FritzActionError:
            # Only the mesh master can serve the topology; every other node answers
            # "Device has no access to topology information" (404). That is the normal
            # case for mesh slaves/repeaters, so log it quietly and skip mesh metrics
            # for this device — do NOT mark it unavailable.
            logger.debug("No mesh topology available from %s (not the mesh master)", device.host)
            return
        except FritzConnectionException:
            # The mesh list is fetched over HTTP; a transient failure should not
            # mark the whole device unavailable — just skip mesh metrics this cycle.
            logger.warning("Failed to retrieve mesh topology from %s", device.host)
            return

        nodes = topology.get("nodes", [])
        uid_name = {n["uid"]: (n.get("device_name") or "n/a") for n in nodes if "uid" in n}
        meshed = {n["uid"] for n in nodes if n.get("uid") and n.get("is_meshed")}
        seen: set[str] = set()
        for node in nodes:
            if not node.get("is_meshed"):
                continue
            for interface in node.get("node_interfaces", []):
                link_type = interface.get("type", "")
                interface_name = interface.get("name", "")
                for link in interface.get("node_links", []):
                    link_uid = link.get("uid")
                    n1 = link.get("node_1_uid")
                    n2 = link.get("node_2_uid")
                    # Only backhaul (mesh-node <-> mesh-node). Each physical link is
                    # listed once under each endpoint's own node record with identical
                    # uid and data both times, so dedup by uid to avoid double-counting
                    # that one fact - a node pair can still have multiple concurrent
                    # links of the same type (e.g. 2.4GHz + 5GHz backhaul), which are
                    # distinct uids and stay separate, disambiguated by `interface`.
                    if link_uid in seen or n1 not in meshed or n2 not in meshed:
                        continue
                    seen.add(link_uid)
                    base = [
                        device.serial,
                        device.friendly_name,
                        uid_name[n1],
                        uid_name[n2],
                        link_type,
                        interface_name,
                    ]
                    self.metrics["available"].add_metric(
                        base, 1.0 if link.get("state") == "CONNECTED" else 0.0
                    )
                    self.metrics["datarate"].add_metric(
                        [*base, "rx"], link.get("cur_data_rate_rx", 0)
                    )
                    self.metrics["datarate"].add_metric(
                        [*base, "tx"], link.get("cur_data_rate_tx", 0)
                    )
                    self.metrics["maxdatarate"].add_metric(
                        [*base, "rx"], link.get("max_data_rate_rx", 0)
                    )
                    self.metrics["maxdatarate"].add_metric(
                        [*base, "tx"], link.get("max_data_rate_tx", 0)
                    )

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["datarate"]
        yield self.metrics["maxdatarate"]
        yield self.metrics["available"]


class HostInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("Hosts1", "GetHostNumberOfEntries"))
        self.requirements.append(("Hosts1", "GetGenericHostEntry"))
        self.requirements.append(("Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP"))

    def _probe_specific_host_entry(self, device: FritzDevice, svc: str, action: str) -> None:
        with suppress(FritzLookUpError):
            # Probe the action with a deliberately bogus IP address; we only care
            # whether the call is accepted and returns the expected lookup error.
            device.fc.call_action(
                svc,
                action,
                arguments={"NewIPAddress": "0.0.0.0"},  # noqa: S104
            )

    def _probe_action(self, device: FritzDevice, svc: str, action: str) -> bool:
        try:
            if action == "GetHostNumberOfEntries":
                device.fc.call_action(svc, action)
            elif action == "GetGenericHostEntry":
                device.fc.call_action(svc, action, arguments={"NewIndex": 1})
            elif action == "X_AVM-DE_GetSpecificHostEntryByIP":
                self._probe_specific_host_entry(device, svc, action)
        except (FritzServiceError, FritzActionError, FritzInternalError) as e:
            logger.warning(
                "disabling metrics at service %s, action %s - "
                "fritzconnection.call_action returned %s",
                svc,
                action,
                str(e),
            )
            return False
        return True

    def check_capability(self, device: FritzDevice) -> None:
        self.present = device.host_info and all(
            (service in device.fc.services) and (action in device.fc.services[service].actions)
            for (service, action) in self.requirements
        )
        logger.debug(
            "Capability %s set to %s on device %s", type(self).__name__, self.present, device.host
        )
        if self.present:
            for svc, action in self.requirements:
                if not self._probe_action(device, svc, action):
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
            try:
                host_result = device.fc.call_action(
                    "Hosts1", "GetGenericHostEntry", NewIndex=host_index
                )
            except FritzArrayIndexError:
                # The host table shrank between GetHostNumberOfEntries and this read
                # (a client left mid-scan), so this index is now out of range
                # (UPnP errorCode 713, SpecifiedArrayIndexInvalid). This is a benign
                # race on the DHCP server's host table, not a device outage: stop
                # enumerating and keep the hosts collected so far. Letting it bubble
                # up would flip the whole device to unreachable for this cycle.
                logger.debug(
                    "Host table shrank during scan of device serial %s at index %s; "
                    "stopping host enumeration for this cycle",
                    device.serial,
                    host_index,
                )
                break
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
                    "Unable to fetch extended AVM host information for host number %s: no IP found",
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
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["hostactive"]
        yield self.metrics["hostspeed"]


class HomeAutomation(FritzCapability):
    _HA_LABELS: ClassVar[list[str]] = [
        "serial",
        "friendly_name",
        "ain",
        "device_name",
        "device_id",
        "manufacturer",
        "productname",
    ]
    _HKR_VALVE_MAP: ClassVar[dict[str, int]] = {"CLOSED": 0, "OPEN": 1, "TEMP": 2}
    _HTTP_SWITCH_STATE_MAP: ClassVar[dict[str | None, int]] = {"0": 0, "1": 1}
    _HTTP_SWITCH_MODE_MAP: ClassVar[dict[str | None, int]] = {"manuell": 0, "auto": 1}

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_AVM-DE_Homeauto1", "GetInfo"))

    def create_metrics(self) -> None:
        labels = self._HA_LABELS
        metric_definitions: list[tuple[str, str, str]] = [
            ("devicepresent", "fritz_ha_device_present", "Indicates that the device is present"),
            ("battery_level", "fritz_ha_battery_level_percent", "Battery level in percent"),
            ("battery_low", "fritz_ha_battery_low", "Indicates that the battery is low"),
            ("multimeter_power", "fritz_ha_multimeter_power_W", "Power in W"),
            ("multimeter_energy", "fritz_ha_multimeter_energy_Wh", "Energy in Wh"),
            ("temperature", "fritz_ha_temperature_C", "Temperature in °C"),
            (
                "temperature_offset",
                "fritz_ha_temperature_offset_C",
                "Temperature offset in °C",
            ),
            ("switch_state", "fritz_ha_switch_state", "Switch state"),
            ("switch_mode", "fritz_ha_switch_mode", "Switch mode"),
            ("switch_lock", "fritz_ha_switch_lock", "Switch lock"),
            (
                "heater_temperature",
                "fritz_ha_heater_temperature_C",
                "Heater temperature in °C",
            ),
            (
                "heater_set_temperature",
                "fritz_ha_heater_set_temperature_C",
                "Heater set temperature in °C",
            ),
            (
                "heater_valve_set_state",
                "fritz_ha_heater_valve_set_state",
                "Heater valve set state",
            ),
            (
                "heater_reduced_temperature",
                "fritz_ha_heater_reduced_temperature_C",
                "Heater reduced temperature in °C",
            ),
            (
                "heater_comfort_temperature",
                "fritz_ha_heater_comfort_temperature_C",
                "Heater comfort temperature in °C",
            ),
            (
                "heater_reduced_valve_state",
                "fritz_ha_heater_reduced_valve_state",
                "Heater reduced valve state",
            ),
            (
                "heater_comfort_valve_state",
                "fritz_ha_heater_comfort_valve_state",
                "Heater comfort valve state",
            ),
        ]
        for key, metric_name, help_text in metric_definitions:
            self.metrics[key] = GaugeMetricFamily(metric_name, help_text, labels=labels)

    def _build_ha_labels(self, device: FritzDevice, ha_device: dict[str, Any]) -> list[str]:
        return [
            device.serial,
            device.friendly_name,
            ha_device["ain"],
            ha_device["device_name"],
            str(ha_device["device_id"]),
            ha_device["manufacturer"],
            ha_device["productname"],
        ]

    def _collect_multimeter(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        powermeter = ha_device["powermeter"]
        if powermeter is None:
            return
        if powermeter["power"] is not None:
            self.metrics["multimeter_power"].add_metric(labels, powermeter["power"] / 1000.0)
        if powermeter["energy"] is not None:
            self.metrics["multimeter_energy"].add_metric(labels, powermeter["energy"])

    def _collect_temperature(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        temperature = ha_device["temperature"]
        if temperature is None:
            return
        if temperature["celsius"] is not None:
            self.metrics["temperature"].add_metric(labels, temperature["celsius"] / 10.0)
        if temperature["offset"] is not None:
            self.metrics["temperature_offset"].add_metric(labels, temperature["offset"] / 10.0)

    def _collect_switch(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        switch = ha_device["switch"]
        if switch is None:
            return
        if switch["state"] in self._HTTP_SWITCH_STATE_MAP:
            self.metrics["switch_state"].add_metric(
                labels, self._HTTP_SWITCH_STATE_MAP[switch["state"]]
            )
        if switch["mode"] in self._HTTP_SWITCH_MODE_MAP:
            self.metrics["switch_mode"].add_metric(
                labels, self._HTTP_SWITCH_MODE_MAP[switch["mode"]]
            )
        if switch["lock"] is not None:
            self.metrics["switch_lock"].add_metric(labels, 1 if switch["lock"] == "1" else 0)

    def _collect_heater(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        hkr = ha_device["hkr"]
        if hkr is None:
            return
        # HKR temperatures from the AHA HTTP API are reported in half-degree
        # steps, unlike the plain <temperature> element (tenths of a degree).
        if hkr["tist"] is not None:
            self.metrics["heater_temperature"].add_metric(labels, hkr["tist"] / 2.0)
        if hkr["tsoll"] is not None:
            self.metrics["heater_set_temperature"].add_metric(labels, hkr["tsoll"] / 2.0)
        if hkr["absenk"] is not None:
            self.metrics["heater_reduced_temperature"].add_metric(labels, hkr["absenk"] / 2.0)
        if hkr["komfort"] is not None:
            self.metrics["heater_comfort_temperature"].add_metric(labels, hkr["komfort"] / 2.0)

    def _collect_heater_valve_state(self, device: FritzDevice, ain: str, labels: list[str]) -> None:
        # The valve/ventil status fields are not exposed by the AHA HTTP API's
        # getdevicelistinfos response, so they still require one TR-064 call
        # per thermostat (identified by AIN, not by enumeration index).
        try:
            ha_result = device.fc.call_action(
                "X_AVM-DE_Homeauto1", "GetSpecificDeviceInfos", NewAIN=ain
            )
        except FritzArgumentError, FritzActionError, FritzArrayIndexError:
            logger.debug("Could not fetch HKR valve state for ain %s, skipping", ain)
            return

        if "NewHkrSetVentilStatus" in ha_result:
            self.metrics["heater_valve_set_state"].add_metric(
                labels, self._HKR_VALVE_MAP[ha_result["NewHkrSetVentilStatus"]]
            )
        if "NewHkrReduceVentilStatus" in ha_result:
            self.metrics["heater_reduced_valve_state"].add_metric(
                labels, self._HKR_VALVE_MAP[ha_result["NewHkrReduceVentilStatus"]]
            )
        if "NewHkrComfortVentilStatus" in ha_result:
            self.metrics["heater_comfort_valve_state"].add_metric(
                labels, self._HKR_VALVE_MAP[ha_result["NewHkrComfortVentilStatus"]]
            )

    def _collect_battery(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        if ha_device["battery_level"] is not None:
            self.metrics["battery_level"].add_metric(labels, float(ha_device["battery_level"]))
        if ha_device["battery_low"] is not None:
            self.metrics["battery_low"].add_metric(
                labels, 1 if ha_device["battery_low"] == "1" else 0
            )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        try:
            http_result = device.fc.call_http("getdevicelistinfos")
        except FritzHttpInterfaceError:
            logger.debug("Got FritzHttpInterfaceError for device %s, skipping", device.host)
            return
        if "content" not in http_result:
            return

        for ha_device in parse_aha_devicelist_xml(http_result["content"]):
            labels = self._build_ha_labels(device, ha_device)

            self.metrics["devicepresent"].add_metric(labels, 2 if ha_device["present"] else 0)
            self._collect_multimeter(ha_device, labels)
            self._collect_temperature(ha_device, labels)
            self._collect_switch(ha_device, labels)
            self._collect_heater(ha_device, labels)
            self._collect_battery(ha_device, labels)
            if ha_device["hkr"] is not None:
                self._collect_heater_valve_state(device, ha_device["ain"], labels)

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
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
