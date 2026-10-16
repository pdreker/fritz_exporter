from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Generator, ItemsView, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, cast
from urllib.parse import urlparse

from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzArgumentError,
    FritzArrayIndexError,
    FritzConnectionException,
    FritzInternalError,
    FritzLookUpError,
    FritzServiceError,
)
from fritzconnection.lib.fritzhosts import FritzHosts  # type: ignore[import]
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from requests.exceptions import RequestException

from fritzexporter.fritz_aha import parse_aha_devicelist_xml
from fritzexporter.fritz_docsis import (
    DOC_INFO_PAGE,
    DownstreamChannel,
    UpstreamChannel,
    parse_docsis_response,
)
from fritzexporter.fritz_fiber import FIBER_STATS_PAGE, parse_fiber_stats_response
from fritzexporter.fritz_rest_generic import (
    parse_connections_response,
    parse_monitor_segment,
)
from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.reply import DataError, Issues, Reply, read_action

if TYPE_CHECKING:
    from fritzexporter.fritzdevice import FritzDevice

logger = logging.getLogger("fritzexporter.fritzcapability")

# Errors that mean a bug in the exporter rather than bad device data, since all
# reads go through fritzexporter.reply. They are contained like data problems,
# so one capability's bug does not break the scrape, but logged with a traceback
# on every scrape.
_BUG_ERRORS: tuple[type[Exception], ...] = (
    KeyError,
    IndexError,
    TypeError,
    ValueError,
    AttributeError,
    ArithmeticError,
)

# A lasting problem is logged when it appears or changes level, then reminded of
# at this interval, so its details are never far back in the log.
PROBLEM_REMINDER_INTERVAL: float = 3600.0


class ProblemLevel:
    """How much of a capability's data the last scrape of a device skipped."""

    PARTIAL = "partial"  # some values or items
    SKIPPED = "skipped"  # the whole capability
    ERROR = "error"  # the whole capability, because of a bug in the exporter


class _Problem:
    """A problem of one capability on one device, while it lasts."""

    def __init__(self, level: str) -> None:
        self.level = level
        self.since = datetime.now(tz=UTC)
        self.scrapes = 0
        self.reminded = time.monotonic()


def _origin(exc: BaseException) -> str:
    """``file:line in function`` of the innermost frame of ``exc``."""
    tb = exc.__traceback__
    if tb is None:
        return "unknown"
    while tb.tb_next is not None:
        tb = tb.tb_next
    code = tb.tb_frame.f_code
    return f"{Path(code.co_filename).name}:{tb.tb_lineno} in {code.co_name}"


def _describe(exc: BaseException) -> str:
    """A log-safe description: DataError texts never hold values, other texts may."""
    if isinstance(exc, DataError | FritzWebUiError):
        return str(exc)
    return f"{type(exc).__name__} at {_origin(exc)}"


def _url_host(url: str, what: str) -> str | None:
    """The host name of ``url``; a malformed URL is a :class:`DataError`."""
    try:
        return urlparse(url).hostname
    except ValueError:
        msg = f"{what}: not a valid URL"
        raise DataError(msg) from None


def _div(value: float | None, by: float) -> float | None:
    """``value / by``, passing an absent value on."""
    return None if value is None else value / by


def _since(problem: _Problem) -> str:
    return problem.since.astimezone().isoformat(timespec="seconds")


def _check_samples(
    metrics: dict[str, CounterMetricFamily | GaugeMetricFamily], counts: dict[str, int]
) -> None:
    """Make sure every sample added since ``counts`` can be rendered.

    prometheus_client stores what ``add_metric`` gets and converts it only while
    rendering the scrape, where a bad value fails the whole scrape.
    """
    for key, family in metrics.items():
        for sample in family.samples[counts.get(key, 0) :]:
            if not isinstance(sample.value, int | float):
                msg = f"metric {family.name}: sample value is {type(sample.value).__name__}"
                raise DataError(msg)
            if not all(isinstance(label, str) for label in sample.labels.values()):
                msg = f"metric {family.name}: label value is not text"
                raise DataError(msg)


def probe_action(
    device: FritzDevice,
    service: str,
    action: str,
    failures: tuple[type[Exception], ...] = (FritzInternalError,),
) -> bool:
    """Call a TR-064 action to confirm the device really supports it.

    Some boxes advertise services or actions they do not implement and answer
    with UPnP error 401 (Invalid Action). That is expected, so it is logged at
    INFO; any error in ``failures`` is logged as a warning. Both return False.
    """
    try:
        device.fc.call_action(service, action)
    except FritzServiceError, FritzActionError:
        logger.info(
            "device %s does not support service %s, action %s; disabling its metrics",
            device.host,
            service,
            action,
        )
        return False
    except failures as e:
        logger.warning(
            "disabling metrics at service %s, action %s - fritzconnection.call_action returned %s",
            service,
            action,
            str(e),
        )
        return False
    return True


class FritzCapability(ABC):
    subclasses: ClassVar[list[type[FritzCapability]]] = []

    def __init__(self) -> None:
        self.present: bool = False
        self.requirements: list[tuple[str, str]] = []
        self.metrics: dict[str, CounterMetricFamily | GaugeMetricFamily] = {}
        # Defects contained while collecting the current device; see _add/_item.
        self.issues = Issues()
        # Per device (on the device's own instance): the problem of the last
        # scrape, if any; exported as fritz_read_problem.
        self.problem: _Problem | None = None

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

        # Some boxes report services/actions they don't actually support, so
        # call each requirement and disable the capability if one fails.
        if self.present:
            for svc, action in self.requirements:
                if not probe_action(
                    device,
                    svc,
                    action,
                    (FritzInternalError, FritzArgumentError, FritzConnectionException),
                ):
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
                self._collect_device(device, name)
        yield from self._get_metric_values()

    def _collect_device(self, device: FritzDevice, name: str) -> None:
        """Collect one device, keeping each failure at the level it affects.

        * A value that cannot be read costs the samples built from it (``_add``),
          an item that cannot be read costs that item (``_item``).
        * The web interface fails, or a reply cannot be read at all: the samples
          this device added to this capability are dropped, so no partial set is
          exported; other capabilities and devices are unaffected.
        * A bug in the exporter is contained the same way, but logged with a
          traceback on every scrape.
        * The device does not answer: its samples of this capability are dropped
          and it is marked unavailable for this cycle.

        Anything else, such as the refused MD5 login, is meant to reach the
        collector.
        """
        counts = self._sample_counts()
        self.issues = Issues()
        try:
            self._generate_metric_values(device)
            _check_samples(self.metrics, counts)
        except FritzConnectionException, RequestException:
            self._rollback(counts)
            logger.exception(
                "Device %s is unreachable, skipping %s metrics for this collection cycle",
                device.host,
                name,
            )
            device.available = False
        except (FritzWebUiError, DataError) as e:
            self._rollback(counts)
            self._report(
                device,
                name,
                ProblemLevel.SKIPPED,
                f"Could not read {name} data from {device.host}, skipping it",
                _describe(e),
            )
        except _BUG_ERRORS:
            self._rollback(counts)
            logger.exception(
                "Bug while collecting %s data from %s, skipping it for this cycle; "
                "please report this",
                name,
                device.host,
            )
            self._report(device, name, ProblemLevel.ERROR, "", "")
        else:
            if self.issues.messages:
                self._report(
                    device,
                    name,
                    ProblemLevel.PARTIAL,
                    f"Skipped part of the {name} data from {device.host}",
                    "; ".join(self.issues.messages),
                )
            else:
                self._report(device, name, None, "", "")

    @staticmethod
    def _report(
        device: FritzDevice, name: str, level: str | None, headline: str, details: str
    ) -> None:
        """Log a problem when it appears or changes level, remind of it while it lasts.

        A bug (``ProblemLevel.ERROR``) is logged by the caller on every scrape.
        """
        state = device.capabilities[name]
        if level is None:
            if state.problem is not None:
                logger.info(
                    "Reading %s data from %s works again (problem since %s, %d scrapes)",
                    name,
                    device.host,
                    _since(state.problem),
                    state.problem.scrapes,
                )
            state.problem = None
            return
        if state.problem is None or state.problem.level != level:
            state.problem = _Problem(level)
            if level != ProblemLevel.ERROR:
                logger.warning("%s: %s", headline, details)
        elif (
            level != ProblemLevel.ERROR
            and time.monotonic() - state.problem.reminded >= PROBLEM_REMINDER_INTERVAL
        ):
            state.problem.reminded = time.monotonic()
            logger.warning(
                "%s, still since %s (%d scrapes): %s",
                headline,
                _since(state.problem),
                state.problem.scrapes + 1,
                details,
            )
        else:
            logger.debug("%s: %s", headline, details)
        state.problem.scrapes += 1

    def _sample_counts(self) -> dict[str, int]:
        return {key: len(family.samples) for key, family in self.metrics.items()}

    def _rollback(self, counts: dict[str, int]) -> None:
        for key, count in counts.items():
            del self.metrics[key].samples[count:]

    def _add(self, key: str, labels: list[str], value: float | None) -> None:
        """Add one sample; ``None`` (absent, or recorded in ``self.issues``) adds none."""
        if value is not None:
            self.metrics[key].add_metric(labels, value)

    @contextmanager
    def _item(self, what: str) -> Iterator[None]:
        """Collect one item (a host, a channel, ...); if it cannot be read, skip it."""
        counts = self._sample_counts()
        with self.issues.item(what, on_skip=lambda: self._rollback(counts)):
            yield

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
        info = read_action(device, "DeviceInfo1", "GetInfo")
        labels = [
            info.text("NewModelName", "n/a"),
            info.text("NewSoftwareVersion", "n/a"),
            info.text("NewSerialNumber"),
            device.friendly_name,
        ]
        self._add("uptime", labels, self.issues.num(info, "NewUpTime"))

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
        result = read_action(device, "Hosts1", "GetHostNumberOfEntries")
        self._add(
            "numhosts",
            [device.serial, device.friendly_name],
            self.issues.num(result, "NewHostNumberOfEntries"),
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
        result = read_action(device, "UserInterface1", "GetInfo")
        # The version label depends on the flag, so both are read strictly.
        available = result.flag("NewUpgradeAvailable")
        new_software_version = result.text("NewX_AVM-DE_Version", "n/a") if available else "n/a"
        self._add(
            "update", [device.serial, device.friendly_name, new_software_version], int(available)
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
        result = read_action(device, "LANEthernetInterfaceConfig1", "GetInfo")
        labels = [device.serial, device.friendly_name]
        self._add("lanenable", labels, self.issues.flag(result, "NewEnable"))
        self._add("lanstatus", labels, self.issues.equals(result, "NewStatus", "Up"))

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
        result = read_action(device, "LANEthernetInterfaceConfig1", "GetStatistics")
        labels = [device.serial, device.friendly_name]
        for key, direction, field in (
            ("lanbytes", "rx", "NewBytesReceived"),
            ("lanbytes", "tx", "NewBytesSent"),
            ("lanpackets", "rx", "NewPacketsReceived"),
            ("lanpackets", "tx", "NewPacketsSent"),
        ):
            self._add(key, [*labels, direction], self.issues.num(result, field))

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
        result = read_action(device, "WANDSLInterfaceConfig1", "GetInfo")
        labels = [device.serial, device.friendly_name]
        self._add("enable", labels, self.issues.flag(result, "NewEnable"))
        self._add("status", labels, self.issues.equals(result, "NewStatus", "Up"))
        for direction, kind, field in (
            ("tx", "curr", "NewUpstreamCurrRate"),
            ("rx", "curr", "NewDownstreamCurrRate"),
            ("tx", "max", "NewUpstreamMaxRate"),
            ("rx", "max", "NewDownstreamMaxRate"),
        ):
            self._add("datarate", [*labels, direction, kind], self.issues.num(result, field))
        # Noise margin and attenuation come in tenths of a dB.
        for direction, prefix in (("tx", "NewUpstream"), ("rx", "NewDownstream")):
            self._add(
                "noisemargin",
                [*labels, direction],
                self.issues.num(result, f"{prefix}NoiseMargin", divide_by=10),
            )
            self._add(
                "attenuation",
                [*labels, direction],
                self.issues.num(result, f"{prefix}Attenuation", divide_by=10),
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
        result = read_action(device, "WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo")
        labels = [device.serial, device.friendly_name]
        self._add("fec", labels, self.issues.num(result, "NewFECErrors"))
        self._add("crc", labels, self.issues.num(result, "NewCRCErrors"))

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
        result = read_action(device, "X_AVM-DE_WANFiber1", "GetInfo")
        labels = [device.serial, device.friendly_name]

        # Levels and thresholds come in thousandths of a dBm. The fallback below
        # depends on both levels, so they are read strictly.
        receive = result.num("NewOpticalSignalLevel") / 1000
        transmit = result.num("NewTransmitOpticalLevel") / 1000
        if receive == 0 and transmit == 0:
            # Some boxes answer 0 for both levels over TR-064 although the web
            # UI shows real values; fall back to the page the web UI reads.
            receive, transmit = self._webui_levels(device, receive, transmit)

        self._add("optical_signal", labels, receive)
        self._add("transmit_optical", labels, transmit)
        for bound in ("Lower", "Upper"):
            self._add(
                "optical_threshold",
                [*labels, bound.lower()],
                self.issues.num(result, f"New{bound}OpticalThreshold", divide_by=1000),
            )
            self._add(
                "transmit_threshold",
                [*labels, bound.lower()],
                self.issues.num(result, f"New{bound}TransmitPowerThreshold", divide_by=1000),
            )
        self._add("tx_wavelength", labels, self.issues.num(result, "NewTXWaveLength"))
        with self._item("fibre info"):
            self._add(
                "info",
                [
                    *labels,
                    result.text("NewFiberMode", ""),
                    result.text("NewSFPVendor", ""),
                    result.text("NewSFPPartNumber", ""),
                    result.text("NewSFPSerialNumber", ""),
                    result.text("NewSFPType", ""),
                ],
                1,
            )

    def _webui_levels(
        self, device: FritzDevice, receive: float, transmit: float
    ) -> tuple[float, float]:
        if not device.webui_client:
            return receive, transmit
        try:
            levels = parse_fiber_stats_response(
                device.webui_client.fetch_page(FIBER_STATS_PAGE), self.issues
            )
        except (FritzWebUiError, DataError) as e:
            # The fallback is optional: keep the TR-064 values and the other fibre
            # metrics instead of skipping the capability.
            logger.warning("Could not read fibre levels from the web UI of %s: %s", device.host, e)
            return receive, transmit
        if levels["receive_dbm"] is not None:
            receive = levels["receive_dbm"]
        if levels["transmit_dbm"] is not None:
            transmit = levels["transmit_dbm"]
        return receive, transmit

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
        result = read_action(device, "X_AVM-DE_WANFiber1", "GetInfoGPON")
        labels = [device.serial, device.friendly_name]
        with self._item("GPON info"):
            self._add(
                "gpon_info",
                [
                    *labels,
                    result.text("NewGPONSerial", ""),
                    result.text("NewPONId", ""),
                    result.text("NewUNIType", ""),
                ],
                1,
            )
        self._add("onu_id", labels, self.issues.num(result, "NewONUId"))
        self._add("gem_ports", labels, self.issues.num(result, "NewGEMPortCount"))

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
        result = read_action(device, "X_AVM-DE_WANFiber1", "GetStatistics")
        labels = [device.serial, device.friendly_name]
        for key, direction, field in (
            ("data", "tx", "NewBytesSent"),
            ("data", "rx", "NewBytesReceived"),
            ("packets", "tx", "NewPacketsSent"),
            ("packets", "rx", "NewPacketsReceived"),
            ("packet_errors", "tx", "NewPacketErrorsSent"),
            ("packet_errors", "rx", "NewPacketErrorsReceived"),
            ("connection_rate", "rx", "NewConnectionRateDown"),
            ("connection_rate", "tx", "NewConnectionRateUp"),
        ):
            self._add(key, [*labels, direction], self.issues.num(result, field))
        self._add("multicast", labels, self.issues.num(result, "NewPacketsMulticast"))

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
        result = read_action(device, "WANPPPConnection1", "GetStatusInfo")
        labels = [device.serial, device.friendly_name]
        self._add("uptime", labels, self.issues.num(result, "NewUptime"))
        with self._item("PPP connection state"):
            self._add(
                "connected",
                [*labels, result.text("NewLastConnectionError", "")],
                self.issues.equals(result, "NewConnectionStatus", "Connected"),
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
        result = read_action(device, "WANCommonInterfaceConfig1", "GetCommonLinkProperties")
        # The access type is a label of every sample here, so it is read strictly.
        labels = [device.serial, device.friendly_name, result.text("NewWANAccessType")]
        self._add(
            "wanconfig", [*labels, "tx"], self.issues.num(result, "NewLayer1UpstreamMaxBitRate")
        )
        self._add(
            "wanconfig", [*labels, "rx"], self.issues.num(result, "NewLayer1DownstreamMaxBitRate")
        )
        self._add(
            "wanlinkstatus", labels, self.issues.equals(result, "NewPhysicalLinkStatus", "Up")
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
        service = self.WAN_COMMON_INTERFACE_SERVICE
        labels = [device.serial, device.friendly_name]
        rx = read_action(device, service, "GetTotalBytesReceived")
        tx = read_action(device, service, "GetTotalBytesSent")
        self._add("wanbytes", [*labels, "tx"], self.issues.num(tx, "NewTotalBytesSent"))
        self._add("wanbytes", [*labels, "rx"], self.issues.num(rx, "NewTotalBytesReceived"))

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
        result = read_action(device, "WANCommonIFC1", "GetAddonInfos")
        labels = [device.serial, device.friendly_name]
        self._add("wanbyterate", [*labels, "rx"], self.issues.num(result, "NewByteReceiveRate"))
        self._add("wanbyterate", [*labels, "tx"], self.issues.num(result, "NewByteSendRate"))

        # Classic Layer1*MaxBitRate is ui4 and saturates/misreports on multi-gig links.
        # Prefer the AVM 64-bit fields when the firmware provides them. On WAN
        # types that don't support them (observed on cable), FRITZ!OS reports
        # them as an empty string, which reads as absent.
        for direction, field in (
            ("rx", "NewX_AVM_DE_Layer1DownstreamMaxBitRate64"),
            ("tx", "NewX_AVM_DE_Layer1UpstreamMaxBitRate64"),
        ):
            self._add("layer1max", [*labels, direction], self.issues.opt_num(result, field))

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
        service = self.WAN_COMMON_INTERFACE_SERVICE
        labels = [device.serial, device.friendly_name]
        rx = read_action(device, service, "GetTotalPacketsReceived")
        tx = read_action(device, service, "GetTotalPacketsSent")
        self._add("wanpackets", [*labels, "tx"], self.issues.num(tx, "NewTotalPacketsSent"))
        self._add("wanpackets", [*labels, "rx"], self.issues.num(rx, "NewTotalPacketsReceived"))

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
                    if not probe_action(device, svc, action):
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
                with self._item(f"WLAN {index + 1}"):
                    self._collect_wlan(device, index)

    def _collect_wlan(self, device: FritzDevice, index: int) -> None:
        service = f"WLANConfiguration{index + 1}"
        info = read_action(device, service, "GetInfo")
        enabled = "1" if info.flag("NewEnable") else "0"
        # The labels shared by every WLAN metric, in label order; "direction"
        # of the packet counter goes in before the last two. A defect in them
        # skips this WLAN.
        head = [
            device.serial,
            device.friendly_name,
            enabled,
            info.text("NewStandard", ""),
            info.text("NewSSID", ""),
        ]
        tail = [str(index + 1), self.WIFI_NAMES[index]]
        self._add("wlanstatus", [*head, *tail], self.issues.equals(info, "NewStatus", "Up"))
        self._add("wlanchannel", [*head, *tail], self.issues.num(info, "NewChannel"))

        associations = read_action(device, service, "GetTotalAssociations")
        self._add(
            "wlanassocs", [*head, *tail], self.issues.num(associations, "NewTotalAssociations")
        )

        packets = read_action(device, service, "GetPacketStatistics")
        self._add(
            "wlanpackets", [*head, "rx", *tail], self.issues.num(packets, "NewTotalPacketsReceived")
        )
        self._add(
            "wlanpackets", [*head, "tx", *tail], self.issues.num(packets, "NewTotalPacketsSent")
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
                present = probe_action(device, service, "GetTotalAssociations")
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
            with self._item(f"WLAN {index + 1} clients"):
                self._collect_clients(device, index)

    def _collect_clients(self, device: FritzDevice, index: int) -> None:
        service = f"WLANConfiguration{index + 1}"
        try:
            assoc = read_action(device, service, "GetTotalAssociations")
            # The loop depends on the count, so a defect skips this WLAN.
            total = assoc.opt_integer("NewTotalAssociations") or 0
        except (FritzServiceError, FritzActionError, FritzInternalError) as e:
            logger.warning(
                "failed to read WiFi associations from %s on %s: %s",
                service,
                device.host,
                str(e),
            )
            return
        for client_index in range(total):
            try:
                info = read_action(
                    device,
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
            with self._item(f"WLAN {index + 1} client {client_index}"):
                labels = [
                    device.serial,
                    device.friendly_name,
                    self.WIFI_NAMES[index],
                    info.text("NewAssociatedDeviceMACAddress"),
                    # A client may not have an address yet.
                    info.text("NewAssociatedDeviceIPAddress", ""),
                ]
                self._add("signal", labels, self.issues.num(info, "NewX_AVM-DE_SignalStrength"))
                self._add("speed", labels, self.issues.num(info, "NewX_AVM-DE_Speed"))

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["signal"]
        yield self.metrics["speed"]


class _MeshLink(NamedTuple):
    labels: list[str]  # node, peer, type, interface
    available: float | None
    cur_rx: float | None
    cur_tx: float | None
    max_rx: float | None
    max_tx: float | None


class _MeshParser:
    """Extract the backhaul links (mesh node <-> mesh node) from the mesh topology.

    An unreadable node, interface or link is skipped and an unreadable rate
    becomes ``None``, both recorded in ``issues``; a topology of the wrong
    shape raises :class:`~fritzexporter.reply.DataError`.
    """

    def __init__(self, topology: object, issues: Issues) -> None:
        self.issues = issues
        self.names: dict[str, str] = {}  # uid -> device name, for every node
        self.meshed: dict[str, Reply] = {}  # uid -> node record, for mesh nodes
        for index, node in issues.objects(Reply(topology, "mesh topology"), "nodes"):
            with issues.item(f"mesh node {index}"):
                uid = node.opt_text("uid")
                if uid is not None:
                    self.names[uid] = node.text("device_name", "n/a")
                    if node.opt_flag("is_meshed"):
                        self.meshed[uid] = node
        self.seen: set[str] = set()

    def links(self) -> list[_MeshLink]:
        links: list[_MeshLink] = []
        # In the device's order: the first endpoint seen gives the type and
        # interface labels of a link, so the order must not change.
        for uid, node in self.meshed.items():
            with self.issues.item(f"mesh node {uid}"):
                for index, interface in self.issues.objects(node, "node_interfaces"):
                    with self.issues.item(f"mesh node {uid} interface {index}"):
                        links.extend(self._interface_links(interface, f"{uid}/{index}"))
        return links

    def _interface_links(self, interface: Reply, where: str) -> list[_MeshLink]:
        link_type = interface.text("type", "")
        interface_name = interface.text("name", "")
        links: list[_MeshLink] = []
        for index, link in self.issues.objects(interface, "node_links"):
            with self.issues.item(f"mesh link {where}/{index}"):
                uid = link.opt_text("uid")
                n1 = link.opt_text("node_1_uid")
                n2 = link.opt_text("node_2_uid")
                if uid is None or n1 is None or n2 is None:
                    continue
                if self._is_new_backhaul(uid, n1, n2):
                    links.append(
                        _MeshLink(
                            labels=[
                                self.names[n1],
                                self.names[n2],
                                link_type,
                                interface_name,
                            ],
                            available=self.issues.equals(link, "state", "CONNECTED"),
                            cur_rx=self.issues.num_or(link, "cur_data_rate_rx", 0.0),
                            cur_tx=self.issues.num_or(link, "cur_data_rate_tx", 0.0),
                            max_rx=self.issues.num_or(link, "max_data_rate_rx", 0.0),
                            max_tx=self.issues.num_or(link, "max_data_rate_tx", 0.0),
                        )
                    )
        return links

    def _is_new_backhaul(self, uid: str, n1: str, n2: str) -> bool:
        """Only backhaul (mesh node <-> mesh node), each physical link once.

        AVM lists each physical link once under each endpoint's own node record,
        with identical uid and data both times, so links are deduped by uid. A
        node pair can still have several concurrent links of the same type (e.g.
        2.4GHz + 5GHz backhaul); those have distinct uids and stay separate,
        told apart by the ``interface`` label.
        """
        if uid in self.seen or n1 not in self.meshed or n2 not in self.meshed:
            return False
        self.seen.add(uid)
        return True


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
            topology = FritzHosts(fc=device.fc).get_mesh_topology()
        except FritzActionError:
            # Only the mesh master can serve the topology; every other node answers
            # "Device has no access to topology information" (404). That is the normal
            # case for mesh slaves/repeaters, so log it quietly and skip mesh metrics
            # for this device — do NOT mark it unavailable.
            logger.debug("No mesh topology available from %s (not the mesh master)", device.host)
            return
        except FritzConnectionException, RequestException:
            # The mesh list is fetched over HTTP; a transient failure should not
            # mark the whole device unavailable — just skip mesh metrics this cycle.
            logger.warning("Failed to retrieve mesh topology from %s", device.host)
            return

        for link in _MeshParser(topology, self.issues).links():
            base = [device.serial, device.friendly_name, *link.labels]
            self._add("available", base, link.available)
            self._add("datarate", [*base, "rx"], link.cur_rx)
            self._add("datarate", [*base, "tx"], link.cur_tx)
            self._add("maxdatarate", [*base, "rx"], link.max_rx)
            self._add("maxdatarate", [*base, "tx"], link.max_tx)

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
        num_hosts = read_action(device, "Hosts1", "GetHostNumberOfEntries").integer(
            "NewHostNumberOfEntries"
        )
        logger.debug(
            "Fetching host information for device serial %s (hosts found: %s)",
            device.serial,
            num_hosts,
        )
        for host_index in range(num_hosts):
            logger.debug("Fetching generic host information for host number %s", host_index)
            try:
                host_result = read_action(
                    device, "Hosts1", "GetGenericHostEntry", NewIndex=host_index
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
            with self._item(f"host {host_index}"):
                self._collect_host(device, host_index, host_result)

    def _collect_host(self, device: FritzDevice, host_index: int, host_result: Reply) -> None:
        # A host may have no address or name; "" is passed on as the label.
        host_ip = host_result.text("NewIPAddress", "")
        host_mac = host_result.text("NewMACAddress", "")
        host_name = host_result.text("NewHostName", "")
        host_interface = host_port = host_model = "n/a"
        host_speed: float | None = 0.0
        if host_ip != "":
            logger.debug(
                "Fetching extended AVM host information for host number %s by IP %s",
                host_index,
                host_ip,
            )
            try:
                avm_host_result = read_action(
                    device, "Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP", NewIPAddress=host_ip
                )
            except FritzLookUpError:
                # The host's IP left the host table between GetGenericHostEntry
                # and this lookup (a client disconnected or its lease was
                # reassigned mid-scan), so the box reports UPnP errorCode 714,
                # NoSuchEntryInArray. This is a benign race on the DHCP server's
                # host table, not a device outage: fall back to "n/a" extended
                # info for this host and keep scanning. Letting it bubble up
                # would flip the whole device to unreachable for this cycle.
                logger.debug(
                    "Host %s (IP %s) left the host table during scan of device "
                    "serial %s; using n/a extended info for this host",
                    host_index,
                    host_ip,
                    device.serial,
                )
            else:
                host_interface = avm_host_result.text("NewInterfaceType", "")
                host_port = avm_host_result.text("NewX_AVM-DE_Port", "")
                host_model = avm_host_result.text("NewX_AVM-DE_Model", "")
                host_speed = self.issues.num_or(avm_host_result, "NewX_AVM-DE_Speed", 0.0)
        else:
            logger.debug(
                "Unable to fetch extended AVM host information for host number %s: no IP found",
                host_index,
            )
        labels = [
            device.serial,
            device.friendly_name,
            host_ip,
            host_mac,
            host_name,
            host_interface,
            host_port,
            host_model,
        ]
        self._add("hostactive", labels, self.issues.flag(host_result, "NewActive"))
        self._add("hostspeed", labels, host_speed)

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
        self._add("multimeter_power", labels, _div(powermeter["power"], 1000))
        self._add("multimeter_energy", labels, powermeter["energy"])

    def _collect_temperature(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        temperature = ha_device["temperature"]
        if temperature is None:
            return
        self._add("temperature", labels, _div(temperature["celsius"], 10))
        self._add("temperature_offset", labels, _div(temperature["offset"], 10))

    def _collect_switch(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        switch = ha_device["switch"]
        if switch is None:
            return
        # Unknown states or modes (new firmware) skip just their sample.
        self._add("switch_state", labels, self._HTTP_SWITCH_STATE_MAP.get(switch["state"]))
        self._add("switch_mode", labels, self._HTTP_SWITCH_MODE_MAP.get(switch["mode"]))
        if switch["lock"] is not None:
            self._add("switch_lock", labels, int(switch["lock"] == "1"))

    def _collect_heater(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        hkr = ha_device["hkr"]
        if hkr is None:
            return
        # HKR temperatures from the AHA HTTP API are reported in half-degree
        # steps, unlike the plain <temperature> element (tenths of a degree).
        self._add("heater_temperature", labels, _div(hkr["tist"], 2))
        self._add("heater_set_temperature", labels, _div(hkr["tsoll"], 2))
        self._add("heater_reduced_temperature", labels, _div(hkr["absenk"], 2))
        self._add("heater_comfort_temperature", labels, _div(hkr["komfort"], 2))

    def _collect_heater_valve_state(self, device: FritzDevice, ain: str, labels: list[str]) -> None:
        # The valve/ventil status fields are not exposed by the AHA HTTP API's
        # getdevicelistinfos response, so they still require one TR-064 call
        # per thermostat (identified by AIN, not by enumeration index).
        try:
            ha_result = read_action(
                device, "X_AVM-DE_Homeauto1", "GetSpecificDeviceInfos", NewAIN=ain
            )
        except FritzArgumentError, FritzActionError, FritzArrayIndexError:
            logger.debug("Could not fetch HKR valve state for ain %s, skipping", ain)
            return

        for metric, key in (
            ("heater_valve_set_state", "NewHkrSetVentilStatus"),
            ("heater_reduced_valve_state", "NewHkrReduceVentilStatus"),
            ("heater_comfort_valve_state", "NewHkrComfortVentilStatus"),
        ):
            status = self.issues.text(ha_result, key, "")
            if not status:
                continue
            # An unknown status is a value new firmware may add, not malformed
            # data: skip just this sample, as for the switch state and mode.
            if status not in self._HKR_VALVE_MAP:
                logger.debug("Unknown valve status %s for ain %s, skipping", key, ain)
                continue
            self._add(metric, labels, self._HKR_VALVE_MAP[status])

    def _collect_battery(self, ha_device: dict[str, Any], labels: list[str]) -> None:
        self._add("battery_level", labels, ha_device["battery_level"])
        if ha_device["battery_low"] is not None:
            self._add("battery_low", labels, int(ha_device["battery_low"] == "1"))

    def _generate_metric_values(self, device: FritzDevice) -> None:
        # The AHA interface is part of the web UI, not TR-064: use the web UI
        # client, which targets the right port for local TLS and remote access.
        if not device.webui_client:
            logger.debug("No web UI client on device %s, skipping", device.host)
            return

        devicelist_xml = device.webui_client.fetch_aha("getdevicelistinfos")
        for ha_device in parse_aha_devicelist_xml(devicelist_xml, self.issues):
            labels = self._build_ha_labels(device, ha_device)

            self._add("devicepresent", labels, 2 if ha_device["present"] else 0)
            self._collect_multimeter(ha_device, labels)
            self._collect_temperature(ha_device, labels)
            self._collect_switch(ha_device, labels)
            self._collect_heater(ha_device, labels)
            self._collect_battery(ha_device, labels)
            if ha_device["hkr"] is not None:
                with self._item(f"valve state of {ha_device['ain']}"):
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


class WanDocsisCable(FritzCapability):
    """DOCSIS cable channel statistics from the Fritz!Box web UI.

    The TR-064 API does not expose DOCSIS channel data on AVM cable boxes.
    This capability is auto-detected on cable boxes (``NewWANAccessType`` is
    ``"Cable"`` or ``"X_AVM-DE_Cable"``) and reads the data from the internal
    web endpoint ``data.lua?page=docInfo``, the same one the Fritz!Box web UI
    uses. It requires a web login with the configured credentials.
    """

    def __init__(self) -> None:
        super().__init__()
        # Not auto-detected via TR-064 service/action presence; presence is
        # determined by the WAN access type reported by the box.
        self.present = False

    def check_capability(self, device: FritzDevice) -> None:
        # The base implementation would set present = all([]) = True because
        # there are no TR-064 requirements, which would wrongly enable DOCSIS
        # collection on every device. Instead, probe the WAN access type: cable
        # boxes report NewWANAccessType == "Cable" (WANCommonIFC1) or
        # "X_AVM-DE_Cable" (WANCommonInterfaceConfig1).
        try:
            access_type = read_action(
                device, "WANCommonInterfaceConfig1", "GetCommonLinkProperties"
            ).opt_text("NewWANAccessType")
        except (
            FritzServiceError,
            FritzActionError,
            FritzInternalError,
            FritzArgumentError,
            FritzConnectionException,
            DataError,
        ):
            logger.debug("No WAN access type info on %s, DOCSIS capability disabled", device.host)
            self.present = False
            return

        self.present = access_type in ("Cable", "X_AVM-DE_Cable")
        logger.debug(
            "Capability %s set to %s on device %s",
            type(self).__name__,
            self.present,
            device.host,
        )

    def create_metrics(self) -> None:
        self.metrics["power"] = GaugeMetricFamily(
            "fritz_docsis_power",
            "DOCSIS channel signal power level",
            labels=["serial", "friendly_name", "direction", "channel_id", "standard"],
            unit="dBmV",
        )
        self.metrics["mer"] = GaugeMetricFamily(
            "fritz_docsis_mer",
            "DOCSIS 3.1 downstream Modulation Error Ratio",
            labels=["serial", "friendly_name", "channel_id", "standard"],
            unit="dB",
        )
        self.metrics["mse"] = GaugeMetricFamily(
            "fritz_docsis_mse",
            "DOCSIS 3.0 downstream Mean Squared Error",
            labels=["serial", "friendly_name", "channel_id", "standard"],
            unit="dB",
        )
        self.metrics["corrected_errors"] = CounterMetricFamily(
            "fritz_docsis_corrected_errors_total",
            "DOCSIS downstream corrected codeword errors",
            labels=["serial", "friendly_name", "channel_id", "standard"],
        )
        self.metrics["uncorrected_errors"] = CounterMetricFamily(
            "fritz_docsis_uncorrected_errors_total",
            "DOCSIS downstream uncorrected codeword errors",
            labels=["serial", "friendly_name", "channel_id", "standard"],
        )
        self.metrics["latency"] = GaugeMetricFamily(
            "fritz_docsis_latency_ms",
            "DOCSIS downstream channel latency",
            labels=["serial", "friendly_name", "channel_id", "standard"],
            unit="ms",
        )
        self.metrics["info"] = GaugeMetricFamily(
            "fritz_docsis_channel_info",
            "DOCSIS channel information (always 1 if present)",
            labels=[
                "serial",
                "friendly_name",
                "direction",
                "channel_id",
                "standard",
                "modulation",
                "frequency",
            ],
        )

    def _emit_info(
        self, labels: list[str], direction: str, ch: DownstreamChannel | UpstreamChannel
    ) -> None:
        """The info sample, whose labels are modulation and frequency."""
        if ch["modulation"] is None or ch["frequency"] is None:
            return
        self._add(
            "info",
            [
                *labels,
                direction,
                str(ch["channel_id"]),
                ch["standard"],
                ch["modulation"],
                ch["frequency"],
            ],
            1,
        )

    def _emit_downstream_metrics(self, labels: list[str], ch: DownstreamChannel) -> None:
        """Emit the quality metrics for one DOCSIS downstream channel."""
        channel_labels = [*labels, str(ch["channel_id"]), ch["standard"]]
        self._add(
            "power",
            [*labels, "downstream", str(ch["channel_id"]), ch["standard"]],
            ch["power_dbmv"],
        )
        self._add("mer", channel_labels, ch["mer_db"])
        self._add("mse", channel_labels, ch["mse_db"])
        self._add("corrected_errors", channel_labels, ch["corrected_errors"])
        self._add("uncorrected_errors", channel_labels, ch["uncorrected_errors"])
        self._add("latency", channel_labels, ch["latency_ms"])
        self._emit_info(labels, "downstream", ch)

    def _emit_upstream_metrics(self, labels: list[str], ch: UpstreamChannel) -> None:
        """Emit the metrics for one DOCSIS upstream channel."""
        self._add(
            "power", [*labels, "upstream", str(ch["channel_id"]), ch["standard"]], ch["power_dbmv"]
        )
        self._emit_info(labels, "upstream", ch)

    def _generate_metric_values(self, device: FritzDevice) -> None:
        if not device.webui_client:
            logger.debug("No web UI client on device %s, skipping", device.host)
            return

        data = parse_docsis_response(device.webui_client.fetch_page(DOC_INFO_PAGE), self.issues)
        labels = [device.serial, device.friendly_name]

        for ch in data["downstream"]:
            self._emit_downstream_metrics(labels, ch)

        for ch in data["upstream"]:
            self._emit_upstream_metrics(labels, ch)

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["power"]
        yield self.metrics["mer"]
        yield self.metrics["mse"]
        yield self.metrics["corrected_errors"]
        yield self.metrics["uncorrected_errors"]
        yield self.metrics["latency"]
        yield self.metrics["info"]


class WanSegmentUtilization(FritzCapability):
    """Shared network-segment utilization from the Fritz!Box REST API.

    The Fritz!OS REST endpoint ``/api/v0/monitor/segment/<n>`` exposes
    per-sample utilization of the shared medium, split into the traffic this
    box generates (``own``) and the total on the segment (``total``, including
    all other subscribers), for downstream and upstream. On cable boxes this
    is the shared coax segment; the endpoint itself is not cable-specific and
    is available on any Fritz!OS 7+ box.

    Segment ``0`` covers the last hour at minute granularity (60 one-minute
    averages). We expose only the *newest* sample of each series — the last
    element of each list — aligned by the shared ``lastSampleTime``.

    Auto-detected on any box exposing the common WAN interface service (the
    REST API requires Fritz!OS 7+); the data is fetched through the shared
    web UI client.
    """

    def __init__(self) -> None:
        super().__init__()
        # The REST API is available on any Fritz!OS 7+ box, independent of
        # the WAN access type (cable/DSL/fiber). Presence is determined by the
        # common WAN interface service, which all modern boxes expose.
        self.requirements.append(("WANCommonInterfaceConfig1", "GetCommonLinkProperties"))

    def create_metrics(self) -> None:
        self.metrics["utilization"] = GaugeMetricFamily(
            "fritz_cable_segment_utilization_percent",
            "Shared cable segment utilization of the newest sample",
            labels=["serial", "friendly_name", "direction", "scope"],
            unit="percent",
        )
        self.metrics["sample_age"] = GaugeMetricFamily(
            "fritz_cable_segment_sample_timestamp_seconds",
            "Unix timestamp of the newest shared-segment utilization sample",
            labels=["serial", "friendly_name"],
            unit="seconds",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        if not device.webui_client:
            logger.debug("No web UI client on device %s, skipping", device.host)
            return

        data = parse_monitor_segment(
            device.webui_client.fetch_api("/api/v0/monitor/segment/0"), self.issues
        )
        labels = [device.serial, device.friendly_name]

        if data["last_sample_time"] is not None:
            self._add("sample_age", labels, data["last_sample_time"])

        # "own" -> what our traffic contributes, "total" -> whole shared medium.
        for series in data["series"]:
            for direction in ("downstream", "upstream"):
                samples = series[direction]
                if samples:
                    self._add("utilization", [*labels, direction, series["type"]], samples[-1])

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["utilization"]
        yield self.metrics["sample_age"]


class WanConnectionStatus(FritzCapability):
    """Per-connection IPv4/IPv6 uptime and connection state from the Fritz!Box REST API.

    The TR-064 API only exposes uptime/state for the single *active* connection
    of the supported interface types (PPP, DSL, ...). The Fritz!OS REST
    endpoint ``/api/v0/generic/connections`` instead reports every configured
    WAN connection — including disabled fallbacks — with per-stack fields:
    ``ip4_uptime``/``ip6_uptime`` (seconds) and ``ip4_connstatus``/
    ``ip6_connstatus`` (``"connected"``, ``"disabled"``, ``"connecting"``, ...).

    Uptimes are exposed as counters (they reset on each reconnect, like the
    PPP uptime metric), and the state as a gauge whose value is ``1`` when the
    stack reports ``"connected"`` and ``0`` for any other state
    (``"disabled"``, ``"connecting"``, ...). The state string is kept as the
    ``state`` label so callers can disambiguate the non-connected cases.

    Auto-detected on any box exposing the common WAN interface service (the
    REST API requires Fritz!OS 7+); the data is fetched through the shared
    web UI client.
    """

    def __init__(self) -> None:
        super().__init__()
        # The REST API is available on any Fritz!OS 7+ box, independent of
        # the WAN access type (cable/DSL/fiber). Presence is determined by the
        # common WAN interface service, which all modern boxes expose.
        self.requirements.append(("WANCommonInterfaceConfig1", "GetCommonLinkProperties"))

    def create_metrics(self) -> None:
        self.metrics["uptime"] = CounterMetricFamily(
            "fritz_wan_connection_uptime",
            "Per-stack uptime of a WAN connection in seconds (resets on reconnect)",
            labels=[
                "serial",
                "friendly_name",
                "connection",
                "connection_name",
                "media_type",
                "ip_address",
                "stack",
            ],
            unit="seconds",
        )
        self.metrics["status"] = GaugeMetricFamily(
            "fritz_wan_connection_status",
            "Per-stack connection state of a WAN connection "
            "(1 when connected, 0 otherwise; state in label)",
            labels=[
                "serial",
                "friendly_name",
                "connection",
                "connection_name",
                "media_type",
                "ip_address",
                "stack",
                "state",
            ],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        if not device.webui_client:
            logger.debug("No web UI client on device %s, skipping", device.host)
            return

        raw = device.webui_client.fetch_api("/api/v0/generic/connections")
        labels = [device.serial, device.friendly_name]
        for conn in parse_connections_response(raw, self.issues):
            base_labels = [*labels, conn["uid"], conn["name"], conn["media_type"]]
            for stack, uptime_key, status_key, ip_key in (
                ("ipv4", "ip4_uptime", "ip4_connstatus", "ip4_addr"),
                ("ipv6", "ip6_uptime", "ip6_connstatus", "ip6_addr"),
            ):
                self._add("uptime", [*base_labels, conn[ip_key], stack], conn[uptime_key])
                state = conn[status_key]
                if state:
                    self._add(
                        "status",
                        [*base_labels, conn[ip_key], stack, state],
                        int(state == "connected"),
                    )

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["uptime"]
        yield self.metrics["status"]


class ManagementServerInfo(FritzCapability):
    """TR-069 management server (ACS) state.

    Only the ACS host name and the non-sensitive flags are exported. The ACS URL
    path, the user names and the connection request URL are credentials or
    identifiers of the subscriber and are deliberately never read out.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("ManagementServer1", "GetInfo"))

    def create_metrics(self) -> None:
        labels = ["serial", "friendly_name"]
        self.metrics["acs_info"] = GaugeMetricFamily(
            "fritz_tr069_acs_info",
            "TR-069 management server host name (always 1 if present)",
            labels=[*labels, "acs_host"],
        )
        self.metrics["inform_enabled"] = GaugeMetricFamily(
            "fritz_tr069_periodic_inform_enabled",
            "Whether the device periodically informs the TR-069 management server",
            labels=labels,
        )
        self.metrics["inform_interval"] = GaugeMetricFamily(
            "fritz_tr069_periodic_inform_interval",
            "Interval of the periodic TR-069 inform",
            labels=labels,
            unit="seconds",
        )
        self.metrics["upgrades_managed"] = GaugeMetricFamily(
            "fritz_tr069_upgrades_managed",
            "Whether firmware upgrades are managed by the TR-069 management server",
            labels=labels,
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "ManagementServer1", "GetInfo")
        labels = [device.serial, device.friendly_name]
        with self._item("ACS host"):
            acs_host = _url_host(result.text("NewURL", ""), result.where("NewURL"))
            if acs_host:
                self._add("acs_info", [*labels, acs_host], 1)
        self._add("inform_enabled", labels, self.issues.flag(result, "NewPeriodicInformEnable"))
        self._add("inform_interval", labels, self.issues.num(result, "NewPeriodicInformInterval"))
        self._add("upgrades_managed", labels, self.issues.flag(result, "NewUpgradesManaged"))

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["acs_info"]
        yield self.metrics["inform_enabled"]
        yield self.metrics["inform_interval"]
        yield self.metrics["upgrades_managed"]


class UspControllers(FritzCapability):
    """Enable flags of the USP (TR-369) controllers configured on the device.

    Controller endpoint IDs, host names and credentials are not exported.
    """

    def __init__(self) -> None:
        super().__init__()
        # GetUSPControllerByIndex needs an argument, so it cannot be a requirement.
        self.requirements.append(("X_AVM-DE_USPController1", "GetUSPControllerNumberOfEntries"))
        self.requirements.append(("X_AVM-DE_USPController1", "GetUSPMyFRITZEnable"))

    def create_metrics(self) -> None:
        self.metrics["controller_enabled"] = GaugeMetricFamily(
            "fritz_usp_controller_enabled",
            "Whether a USP controller is enabled",
            labels=["serial", "friendly_name", "index"],
        )
        self.metrics["myfritz_enabled"] = GaugeMetricFamily(
            "fritz_usp_myfritz_enabled",
            "Whether the MyFRITZ USP controller is enabled",
            labels=["serial", "friendly_name"],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        labels = [device.serial, device.friendly_name]
        service = "X_AVM-DE_USPController1"
        # The loop depends on the count, so it is read strictly.
        count = read_action(device, service, "GetUSPControllerNumberOfEntries").integer(
            "NewUSPControllerNumberOfEntries"
        )
        for index in range(count):
            try:
                controller = read_action(device, service, "GetUSPControllerByIndex", NewIndex=index)
            except FritzArrayIndexError:
                continue
            self._add(
                "controller_enabled",
                [*labels, str(index)],
                self.issues.flag(controller, "NewEnable"),
            )
        myfritz = read_action(device, service, "GetUSPMyFRITZEnable")
        self._add("myfritz_enabled", labels, self.issues.flag(myfritz, "NewUSPMyFRITZEnabled"))

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["controller_enabled"]
        yield self.metrics["myfritz_enabled"]


class TelephonyNumbers(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_VoIP1", "X_AVM-DE_GetNumberOfNumbers"))

    def create_metrics(self) -> None:
        self.metrics["numbers"] = GaugeMetricFamily(
            "fritz_telephony_numbers",
            "Number of telephone numbers configured on the device",
            labels=["serial", "friendly_name"],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "X_VoIP1", "X_AVM-DE_GetNumberOfNumbers")
        self._add(
            "numbers",
            [device.serial, device.friendly_name],
            self.issues.num(result, "NewNumberOfNumbers"),
        )

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["numbers"]


class WanIPv6Prefix(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANIPConn1", "X_AVM_DE_GetIPv6Prefix"))

    def create_metrics(self) -> None:
        self.metrics["prefix"] = GaugeMetricFamily(
            "fritz_wan_ipv6_prefix_info",
            "Delegated IPv6 prefix of the WAN connection (always 1 if present)",
            labels=["serial", "friendly_name", "prefix", "prefix_length"],
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "WANIPConn1", "X_AVM_DE_GetIPv6Prefix")
        prefix = result.opt_text("NewIPv6Prefix")
        if prefix is None:
            return
        self._add(
            "prefix",
            [device.serial, device.friendly_name, prefix, result.text("NewPrefixLength")],
            1,
        )

    def _get_metric_values(
        self,
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield self.metrics["prefix"]


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
