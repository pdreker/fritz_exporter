from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Generator, ItemsView, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, cast
from urllib.parse import urlparse

from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzArrayIndexError,
    FritzConnectionException,
    FritzInternalError,
    FritzLookUpError,
    FritzServiceError,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from fritzexporter.fritz_aha import AhaDevice, parse_aha_devicelist_xml
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
from fritzexporter.fritz_webui import FritzWebUiClient, FritzWebUiError
from fritzexporter.reply import DataError, Issues, Reply, read_action
from fritzexporter.tr064_remote import TRANSPORT_ERRORS, transport_failure_text

if TYPE_CHECKING:
    from fritzexporter.fritzdevice import FritzDevice

logger = logging.getLogger("fritzexporter.fritzcapability")

_PACKAGE_DIR = Path(__file__).parent

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


class ProblemLevel(StrEnum):
    """How much of a capability's data the last scrape of a device skipped."""

    PARTIAL = "partial"  # some values or items
    SKIPPED = "skipped"  # the whole capability
    ERROR = "error"  # the whole capability, because of a bug in the exporter


class _Problem:
    """A problem of one capability on one device, while it lasts."""

    def __init__(self, level: ProblemLevel) -> None:
        self.level = level
        self.since = datetime.now(tz=UTC)
        self.scrapes = 0
        self.reminded = time.monotonic()


def _origin(exc: BaseException) -> str:
    """``file:line in function`` of the innermost frame of ``exc`` in the exporter.

    Errors raised inside a library (fritzconnection) are placed at the exporter's
    call into it, which tells more.
    """
    tb = exc.__traceback__
    if tb is None:
        return "unknown"
    frame = tb
    while tb is not None:
        if Path(tb.tb_frame.f_code.co_filename).parent == _PACKAGE_DIR:
            frame = tb
        tb = tb.tb_next
    code = frame.tb_frame.f_code
    return f"{Path(code.co_filename).name}:{frame.tb_lineno} in {code.co_name}"


def _describe(exc: BaseException) -> str:
    """A log-safe description: DataError texts never hold values, other texts may."""
    if isinstance(exc, DataError | FritzWebUiError):
        return str(exc)
    return f"{type(exc).__name__} at {_origin(exc)}"


class ProblemState:
    """Holds the current problem of one source of data on one device."""

    def __init__(self) -> None:
        self.problem: _Problem | None = None


def report_problem(  # noqa: PLR0913
    state: ProblemState | FritzCapability,
    name: str,
    host: str,
    *,
    level: ProblemLevel | None,
    headline: str = "",
    details: str = "",
) -> None:
    """Record the problem of the last scrape in ``state`` and log it.

    A problem is logged as a warning when it appears or changes level, reminded of
    every ``PROBLEM_REMINDER_INTERVAL`` while it lasts, logged at debug level on the
    other scrapes, and with an info line when it is gone (``level`` is ``None``). A
    bug (``ProblemLevel.ERROR``) is logged by the caller on every scrape.
    """
    if level is None:
        if state.problem is not None:
            logger.info(
                "Reading %s data from %s works again (problem since %s, %d scrapes)",
                name,
                host,
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


# A thermostat setting of 253 means "off" (valve closed), 254 "on" (valve open):
# states, not temperatures.
_HKR_OFF_ON = (253, 254)


def _hkr_setting(value: int | None) -> float | None:
    """A thermostat set temperature in degrees; none while it is switched off or on."""
    return None if value in _HKR_OFF_ON else _div(value, 2)


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
    *,
    arguments: dict[str, Any] | None = None,
    confirms: tuple[type[Exception], ...] = (),
) -> bool:
    """Call a TR-064 action to confirm the device really supports it.

    Some boxes advertise services or actions they do not implement and answer
    with UPnP error 401 (Invalid Action). That is expected, so it is logged at
    INFO; another error reply is logged as a warning. Both return False. An
    error in ``confirms`` (e.g. "no such entry" for a probe with a made-up
    index) shows that the action works. A device that does not answer fails
    its setup, which is tried again later.
    """
    try:
        device.fc.call_action(service, action, arguments=arguments or {})
    except confirms:
        return True
    except FritzServiceError, FritzActionError:
        logger.info(
            "device %s does not support service %s, action %s; disabling its metrics",
            device.log_name,
            service,
            action,
        )
        return False
    except TRANSPORT_ERRORS:
        raise
    except FritzConnectionException as e:
        logger.warning(
            "disabling metrics of %s at service %s, action %s - "
            "fritzconnection.call_action returned %s",
            device.log_name,
            service,
            action,
            type(e).__name__,
        )
        return False
    return True


class FritzCapability(ABC):
    subclasses: ClassVar[list[type[FritzCapability]]] = []
    # Reads (some) data from the web interface; the device then gets a web client.
    uses_webui: ClassVar[bool] = False

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
        self.present = self.enabled(device) and self._supported(device, self.requirements)
        logger.debug(
            "Capability %s set to %s on device %s",
            type(self).__name__,
            self.present,
            device.log_name,
        )

    def enabled(self, device: FritzDevice) -> bool:  # noqa: ARG002
        """Whether the configuration asks for this capability (host_info, ...)."""
        return True

    def _supported(self, device: FritzDevice, requirements: list[tuple[str, str]]) -> bool:
        """Whether the device advertises all ``requirements`` and they answer.

        Some boxes advertise services or actions they do not support, so each one
        is called once.
        """
        return all(
            service in device.fc.services and action in device.fc.services[service].actions
            for service, action in requirements
        ) and all(self._probe(device, service, action) for service, action in requirements)

    def _probe(self, device: FritzDevice, service: str, action: str) -> bool:
        return probe_action(device, service, action)

    def _enumerate(  # noqa: PLR0913
        self,
        device: FritzDevice,
        service: str,
        action: str,
        count: int,
        *,
        index_argument: str,
        item: str,
        ends: tuple[type[Exception], ...] = (FritzArrayIndexError,),
    ) -> Iterator[tuple[int, Reply]]:
        """Read entries ``0 .. count-1`` of a table, by index.

        The table can shrink between counting and reading (a host leaves, a
        WiFi client disconnects); the device then answers with one of ``ends``
        ("invalid array index") and the scan ends with the entries read so far.
        Another error reply skips that entry, recorded as ``"<item> <index>"``,
        as ``_item`` would.
        """
        for index in range(count):
            try:
                entry = read_action(device, service, action, **{index_argument: index})
            except TRANSPORT_ERRORS:
                raise
            except ends:
                logger.debug(
                    "%s.%s on %s: the table shrank to %d entries during the scan",
                    service,
                    action,
                    device.log_name,
                    index,
                )
                return
            except FritzConnectionException as e:
                self.issues.messages.append(f"{item} {index} skipped: {type(e).__name__}")
                continue
            yield index, entry

    def get_metrics(
        self, devices: list[FritzDevice], name: str
    ) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        self.create_metrics()
        for device in devices:
            logger.debug(
                "Fetching %s metrics for %s: %s",
                name,
                device.log_name,
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
        * The device answers a TR-064 request with an error: handled like a
          reply that cannot be read.
        * The device does not answer: its samples of this capability are dropped
          and it is marked unavailable for this cycle.
        """
        counts = self._sample_counts()
        self.issues = Issues()
        # The problem state lives on the device's own instance of this capability.
        state = device.capabilities[name]
        try:
            self._generate_metric_values(device)
            _check_samples(self.metrics, counts)
        except TRANSPORT_ERRORS as e:
            self._rollback(counts)
            logger.warning(
                "Device %s is unreachable, skipping %s metrics for this collection cycle: %s",
                device.log_name,
                name,
                transport_failure_text(e),
            )
            device.available = False
        except (FritzConnectionException, FritzWebUiError, DataError) as e:
            self._rollback(counts)
            report_problem(
                state,
                name,
                device.log_name,
                level=ProblemLevel.SKIPPED,
                headline=f"Could not read {name} data from {device.log_name}, skipping it",
                details=_describe(e),
            )
        except _BUG_ERRORS:
            self._rollback(counts)
            logger.exception(
                "Bug while collecting %s data from %s, skipping it for this cycle; "
                "please report this",
                name,
                device.log_name,
            )
            report_problem(state, name, device.log_name, level=ProblemLevel.ERROR)
        else:
            if self.issues.messages:
                report_problem(
                    state,
                    name,
                    device.log_name,
                    level=ProblemLevel.PARTIAL,
                    headline=f"Skipped part of the {name} data from {device.log_name}",
                    details="; ".join(self.issues.messages),
                )
            else:
                report_problem(state, name, device.log_name, level=None)

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
    def _item(self, what: str) -> Generator[None]:
        """Collect one item (a host, a channel, ...); if it cannot be read, skip it.

        An error reply to a TR-064 request for the item skips it as well; a
        device that does not answer is left to the capability (``_collect_device``).
        """
        counts = self._sample_counts()
        try:
            with self.issues.item(what, on_skip=lambda: self._rollback(counts)):
                yield
        except TRANSPORT_ERRORS:
            raise
        except FritzConnectionException as e:
            self._rollback(counts)
            self.issues.messages.append(f"{what} skipped: {type(e).__name__}")

    @abstractmethod
    def create_metrics(self) -> None:
        pass

    @abstractmethod
    def _generate_metric_values(self, device: FritzDevice) -> None:
        pass

    def _get_metric_values(self) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        yield from self.metrics.values()

    @staticmethod
    def _webui(device: FritzDevice) -> FritzWebUiClient:
        """The device's web client; it exists when a capability with ``uses_webui`` is present."""
        if device.webui_client is None:
            msg = "no web interface client for this device"
            raise FritzWebUiError(msg)
        return device.webui_client

    def _gauge(self, key: str, name: str, documentation: str, *labels: str, unit: str = "") -> None:
        """Create a gauge labelled by device (serial, friendly_name) and ``labels``."""
        self.metrics[key] = GaugeMetricFamily(
            name, documentation, labels=["serial", "friendly_name", *labels], unit=unit
        )

    def _counter(
        self, key: str, name: str, documentation: str, *labels: str, unit: str = ""
    ) -> None:
        """Create a counter labelled by device (serial, friendly_name) and ``labels``."""
        self.metrics[key] = CounterMetricFamily(
            name, documentation, labels=["serial", "friendly_name", *labels], unit=unit
        )


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
        self._counter("uptime", "fritz_uptime", "Device uptime", unit="seconds")
        # Model and Fritz!OS version change with an update; as labels of the
        # uptime they started a new series (and a counter reset) each time.
        self._gauge(
            "info",
            "fritz_device_info",
            "Model and Fritz!OS version of the device (always 1)",
            "modelname",
            "softwareversion",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        info = read_action(device, "DeviceInfo1", "GetInfo")
        labels = [device.serial, device.friendly_name]
        self._add("uptime", labels, self.issues.num(info, "NewUpTime"))
        with self._item("device info"):
            self._add(
                "info",
                [
                    *labels,
                    info.text("NewModelName", "n/a"),
                    info.text("NewSoftwareVersion", "n/a"),
                ],
                1,
            )


class HostNumberOfEntries(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("Hosts1", "GetHostNumberOfEntries"))

    def create_metrics(self) -> None:
        self._gauge(
            "numhosts", "fritz_known_devices", "Number of devices in hosts table", unit="count"
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "Hosts1", "GetHostNumberOfEntries")
        self._add(
            "numhosts",
            [device.serial, device.friendly_name],
            self.issues.num(result, "NewHostNumberOfEntries"),
        )


class UserInterface(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("UserInterface1", "GetInfo"))

    def create_metrics(self) -> None:
        self._gauge("update", "fritz_update_available", "Fritz!OS update available")
        self._gauge(
            "info",
            "fritz_update_info",
            "Version of the available Fritz!OS update (always 1, only while there is one)",
            "newsoftwareversion",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "UserInterface1", "GetInfo")
        labels = [device.serial, device.friendly_name]
        available = self.issues.flag(result, "NewUpgradeAvailable")
        self._add("update", labels, available)
        if available:
            with self._item("update version"):
                self._add("info", [*labels, result.text("NewX_AVM-DE_Version", "n/a")], 1)


class LanInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("LANEthernetInterfaceConfig1", "GetInfo"))

    def create_metrics(self) -> None:
        self._gauge("lanenable", "fritz_lan_status_enabled", "LAN Interface enabled")
        self._gauge("lanstatus", "fritz_lan_status", "LAN Interface status")

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "LANEthernetInterfaceConfig1", "GetInfo")
        labels = [device.serial, device.friendly_name]
        self._add("lanenable", labels, self.issues.flag(result, "NewEnable"))
        self._add("lanstatus", labels, self.issues.equals(result, "NewStatus", "Up"))


class LanInterfaceConfigStatistics(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("LANEthernetInterfaceConfig1", "GetStatistics"))

    def create_metrics(self) -> None:
        self._counter("lanbytes", "fritz_lan_data", "LAN bytes received", "direction", unit="bytes")
        self._counter(
            "lanpackets", "fritz_lan_packet", "LAN packets transmitted", "direction", unit="count"
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


class WanDSLInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANDSLInterfaceConfig1", "GetInfo"))

    def create_metrics(self) -> None:
        self._gauge("enable", "fritz_dsl_status_enabled", "DSL enabled")
        self._gauge(
            "datarate",
            "fritz_dsl_datarate",
            "DSL datarate in kbps",
            "direction",
            "type",
            unit="kbps",
        )
        self._gauge(
            "noisemargin", "fritz_dsl_noise_margin", "Noise Margin in dB", "direction", unit="dB"
        )
        self._gauge(
            "attenuation", "fritz_dsl_attenuation", "Line attenuation in dB", "direction", unit="dB"
        )
        self._gauge("status", "fritz_dsl_status", "DSL status")

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


class WanDSLInterfaceConfigAVM(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo"))

    def create_metrics(self) -> None:
        self._counter(
            "fec", "fritz_dsl_fec_errors_count", "Number of Forward Error Correction Errors"
        )
        self._counter("crc", "fritz_dsl_crc_errors_count", "Number of CRC Errors")

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo")
        labels = [device.serial, device.friendly_name]
        self._add("fec", labels, self.issues.num(result, "NewFECErrors"))
        self._add("crc", labels, self.issues.num(result, "NewCRCErrors"))


class WanFiberInterfaceConfig(FritzCapability):
    """Optical / SFP metrics from X_AVM-DE_WANFiber.GetInfo."""

    uses_webui = True

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_AVM-DE_WANFiber1", "GetInfo"))

    def create_metrics(self) -> None:
        self._gauge(
            "optical_signal",
            "fritz_fiber_optical_signal_level",
            "Current received optical signal level",
            unit="dBm",
        )
        self._gauge(
            "optical_threshold",
            "fritz_fiber_optical_threshold",
            "Optical receive power threshold",
            "bound",
            unit="dBm",
        )
        self._gauge(
            "transmit_optical",
            "fritz_fiber_transmit_optical_level",
            "Current transmit optical power level",
            unit="dBm",
        )
        self._gauge(
            "transmit_threshold",
            "fritz_fiber_transmit_power_threshold",
            "Transmit optical power threshold",
            "bound",
            unit="dBm",
        )
        self._gauge("tx_wavelength", "fritz_fiber_tx_wavelength", "Fibre TX wavelength", unit="nm")
        self._gauge(
            "info",
            "fritz_fiber_info",
            "Fibre / SFP module information (always 1 if present)",
            "fiber_mode",
            "sfp_vendor",
            "sfp_part_number",
            "sfp_serial_number",
            "sfp_type",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "X_AVM-DE_WANFiber1", "GetInfo")
        labels = [device.serial, device.friendly_name]

        # Levels and thresholds come in thousandths of a dBm. The fallback below
        # depends on both levels, so they are read strictly.
        receive: float | None = result.num("NewOpticalSignalLevel") / 1000
        transmit: float | None = result.num("NewTransmitOpticalLevel") / 1000
        if receive == 0 and transmit == 0:
            # Some boxes answer 0 for both levels over TR-064 although the web
            # UI shows real values: 0 means unknown here, so take the levels
            # from the page the web UI reads, or export none.
            receive, transmit = self._webui_levels(device)

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

    def _webui_levels(self, device: FritzDevice) -> tuple[float | None, float | None]:
        try:
            levels = parse_fiber_stats_response(
                self._webui(device).fetch_page(FIBER_STATS_PAGE), self.issues
            )
        except (FritzWebUiError, DataError) as e:
            # The other fibre metrics are still good: skip only the levels.
            self.issues.messages.append(f"optical levels from the web interface skipped: {e}")
            return None, None
        return levels["receive_dbm"], levels["transmit_dbm"]


class WanFiberGPONInfo(FritzCapability):
    """GPON identity metrics from X_AVM-DE_WANFiber.GetInfoGPON."""

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_AVM-DE_WANFiber1", "GetInfoGPON"))

    def create_metrics(self) -> None:
        self._gauge(
            "gpon_info",
            "fritz_fiber_gpon_info",
            "GPON identity information (always 1 if present)",
            "gpon_serial",
            "pon_id",
            "uni_type",
        )
        self._gauge("onu_id", "fritz_fiber_gpon_onu_id", "GPON ONU identifier")
        self._gauge("gem_ports", "fritz_fiber_gpon_gem_port_count", "Number of GPON GEM ports")

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


class WanFiberStatistics(FritzCapability):
    """Fibre link statistics from X_AVM-DE_WANFiber.GetStatistics."""

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_AVM-DE_WANFiber1", "GetStatistics"))

    def create_metrics(self) -> None:
        self._counter(
            "data", "fritz_fiber_data", "Fibre data transferred", "direction", unit="bytes"
        )
        self._counter(
            "packets", "fritz_fiber_data_packets", "Fibre packets transferred", "direction"
        )
        self._counter(
            "packet_errors", "fritz_fiber_packet_errors", "Fibre packet errors", "direction"
        )
        self._counter("multicast", "fritz_fiber_packets_multicast", "Fibre multicast packets")
        self._gauge(
            "connection_rate",
            "fritz_fiber_connection_rate",
            "Fibre connection rate as reported by the device (see docs for unit quirks)",
            "direction",
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


class WanPPPConnectionStatus(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANPPPConnection1", "GetStatusInfo"))

    def create_metrics(self) -> None:
        self._counter(
            "uptime", "fritz_ppp_connection_uptime", "PPP connection uptime", unit="seconds"
        )
        self._gauge("connected", "fritz_ppp_connection_state", "PPP connection state")
        self._gauge(
            "info",
            "fritz_ppp_connection_info",
            "Last error of the PPP connection (always 1)",
            "last_error",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "WANPPPConnection1", "GetStatusInfo")
        labels = [device.serial, device.friendly_name]
        self._add("uptime", labels, self.issues.num(result, "NewUptime"))
        self._add(
            "connected", labels, self.issues.equals(result, "NewConnectionStatus", "Connected")
        )
        with self._item("PPP last error"):
            self._add("info", [*labels, result.text("NewLastConnectionError", "")], 1)


class WanCommonInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetCommonLinkProperties"))

    def create_metrics(self) -> None:
        self._gauge(
            "wanconfig",
            "fritz_wan_max_bitrate",
            "max bitrate at the physical layer",
            "direction",
            unit="bps",
        )
        self._gauge(
            "wanlinkstatus", "fritz_wan_phys_link_status", "link status at the physical layer"
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "WANCommonInterfaceConfig1", "GetCommonLinkProperties")
        # The access type is in fritz_wan_access_type_info: it changes on a mobile
        # fallback.
        labels = [device.serial, device.friendly_name]
        self._add(
            "wanconfig", [*labels, "tx"], self.issues.num(result, "NewLayer1UpstreamMaxBitRate")
        )
        self._add(
            "wanconfig", [*labels, "rx"], self.issues.num(result, "NewLayer1DownstreamMaxBitRate")
        )
        self._add(
            "wanlinkstatus", labels, self.issues.equals(result, "NewPhysicalLinkStatus", "Up")
        )


class WanCommonInterfaceDataBytes(FritzCapability):
    WAN_COMMON_INTERFACE_SERVICE: str = "WANCommonInterfaceConfig1"

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalBytesReceived"))
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalBytesSent"))

    def create_metrics(self) -> None:
        self._counter("wanbytes", "fritz_wan_data", "WAN data in bytes", "direction", unit="bytes")

    def _generate_metric_values(self, device: FritzDevice) -> None:
        service = self.WAN_COMMON_INTERFACE_SERVICE
        labels = [device.serial, device.friendly_name]
        rx = read_action(device, service, "GetTotalBytesReceived")
        tx = read_action(device, service, "GetTotalBytesSent")
        self._add("wanbytes", [*labels, "tx"], self.issues.num(tx, "NewTotalBytesSent"))
        self._add("wanbytes", [*labels, "rx"], self.issues.num(rx, "NewTotalBytesReceived"))


class WanCommonInterfaceByteRate(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonIFC1", "GetAddonInfos"))

    def create_metrics(self) -> None:
        self._gauge(
            "wanbyterate",
            "fritz_wan_datarate",
            "Current WAN data rate in bytes/s",
            "direction",
            unit="bytes",
        )
        self._gauge(
            "layer1max",
            "fritz_wan_layer1_max_bitrate",
            "Layer1 max bitrate (64-bit; correct for multi-gig fibre/cable)",
            "direction",
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


class WanCommonInterfaceDataPackets(FritzCapability):
    WAN_COMMON_INTERFACE_SERVICE: str = "WANCommonInterfaceConfig1"

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalPacketsReceived"))
        self.requirements.append(("WANCommonInterfaceConfig1", "GetTotalPacketsSent"))

    def create_metrics(self) -> None:
        self._counter(
            "wanpackets", "fritz_wan_data_packets", "WAN data in packets", "direction", unit="count"
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        service = self.WAN_COMMON_INTERFACE_SERVICE
        labels = [device.serial, device.friendly_name]
        rx = read_action(device, service, "GetTotalPacketsReceived")
        tx = read_action(device, service, "GetTotalPacketsSent")
        self._add("wanpackets", [*labels, "tx"], self.issues.num(tx, "NewTotalPacketsSent"))
        self._add("wanpackets", [*labels, "rx"], self.issues.num(rx, "NewTotalPacketsReceived"))


# WLANConfiguration1..4: what the bands are on Fritz!Boxes.
WIFI_NAMES: tuple[str, ...] = ("2.4GHz", "5GHz", "Guest", "WLAN4")


class WlanConfigurationInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.wifi_present: list[bool] = [False] * len(WIFI_NAMES)

    def check_capability(self, device: FritzDevice) -> None:
        for index in range(len(WIFI_NAMES)):
            service = f"WLANConfiguration{index + 1}"
            requirements = [
                (service, "GetInfo"),
                (service, "GetTotalAssociations"),
                (service, "GetPacketStatistics"),
            ]
            logger.debug(
                "Capability %s checking %s on %s", type(self).__name__, service, device.log_name
            )
            self.wifi_present[index] = self._supported(device, requirements)
            logger.debug(
                "Capability %s in WLAN %d set to %s on device %s",
                type(self).__name__,
                index + 1,
                self.wifi_present[index],
                device.log_name,
            )
        self.present = any(self.wifi_present)

    def create_metrics(self) -> None:
        # Keyed by the band. SSID, standard and the enabled flag change when the
        # WLAN is set up differently; as labels they started new series.
        self._gauge("wlanstatus", "fritz_wifi_status", "Status of WiFi", "wifi_index", "wifi_name")
        self._gauge("wlanenabled", "fritz_wifi_enabled", "WiFi enabled", "wifi_index", "wifi_name")
        self._gauge(
            "wlanchannel", "fritz_wifi_channel", "Channel of WiFi", "wifi_index", "wifi_name"
        )
        self._gauge(
            "wlanassocs",
            "fritz_wifi_associations",
            "Number of associations (devices) in WiFi",
            "wifi_index",
            "wifi_name",
            unit="count",
        )
        self._counter(
            "wlanpackets",
            "fritz_wifi_packets",
            "Amount of packets in WiFi",
            "wifi_index",
            "wifi_name",
            "direction",
            unit="count",
        )
        self._gauge(
            "wlaninfo",
            "fritz_wifi_info",
            "SSID and standard of a WiFi (always 1)",
            "wifi_index",
            "wifi_name",
            "ssid",
            "standard",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:

        device_wlan_cap = cast(WlanConfigurationInfo, device.capabilities[self.__class__.__name__])
        for index, wlan_present in enumerate(device_wlan_cap.wifi_present):
            logger.debug(
                "Checking WLAN %d on %s (present: %s)", index + 1, device.log_name, wlan_present
            )
            if wlan_present:
                with self._item(f"WLAN {index + 1}"):
                    self._collect_wlan(device, index)

    def _collect_wlan(self, device: FritzDevice, index: int) -> None:
        service = f"WLANConfiguration{index + 1}"
        info = read_action(device, service, "GetInfo")
        labels = [device.serial, device.friendly_name, str(index + 1), WIFI_NAMES[index]]
        self._add("wlanstatus", labels, self.issues.equals(info, "NewStatus", "Up"))
        self._add("wlanenabled", labels, self.issues.flag(info, "NewEnable"))
        self._add("wlanchannel", labels, self.issues.num(info, "NewChannel"))
        with self._item(f"WLAN {index + 1} info"):
            self._add(
                "wlaninfo",
                [*labels, info.text("NewSSID", ""), info.text("NewStandard", "")],
                1,
            )

        associations = read_action(device, service, "GetTotalAssociations")
        self._add("wlanassocs", labels, self.issues.num(associations, "NewTotalAssociations"))

        packets = read_action(device, service, "GetPacketStatistics")
        for direction, field in (("rx", "NewTotalPacketsReceived"), ("tx", "NewTotalPacketsSent")):
            self._add("wlanpackets", [*labels, direction], self.issues.num(packets, field))


class WlanAssociatedDevices(FritzCapability):
    """Per-station WiFi client metrics (signal strength + negotiated speed).

    Opt-in via the device ``wifi_client_info`` flag: it emits one time series per
    associated client (per-client cardinality), so it is disabled by default just
    like ``host_info``. Works on any device with radios — the box and (with the
    repeater availability fix) mesh repeaters alike.
    """

    def __init__(self) -> None:
        super().__init__()
        self.wifi_present: list[bool] = [False] * len(WIFI_NAMES)

    def enabled(self, device: FritzDevice) -> bool:
        return device.wifi_client_info

    def check_capability(self, device: FritzDevice) -> None:
        if not self.enabled(device):
            self.present = False
            return
        for index in range(len(WIFI_NAMES)):
            service = f"WLANConfiguration{index + 1}"
            # Only GetTotalAssociations is probed: GetGenericAssociatedDeviceInfo
            # needs an index and raises on a radio with no clients.
            self.wifi_present[index] = (
                service in device.fc.services
                and "GetGenericAssociatedDeviceInfo" in device.fc.services[service].actions
                and self._supported(device, [(service, "GetTotalAssociations")])
            )
        self.present = any(self.wifi_present)

    def create_metrics(self) -> None:
        # Keyed by the client's MAC address: its band changes when it roams and
        # its address with a new lease; as labels they started new series.
        self._gauge(
            "signal",
            "fritz_wifi_client_signal_strength",
            "Signal strength of an associated WiFi client in percent",
            "client_mac",
        )
        self._gauge(
            "speed",
            "fritz_wifi_client_speed",
            "Negotiated speed of an associated WiFi client in Mbit/s",
            "client_mac",
        )
        self._gauge(
            "info",
            "fritz_wifi_client_info",
            "Band and address of an associated WiFi client (always 1)",
            "client_mac",
            "wifi_name",
            "client_ip",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        device_cap = cast(WlanAssociatedDevices, device.capabilities[self.__class__.__name__])
        seen: set[str] = set()  # MAC addresses read in this scan
        for index, present in enumerate(device_cap.wifi_present):
            if not present:
                continue
            with self._item(f"WLAN {index + 1} clients"):
                self._collect_clients(device, index, seen)

    def _collect_clients(self, device: FritzDevice, index: int, seen: set[str]) -> None:
        service = f"WLANConfiguration{index + 1}"
        # The loop depends on the count, so a defect skips this WLAN.
        total = (
            read_action(device, service, "GetTotalAssociations").opt_integer("NewTotalAssociations")
            or 0
        )
        clients = self._enumerate(
            device,
            service,
            "GetGenericAssociatedDeviceInfo",
            total,
            index_argument="NewAssociatedDeviceIndex",
            item=f"WLAN {index + 1} client",
            # Boxes also answer these for clients that left during the scan.
            ends=(FritzArrayIndexError, FritzServiceError, FritzActionError, FritzInternalError),
        )
        for client_index, info in clients:
            with self._item(f"WLAN {index + 1} client {client_index}"):
                mac = info.text("NewAssociatedDeviceMACAddress")
                if mac in seen:
                    # The table changed during the scan and moved this client to
                    # the next index; it was read already.
                    continue
                seen.add(mac)
                labels = [device.serial, device.friendly_name, mac]
                self._add("signal", labels, self.issues.num(info, "NewX_AVM-DE_SignalStrength"))
                self._add("speed", labels, self.issues.num(info, "NewX_AVM-DE_Speed"))
                # A client may not have an address yet.
                address = info.text("NewAssociatedDeviceIPAddress", "")
                self._add("info", [*labels, WIFI_NAMES[index], address], 1)


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
        # uid -> (index, node record), for mesh nodes. Texts name nodes by index:
        # uids are device values.
        self.meshed: dict[str, tuple[int, Reply]] = {}
        for index, node in issues.objects(Reply(topology, "mesh topology"), "nodes"):
            with issues.item(f"mesh node {index}"):
                uid = node.opt_text("uid")
                if uid is not None:
                    self.names[uid] = node.text("device_name", "n/a")
                    if node.opt_flag("is_meshed"):
                        self.meshed[uid] = (index, node)
        self.seen: set[str] = set()
        # Label sets of the links so far: two links between nodes of the same
        # names on one interface would be two series with identical labels.
        self.label_sets: set[tuple[str, ...]] = set()

    def links(self) -> list[_MeshLink]:
        links: list[_MeshLink] = []
        # In the device's order: the first endpoint seen gives the type and
        # interface labels of a link, so the order must not change.
        for node_index, node in self.meshed.values():
            with self.issues.item(f"mesh node {node_index}"):
                for index, interface in self.issues.objects(node, "node_interfaces"):
                    with self.issues.item(f"mesh node {node_index} interface {index}"):
                        links.extend(self._interface_links(interface, f"{node_index}/{index}"))
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
                    labels = [self.names[n1], self.names[n2], link_type, interface_name]
                    if tuple(labels) in self.label_sets:
                        msg = (
                            "same node names and interface as another link "
                            "(give the mesh nodes different names)"
                        )
                        raise DataError(msg)
                    self.label_sets.add(tuple(labels))
                    links.append(
                        _MeshLink(
                            labels=labels,
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
        self._gauge(
            "available",
            "fritz_mesh_link_available",
            "Mesh backhaul link state (1=connected, 0=otherwise)",
            "node",
            "peer",
            "type",
            "interface",
        )

    @staticmethod
    def _mesh_list(device: FritzDevice) -> object | None:
        """The mesh topology; ``None`` from a node that is not the mesh master.

        The path comes over TR-064, where the usual rules apply (a device that
        does not answer is unreachable). The list itself is a web interface page:
        if only it fails, the device's other metrics are still good.
        """
        try:
            path = read_action(device, "Hosts1", "X_AVM-DE_GetMeshListPath").text(
                "NewX_AVM-DE_MeshListPath"
            )
        except FritzActionError:
            return None
        url = f"{device.fc.address}:{device.fc.port}{path}"
        try:
            with device.fc.session.get(url) as response:
                # Only the mesh master serves the topology; other nodes answer
                # 404 ("Device has no access to topology information").
                return response.json() if response.ok else None
        except ValueError, RecursionError:
            # Before TRANSPORT_ERRORS: requests' JSONDecodeError is a RequestException.
            msg = "mesh list: not valid JSON"
            raise DataError(msg) from None
        except TRANSPORT_ERRORS as e:
            msg = f"mesh list request failed: {transport_failure_text(e)}"
            raise FritzWebUiError(msg) from None

    def _generate_metric_values(self, device: FritzDevice) -> None:
        topology = self._mesh_list(device)
        if topology is None:
            logger.debug(
                "No mesh topology available from %s (not the mesh master)", device.log_name
            )
            return

        for link in _MeshParser(topology, self.issues).links():
            base = [device.serial, device.friendly_name, *link.labels]
            self._add("available", base, link.available)
            self._add("datarate", [*base, "rx"], link.cur_rx)
            self._add("datarate", [*base, "tx"], link.cur_tx)
            self._add("maxdatarate", [*base, "rx"], link.max_rx)
            self._add("maxdatarate", [*base, "tx"], link.max_tx)


class HostInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("Hosts1", "GetHostNumberOfEntries"))
        self.requirements.append(("Hosts1", "GetGenericHostEntry"))
        self.requirements.append(("Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP"))

    def enabled(self, device: FritzDevice) -> bool:
        return device.host_info

    def _probe(self, device: FritzDevice, service: str, action: str) -> bool:
        # Index 0 is missing in an empty host table, and the made-up address in
        # no table: the "no such entry" answers show that the actions work.
        if action == "GetGenericHostEntry":
            return probe_action(
                device, service, action, arguments={"NewIndex": 0}, confirms=(FritzArrayIndexError,)
            )
        if action == "X_AVM-DE_GetSpecificHostEntryByIP":
            return probe_action(
                device,
                service,
                action,
                arguments={"NewIPAddress": "0.0.0.0"},  # noqa: S104
                confirms=(FritzLookUpError,),
            )
        return super()._probe(device, service, action)

    def create_metrics(self) -> None:
        # Keyed by MAC address only: IP address, name and port change, and as
        # labels they made a new series each time. They are in fritz_host_info.
        self._gauge(
            "hostactive",
            "fritz_host_active",
            "Indicates that the device is curently active",
            "mac_address",
        )
        self._gauge(
            "hostspeed",
            "fritz_host_speed",
            "Connection speed of the device",
            "mac_address",
        )
        self._gauge(
            "hostinfo",
            "fritz_host_info",
            "Address, name and connection of a host (always 1)",
            "mac_address",
            "ip_address",
            "hostname",
            "interface",
            "port",
            "model",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        num_hosts = read_action(device, "Hosts1", "GetHostNumberOfEntries").integer(
            "NewHostNumberOfEntries"
        )
        logger.debug(
            "Fetching host information for %s (hosts found: %s)",
            device.log_name,
            num_hosts,
        )
        hosts = self._enumerate(
            device,
            "Hosts1",
            "GetGenericHostEntry",
            num_hosts,
            index_argument="NewIndex",
            item="host",
        )
        seen: set[str] = set()
        for host_index, host_result in hosts:
            with self._item(f"host {host_index}"):
                self._collect_host(device, host_index, host_result, seen)

    def _collect_host(
        self, device: FritzDevice, host_index: int, host_result: Reply, seen: set[str]
    ) -> None:
        host_mac = host_result.text("NewMACAddress", "")
        if not host_mac:
            # The MAC address tells hosts apart. Host tables have entries without
            # one (e.g. VPN clients); that is no defect, so they are left out quietly.
            logger.debug("Host %s on %s has no MAC address, skipping", host_index, device.log_name)
            return
        if host_mac in seen:
            msg = f"Hosts1.GetGenericHostEntry NewMACAddress of host {host_index}: listed twice"
            raise DataError(msg)
        seen.add(host_mac)
        # A host may have no address or name; "" is passed on as the label.
        host_ip = host_result.text("NewIPAddress", "")
        host_name = host_result.text("NewHostName", "")
        host_interface = host_port = host_model = "n/a"
        host_speed: float | None = 0.0
        if host_ip != "":
            logger.debug(
                "Fetching extended AVM host information for host number %s on %s by IP %s",
                host_index,
                device.log_name,
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
                    "Host %s (IP %s) left the host table during scan of %s; "
                    "using n/a extended info for this host",
                    host_index,
                    host_ip,
                    device.log_name,
                )
            else:
                host_interface = avm_host_result.text("NewInterfaceType", "")
                host_port = avm_host_result.text("NewX_AVM-DE_Port", "")
                host_model = avm_host_result.text("NewX_AVM-DE_Model", "")
                host_speed = self.issues.num_or(avm_host_result, "NewX_AVM-DE_Speed", 0.0)
        else:
            logger.debug(
                "Unable to fetch extended AVM host information for host number %s on %s: "
                "no IP found",
                host_index,
                device.log_name,
            )
        labels = [device.serial, device.friendly_name, host_mac]
        self._add("hostactive", labels, self.issues.flag(host_result, "NewActive"))
        self._add("hostspeed", labels, host_speed)
        self._add(
            "hostinfo",
            [*labels, host_ip, host_name, host_interface, host_port, host_model],
            1,
        )


class HomeAutomation(FritzCapability):
    uses_webui = True
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

    def _build_ha_labels(self, device: FritzDevice, ha_device: AhaDevice) -> list[str]:
        return [
            device.serial,
            device.friendly_name,
            ha_device["ain"],
            ha_device["device_name"],
            str(ha_device["device_id"]),
            ha_device["manufacturer"],
            ha_device["productname"],
        ]

    def _collect_multimeter(self, ha_device: AhaDevice, labels: list[str]) -> None:
        powermeter = ha_device["powermeter"]
        if powermeter is None:
            return
        self._add("multimeter_power", labels, _div(powermeter["power"], 1000))
        self._add("multimeter_energy", labels, powermeter["energy"])

    def _collect_temperature(self, ha_device: AhaDevice, labels: list[str]) -> None:
        temperature = ha_device["temperature"]
        if temperature is None:
            return
        self._add("temperature", labels, _div(temperature["celsius"], 10))
        self._add("temperature_offset", labels, _div(temperature["offset"], 10))

    def _collect_switch(self, ha_device: AhaDevice, labels: list[str]) -> None:
        switch = ha_device["switch"]
        if switch is None:
            return
        # Unknown states or modes (new firmware) skip just their sample.
        self._add("switch_state", labels, self._HTTP_SWITCH_STATE_MAP.get(switch["state"]))
        self._add("switch_mode", labels, self._HTTP_SWITCH_MODE_MAP.get(switch["mode"]))
        if switch["lock"] is not None:
            self._add("switch_lock", labels, int(switch["lock"] == "1"))

    def _collect_heater(self, ha_device: AhaDevice, labels: list[str]) -> None:
        hkr = ha_device["hkr"]
        if hkr is None:
            return
        # HKR temperatures from the AHA HTTP API are reported in half-degree
        # steps, unlike the plain <temperature> element (tenths of a degree).
        self._add("heater_temperature", labels, _div(hkr["tist"], 2))
        self._add("heater_set_temperature", labels, _hkr_setting(hkr["tsoll"]))
        self._add("heater_reduced_temperature", labels, _hkr_setting(hkr["absenk"]))
        self._add("heater_comfort_temperature", labels, _hkr_setting(hkr["komfort"]))

    def _collect_heater_valve_state(
        self, device: FritzDevice, ain: str, what: str, labels: list[str]
    ) -> None:
        # The valve/ventil status fields are not exposed by the AHA HTTP API's
        # getdevicelistinfos response, so they still require one TR-064 call
        # per thermostat (identified by AIN, not by enumeration index). An error
        # reply skips this item (see _item) and shows in fritz_read_problem.
        ha_result = read_action(device, "X_AVM-DE_Homeauto1", "GetSpecificDeviceInfos", NewAIN=ain)

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
                logger.debug(
                    "Unknown valve status %s of %s on %s, skipping", key, what, device.log_name
                )
                continue
            self._add(metric, labels, self._HKR_VALVE_MAP[status])

    def _collect_battery(self, ha_device: AhaDevice, labels: list[str]) -> None:
        self._add("battery_level", labels, ha_device["battery_level"])
        if ha_device["battery_low"] is not None:
            self._add("battery_low", labels, int(ha_device["battery_low"] == "1"))

    def _generate_metric_values(self, device: FritzDevice) -> None:
        # The AHA interface is part of the web UI, not TR-064: use the web UI
        # client, which targets the right port for local TLS and remote access.
        devicelist_xml = self._webui(device).fetch_aha("getdevicelistinfos")
        for index, ha_device in enumerate(parse_aha_devicelist_xml(devicelist_xml, self.issues)):
            labels = self._build_ha_labels(device, ha_device)

            self._add("devicepresent", labels, 2 if ha_device["present"] else 0)
            self._collect_multimeter(ha_device, labels)
            self._collect_temperature(ha_device, labels)
            self._collect_switch(ha_device, labels)
            self._collect_heater(ha_device, labels)
            self._collect_battery(ha_device, labels)
            if ha_device["hkr"] is not None:
                # Texts name the device by index: the AIN is a device value.
                what = f"smart home device {index}"
                with self._item(f"valve state of {what}"):
                    self._collect_heater_valve_state(device, ha_device["ain"], what, labels)


class WanDocsisCable(FritzCapability):
    """DOCSIS cable channel statistics from the Fritz!Box web UI.

    The TR-064 API does not expose DOCSIS channel data on AVM cable boxes.
    This capability is auto-detected on cable boxes (``NewWANAccessType`` is
    ``"Cable"`` or ``"X_AVM-DE_Cable"``) and reads the data from the internal
    web endpoint ``data.lua?page=docInfo``, the same one the Fritz!Box web UI
    uses. It requires a web login with the configured credentials.
    """

    uses_webui = True

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
        except TRANSPORT_ERRORS:
            raise
        except FritzConnectionException, DataError:
            logger.debug(
                "No WAN access type info on %s, DOCSIS capability disabled", device.log_name
            )
            self.present = False
            return

        self.present = access_type in ("Cable", "X_AVM-DE_Cable")
        logger.debug(
            "Capability %s set to %s on device %s",
            type(self).__name__,
            self.present,
            device.log_name,
        )

    def create_metrics(self) -> None:
        self._gauge(
            "power",
            "fritz_docsis_power",
            "DOCSIS channel signal power level",
            "direction",
            "channel_id",
            "standard",
            unit="dBmV",
        )
        self._gauge(
            "mer",
            "fritz_docsis_mer",
            "DOCSIS 3.1 downstream Modulation Error Ratio",
            "channel_id",
            "standard",
            unit="dB",
        )
        self._gauge(
            "mse",
            "fritz_docsis_mse",
            "DOCSIS 3.0 downstream Mean Squared Error",
            "channel_id",
            "standard",
            unit="dB",
        )
        self._counter(
            "corrected_errors",
            "fritz_docsis_corrected_errors_total",
            "DOCSIS downstream corrected codeword errors",
            "channel_id",
            "standard",
        )
        self._counter(
            "uncorrected_errors",
            "fritz_docsis_uncorrected_errors_total",
            "DOCSIS downstream uncorrected codeword errors",
            "channel_id",
            "standard",
        )
        self._gauge(
            "latency",
            "fritz_docsis_latency_ms",
            "DOCSIS downstream channel latency",
            "channel_id",
            "standard",
            unit="ms",
        )
        self._gauge(
            "info",
            "fritz_docsis_channel_info",
            "DOCSIS channel information (always 1 if present)",
            "direction",
            "channel_id",
            "standard",
            "modulation",
            "frequency",
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
        data = parse_docsis_response(self._webui(device).fetch_page(DOC_INFO_PAGE), self.issues)
        labels = [device.serial, device.friendly_name]

        for ch in data["downstream"]:
            self._emit_downstream_metrics(labels, ch)

        for ch in data["upstream"]:
            self._emit_upstream_metrics(labels, ch)


class WanSegmentUtilization(FritzCapability):
    """Shared network-segment utilization from the Fritz!Box REST API.

    The Fritz!OS REST endpoint ``/api/v0/monitor/segment/<n>`` exposes
    per-sample utilization of a shared medium, split into the traffic this
    box generates (``own``) and the total on the segment (``total``, including
    all other subscribers), for downstream and upstream. Only shared media have
    such data: on a cable box it is the coax segment, hence the metric names.
    Other boxes answer the endpoint too, with an empty ``data`` list (seen on
    DSL), and these metrics then have no samples.

    Segment ``0`` covers the last hour at minute granularity (60 one-minute
    averages). We expose only the *newest* sample of each series — the last
    element of each list — aligned by the shared ``lastSampleTime``.

    Asked for on every box with the REST API (detected by the common WAN
    interface service), since whether a medium is shared does not show in
    TR-064; the data is fetched through the shared web UI client.
    """

    uses_webui = True

    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANCommonInterfaceConfig1", "GetCommonLinkProperties"))

    def create_metrics(self) -> None:
        self._gauge(
            "utilization",
            "fritz_cable_segment_utilization_percent",
            "Shared cable segment utilization of the newest sample",
            "direction",
            "scope",
            unit="percent",
        )
        self._gauge(
            "sample_age",
            "fritz_cable_segment_sample_timestamp_seconds",
            "Unix timestamp of the newest shared-segment utilization sample",
            unit="seconds",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        data = parse_monitor_segment(
            self._webui(device).fetch_api("/api/v0/monitor/segment/0"), self.issues
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

    uses_webui = True

    def __init__(self) -> None:
        super().__init__()
        # The REST API is available on any Fritz!OS 7+ box, independent of
        # the WAN access type (cable/DSL/fiber). Presence is determined by the
        # common WAN interface service, which all modern boxes expose.
        self.requirements.append(("WANCommonInterfaceConfig1", "GetCommonLinkProperties"))

    def create_metrics(self) -> None:
        self._counter(
            "uptime",
            "fritz_wan_connection_uptime",
            "Per-stack uptime of a WAN connection in seconds (resets on reconnect)",
            "connection",
            "connection_name",
            "media_type",
            "stack",
            unit="seconds",
        )
        self._gauge(
            "status",
            "fritz_wan_connection_status",
            "Per-stack connection state of a WAN connection "
            "(1 when connected, 0 otherwise; the state is in fritz_wan_connection_info)",
            "connection",
            "connection_name",
            "media_type",
            "stack",
        )
        # The state and the address change on every reconnect; as labels of the
        # metrics above they made new series each time.
        self._gauge(
            "info",
            "fritz_wan_connection_info",
            "State and address of a WAN connection stack (always 1)",
            "connection",
            "connection_name",
            "media_type",
            "stack",
            "state",
            "ip_address",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        raw = self._webui(device).fetch_api("/api/v0/generic/connections")
        labels = [device.serial, device.friendly_name]
        for conn in parse_connections_response(raw, self.issues):
            base_labels = [*labels, conn["uid"], conn["name"], conn["media_type"]]
            for stack, uptime_key, status_key, ip_key in (
                ("ipv4", "ip4_uptime", "ip4_connstatus", "ip4_addr"),
                ("ipv6", "ip6_uptime", "ip6_connstatus", "ip6_addr"),
            ):
                self._add("uptime", [*base_labels, stack], conn[uptime_key])
                state = conn[status_key]
                if state:
                    self._add("status", [*base_labels, stack], int(state == "connected"))
                if state or conn[ip_key]:
                    self._add("info", [*base_labels, stack, state, conn[ip_key]], 1)


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
        self._gauge(
            "controller_enabled",
            "fritz_usp_controller_enabled",
            "Whether a USP controller is enabled",
            "index",
        )
        self._gauge(
            "myfritz_enabled",
            "fritz_usp_myfritz_enabled",
            "Whether the MyFRITZ USP controller is enabled",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        labels = [device.serial, device.friendly_name]
        service = "X_AVM-DE_USPController1"
        # The loop depends on the count, so it is read strictly.
        count = read_action(device, service, "GetUSPControllerNumberOfEntries").integer(
            "NewUSPControllerNumberOfEntries"
        )
        controllers = self._enumerate(
            device,
            service,
            "GetUSPControllerByIndex",
            count,
            index_argument="NewIndex",
            item="USP controller",
        )
        for index, controller in controllers:
            self._add(
                "controller_enabled",
                [*labels, str(index)],
                self.issues.flag(controller, "NewEnable"),
            )
        myfritz = read_action(device, service, "GetUSPMyFRITZEnable")
        self._add("myfritz_enabled", labels, self.issues.flag(myfritz, "NewUSPMyFRITZEnabled"))


class TelephonyNumbers(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("X_VoIP1", "X_AVM-DE_GetNumberOfNumbers"))

    def create_metrics(self) -> None:
        self._gauge(
            "numbers",
            "fritz_telephony_numbers",
            "Number of telephone numbers configured on the device",
        )

    def _generate_metric_values(self, device: FritzDevice) -> None:
        result = read_action(device, "X_VoIP1", "X_AVM-DE_GetNumberOfNumbers")
        self._add(
            "numbers",
            [device.serial, device.friendly_name],
            self.issues.num(result, "NewNumberOfNumbers"),
        )


class WanIPv6Prefix(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(("WANIPConn1", "X_AVM_DE_GetIPv6Prefix"))

    def create_metrics(self) -> None:
        self._gauge(
            "prefix",
            "fritz_wan_ipv6_prefix_info",
            "Delegated IPv6 prefix of the WAN connection (always 1 if present)",
            "prefix",
            "prefix_length",
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
