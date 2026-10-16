import collections
import logging
import threading

from attrs import Factory, define, evolve, field
from fritzconnection import FritzConnection  # type: ignore[import]
from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzAuthorizationError,
    FritzConnectionException,
    FritzServiceError,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector
from requests.exceptions import RequestException

from fritzexporter.exceptions import (
    FritzDeviceHasNoCapabilitiesError,
    FritzDeviceSerialUnknownError,
)
from fritzexporter.fritz_webui import FritzWebUiClient
from fritzexporter.fritzcapabilities import (
    FritzCapabilities,
    ProblemLevel,
    ProblemState,
    report_problem,
)
from fritzexporter.reply import DataError, read_action
from fritzexporter.tr064_remote import (
    TRANSPORT_ERRORS,
    ConnectionOptions,
    create_fritz_connection,
    transport_failure_text,
)

logger = logging.getLogger("fritzexporter.fritzdevice")


# A device that answers but gives no readable serial number is retried this many
# times after its first failure, then given up until the exporter is restarted.
# Connection failures are retried for as long as they last.
SERIAL_RETRIES: int = 3

# Maximum time a concurrent scrape waits for the collector lock before giving
# up and returning an empty scrape. Prevents one wedged/slow scrape from
# blocking every other Prometheus instance indefinitely.
SCRAPE_LOCK_TIMEOUT: float = 30.0


def device_log_name(friendly_name: str, host: str) -> str:
    """How log messages name a device: ``"<friendly name> (<host>)"``, or just the host."""
    if not friendly_name or friendly_name == host:
        return host
    return f"{friendly_name} ({host})"


# Errors that keep a device from being set up; it is then reported as unreachable
# and set up again on every scrape. FritzAuthorizationError is a
# FritzConnectionException.
_SETUP_ERRORS: tuple[type[Exception], ...] = (
    FritzConnectionException,
    FritzDeviceHasNoCapabilitiesError,
    FritzDeviceSerialUnknownError,
    RequestException,
)


@define(frozen=True)
class FritzCredentials:
    host: str
    user: str
    password: str = field(repr=False)


@define(frozen=True)
class DeviceSpec:
    """Everything needed to set up a device, at startup and after it was offline."""

    creds: FritzCredentials
    name: str
    host_info: bool = False
    wifi_client_info: bool = False
    connection: ConnectionOptions = Factory(ConnectionOptions)

    @property
    def log_name(self) -> str:
        return device_log_name(self.name, self.creds.host)


@define(frozen=True)
class OfflineDevice:
    """A device that could not be set up yet."""

    spec: DeviceSpec
    # Why the last attempt failed (exception type), and how often the serial
    # number could not be read; see SERIAL_RETRIES.
    last_failure: str = ""
    serial_failures: int = 0

    @property
    def given_up(self) -> bool:
        return self.serial_failures > SERIAL_RETRIES

    @property
    def friendly_name(self) -> str:
        return self.spec.name

    @property
    def log_name(self) -> str:
        return self.spec.log_name

    def failed(self, error: Exception) -> OfflineDevice:
        return evolve(
            self,
            last_failure=type(error).__name__,
            serial_failures=self.serial_failures + isinstance(error, FritzDeviceSerialUnknownError),
        )


def _setup_failure(error: Exception) -> str:
    """Why a device could not be set up, for the log."""
    if isinstance(error, TRANSPORT_ERRORS):
        return transport_failure_text(error)
    text = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
    if isinstance(error, FritzAuthorizationError):
        text += " (check username and password)"
    if not isinstance(error, _SETUP_ERRORS):
        text += " (unexpected; please report this)"
    return text


def _set_up(spec: DeviceSpec) -> FritzDevice | Exception:
    """Set up a device, or return why that failed.

    Any error is caught, not only the expected ones: an odd reply during setup
    (a bug in the exporter or a library) keeps this one device offline instead
    of stopping every scrape.
    """
    try:
        return FritzDevice.from_spec(spec)
    except Exception as e:  # noqa: BLE001 - contained per device, logged by the caller
        return e


def _traceback(error: Exception) -> Exception | None:
    """Unexpected setup errors are logged with their traceback."""
    return None if isinstance(error, _SETUP_ERRORS) else error


class FritzDevice:
    def __init__(
        self,
        creds: FritzCredentials,
        name: str,
        *,
        host_info: bool = False,
        wifi_client_info: bool = False,
        connection: ConnectionOptions | None = None,
    ) -> None:
        connection = connection or ConnectionOptions()
        self.host: str = creds.host
        self.serial: str = "n/a"
        self.model: str = "n/a"
        self.friendly_name: str = name
        self.log_name: str = device_log_name(name, creds.host)
        self.host_info: bool = host_info
        self.wifi_client_info: bool = wifi_client_info
        self.webui_client: FritzWebUiClient | None = None
        self.available: bool = True
        # Problems reading the connection mode; reported like a capability's.
        self.connection_mode_state = ProblemState()

        self.fc: FritzConnection = create_fritz_connection(
            address=creds.host,
            user=creds.user,
            password=creds.password,
            connection=connection,
        )

        self.get_device_info()

        logger.info("Connection to %s successful, reading capabilities", self.log_name)
        self.capabilities = FritzCapabilities(self)

        logger.info(
            "Reading capabilities for %s, got serial %s, model name %s completed",
            self.log_name,
            self.serial,
            self.model,
        )
        if host_info:
            logger.info(
                "HostInfo Capability enabled on device %s. "
                "This will cause slow responses from the exporter. "
                "Ensure prometheus is configured appropriately.",
                self.log_name,
            )
        # One web client, shared by every capability that reads data from the web
        # interface (smart home, DOCSIS, fibre levels, REST API).
        if any(cap.present and cap.uses_webui for _, cap in self.capabilities.items()):
            logger.info(
                "Web interface collector enabled on device %s. Reading web-interface data.",
                self.log_name,
            )
            self.webui_client = FritzWebUiClient(
                creds.host,
                creds.user,
                creds.password,
                use_tls=connection.use_tls,
                port=connection.web_port,
                timeout=connection.connection_timeout,
                allow_unsafe_md5_password=connection.allow_unsafe_md5_password,
                name=self.log_name,
            )
        if self.capabilities.empty():
            msg = f"{self.log_name} has no detected capabilities"
            raise FritzDeviceHasNoCapabilitiesError(msg)

    def get_device_info(self) -> None:
        try:
            device_info = read_action(self, "DeviceInfo1", "GetInfo")
            self.serial = device_info.text("NewSerialNumber")
            self.model = device_info.text("NewModelName", "n/a")
        except (FritzServiceError, FritzActionError, DataError) as e:
            # Without a serial, the device's series cannot be told apart from
            # another device's of the same name; collecting it could make
            # Prometheus reject the whole scrape over duplicate samples. Logged
            # once at registration; retries stay quiet, fritz_device_reachable
            # shows the device as down meanwhile.
            msg = f"could not read the serial number of {self.log_name} (DeviceInfo1.GetInfo: {e})"
            raise FritzDeviceSerialUnknownError(msg) from None

    @classmethod
    def from_spec(cls, spec: DeviceSpec) -> FritzDevice:
        return cls(
            spec.creds,
            spec.name,
            host_info=spec.host_info,
            wifi_client_info=spec.wifi_client_info,
            connection=spec.connection,
        )

    def add_connection_mode(self, family: GaugeMetricFamily, info: GaugeMetricFamily) -> None:
        """Add whether the device is in DSL, fibre, cable, mobile fallback or offline mode."""
        try:
            resp = read_action(self, "WANCommonInterfaceConfig", "GetCommonLinkProperties")
            link_status = resp.opt_text("NewPhysicalLinkStatus")
            access_type = resp.text("NewWANAccessType", "")
        except FritzServiceError, FritzActionError:
            # Device simply has no WAN interface (e.g. a mesh repeater). That does
            # NOT make it unavailable — skip the connection-mode metric but keep
            # the device available so its other capabilities (uptime, WLAN, hosts)
            # are still collected.
            logger.debug(
                "No WAN connection-mode info on %s (no WAN service); skipping metric.",
                self.log_name,
            )
            return
        except TRANSPORT_ERRORS as e:
            logger.warning("Device %s is unreachable: %s", self.log_name, transport_failure_text(e))
            self.available = False
            return
        except (FritzConnectionException, DataError) as e:
            report_problem(
                self.connection_mode_state,
                "ConnectionMode",
                self.log_name,
                level=ProblemLevel.SKIPPED,
                headline=f"Could not read the connection mode of {self.log_name}",
                details=str(e) if isinstance(e, DataError) else type(e).__name__,
            )
            return
        report_problem(self.connection_mode_state, "ConnectionMode", self.log_name, level=None)

        mode = _CONNECTION_MODES.get((link_status, access_type), 0)
        family.add_metric([self.serial, self.friendly_name], mode)
        # The access type changes on a mobile fallback; as a label of the mode it
        # started a new series each time.
        info.add_metric([self.serial, self.friendly_name, access_type], 1)


# fritz_connection_mode by (physical link status, WAN access type); anything
# else is 0, disconnected or not available.
_CONNECTION_MODES: dict[tuple[str | None, str], int] = {
    ("Up", "DSL"): 1,  # DSL connection active
    ("Down", "X_AVM-DE_Mobile"): 2,  # DSL disconnected -> fallback mobile connection active
    ("Up", "X_AVM-DE_Mobile"): 3,  # DSL disabled, only mobile connection active
    ("Up", "X_AVM-DE_Fiber"): 4,  # Fibre connection active
    ("Up", "Cable"): 5,  # Cable (DOCSIS) connection active
    ("Up", "X_AVM-DE_Cable"): 5,
}


def _connection_mode_families() -> tuple[GaugeMetricFamily, GaugeMetricFamily]:
    mode = GaugeMetricFamily(
        "fritz_connection_mode",
        "Connection mode: 1=DSL, 2=Mobile fallback, 3=Mobile-only, "
        "4=Fiber, 5=Cable, 0=offline/unknown",
        labels=["serial", "friendly_name"],
    )
    info = GaugeMetricFamily(
        "fritz_wan_access_type_info",
        "WAN access type of the device: DSL, X_AVM-DE_Mobile, ... (always 1)",
        labels=["serial", "friendly_name", "access_type"],
    )
    return mode, info


class FritzCollector(Collector):
    def __init__(self) -> None:
        self.devices: list[FritzDevice] = []
        self.offline_devices: list[OfflineDevice] = []
        # One shared instance per capability class, used to drive the scrape loop and
        # accumulate metrics across all devices. Distinct from per-device capabilities,
        # which are the authority on what each device actually supports.
        self._capability_instances: FritzCapabilities = FritzCapabilities()
        self._collect_lock = threading.RLock()
        # Setting up offline devices again can take a connection timeout per
        # device, so it runs outside _collect_lock, one scrape at a time.
        # _devices_lock guards the hand-over to devices and offline_devices.
        self._retry_lock = threading.Lock()
        self._devices_lock = threading.Lock()
        self._back_online: list[FritzDevice] = []
        # Counters describing the exporter's own operation (self-monitoring).
        # Guarded by a dedicated lock because scrapes increment/read them from
        # threads that do NOT hold _collect_lock (e.g. the lock-timeout path).
        self._scrape_stats_lock = threading.Lock()
        self._scrapes_per_device: dict[tuple[str, str], int] = {}
        self._scrape_timeouts: int = 0
        self._scrape_failures: int = 0

    def register(self, fritzdev: FritzDevice) -> None:
        self.devices.append(fritzdev)
        logger.debug("registered device %s, %s, to collector", fritzdev.log_name, fritzdev.model)

    def add_device(self, spec: DeviceSpec) -> FritzDevice | None:
        """Set up a device and register it; if that fails, report it as unreachable."""
        result = _set_up(spec)
        if isinstance(result, FritzDevice):
            logger.info("registering %s to collector", result.log_name)
            self.register(result)
            return result
        # Expected failures without a traceback: the reason says it all.
        logger.error(
            "Failed to initialize device %s, it will be reported as down: %s",
            spec.log_name,
            _setup_failure(result),
            exc_info=_traceback(result),
        )
        with self._devices_lock:
            self.offline_devices.append(OfflineDevice(spec).failed(result))
        return None

    def _retry_offline_devices(self) -> None:
        """Try to set up the offline devices again; skipped while another scrape does."""
        if not self._retry_lock.acquire(blocking=False):
            return
        try:
            with self._devices_lock:
                offline_devices = list(self.offline_devices)
            still_offline: list[OfflineDevice] = []
            back_online: list[FritzDevice] = []
            for offline in offline_devices:
                if offline.given_up:
                    still_offline.append(offline)
                    continue
                result = _set_up(offline.spec)
                if isinstance(result, Exception):
                    still_offline.append(self._offline_failure(offline, result))
                    continue
                logger.info("Device %s is back online, registering to collector.", offline.log_name)
                back_online.append(result)
            with self._devices_lock:
                self.offline_devices = still_offline
                self._back_online.extend(back_online)
        finally:
            self._retry_lock.release()

    @staticmethod
    def _offline_failure(offline: OfflineDevice, error: Exception) -> OfflineDevice:
        """Record a failed retry; log it when the reason changes or the device is given up."""
        name = offline.log_name
        failed = offline.failed(error)
        reason = _setup_failure(error)
        if failed.given_up:
            logger.error(
                "Giving up on device %s after %d attempts without a readable serial "
                "number: %s. It stays reported as unreachable until the exporter is "
                "restarted.",
                name,
                failed.serial_failures,
                reason,
            )
        elif failed.last_failure != offline.last_failure:
            logger.warning(
                "Device %s is still offline, now because: %s",
                name,
                reason,
                exc_info=_traceback(error),
            )
        else:
            logger.debug("Device %s is still offline: %s", name, reason)
        return failed

    def _build_self_monitoring_metrics(
        self,
    ) -> tuple[CounterMetricFamily, CounterMetricFamily, CounterMetricFamily]:
        """Build exporter self-monitoring counters for the completed cycle.

        Marks the cycle as a completed scrape for every device that was processed
        (reachable or not; offline-registered devices are never scraped) and
        reports the accumulated lock timeouts and scrape failures.
        """
        with self._scrape_stats_lock:
            for dev in self.devices:
                key = (dev.serial, dev.friendly_name)
                self._scrapes_per_device[key] = self._scrapes_per_device.get(key, 0) + 1
            scrapes_total = CounterMetricFamily(
                "fritz_scrapes_total",
                "Total number of completed scrape cycles per Fritz device.",
                labels=["serial", "friendly_name"],
            )
            for (serial, friendly_name), count in self._scrapes_per_device.items():
                scrapes_total.add_metric([serial, friendly_name], float(count))
            timeouts_total = CounterMetricFamily(
                "fritz_scrapes_timeouts_total",
                "Scrapes skipped because a previous scrape was still in progress "
                "when the collector lock deadline was reached.",
            )
            timeouts_total.add_metric([], float(self._scrape_timeouts))
            failures_total = CounterMetricFamily(
                "fritz_scrapes_failed_total",
                "Scrapes aborted by an unexpected error during collection.",
            )
            failures_total.add_metric([], float(self._scrape_failures))
        return scrapes_total, timeouts_total, failures_total

    def _collect_devices(
        self,
    ) -> tuple[list[CounterMetricFamily | GaugeMetricFamily], list[OfflineDevice]]:
        """Collect all device metrics eagerly, so device availability is known first.

        Also returns the devices that are offline in this scrape.
        """
        with self._devices_lock:
            for device in self._back_online:
                self.register(device)
            self._back_online.clear()
            offline_devices = list(self.offline_devices)

        # Reset availability for this collection cycle
        for dev in self.devices:
            dev.available = True
            if dev.webui_client is not None:
                dev.webui_client.start_scrape()

        connection_mode, access_type = _connection_mode_families()
        for dev in self.devices:
            dev.add_connection_mode(connection_mode, access_type)
        collected: list[CounterMetricFamily | GaugeMetricFamily] = [connection_mode, access_type]

        for name, capa in self._capability_instances.items():
            collected.extend(list(capa.get_metrics(self.devices, name)))
        return collected, offline_devices

    def _build_problem_metric(self) -> GaugeMetricFamily:
        """One series per device and capability whose last scrape skipped data."""
        problems = GaugeMetricFamily(
            "fritz_read_problem",
            "1 while the exporter skips data of a capability: some values or items "
            "(level=partial), the whole capability (skipped) or a bug (error); see the log",
            labels=["serial", "friendly_name", "capability", "level"],
        )
        for dev in self.devices:
            # Nothing was read from an unreachable device in this scrape, so its
            # problems from earlier scrapes would describe stale data;
            # fritz_device_reachable tells the story instead.
            if not dev.available:
                continue
            states = [("ConnectionMode", dev.connection_mode_state), *dev.capabilities.items()]
            for name, state in states:
                if state.problem is not None:
                    problems.add_metric(
                        [dev.serial, dev.friendly_name, name, state.problem.level], 1.0
                    )
        return problems

    def collect(self) -> collections.abc.Iterable[CounterMetricFamily | GaugeMetricFamily]:
        self._retry_offline_devices()
        # The lock is held for the whole scrape (blocking TR-064 calls included),
        # so acquiring it must be bounded: a slow or wedged first scrape would
        # otherwise block every concurrent scrape from other Prometheus instances
        # indefinitely. If the lock cannot be taken within the deadline, return an
        # empty scrape — the exporter stays responsive and a finite configured timeout
        # ensures the in-flight scrape eventually releases the lock. With
        # connection_timeout=0, the in-flight scrape may still remain blocked.
        if not self._collect_lock.acquire(timeout=SCRAPE_LOCK_TIMEOUT):
            with self._scrape_stats_lock:
                self._scrape_timeouts += 1
            logger.warning(
                "Skipping scrape: previous scrape still in progress after %.0fs",
                SCRAPE_LOCK_TIMEOUT,
            )
            return
        try:
            collected, offline_devices = self._collect_devices()

            # Built under the stats lock, yielded outside it, so the counters in
            # one output agree with the fritz_device_reachable gauge of the same cycle.
            scrapes_total, timeouts_total, failures_total = self._build_self_monitoring_metrics()
            yield scrapes_total
            yield timeouts_total
            yield failures_total

            # Yield device availability metric for all known devices
            device_up = GaugeMetricFamily(
                "fritz_device_reachable",
                "Fritz device reachability (1=reachable, 0=unreachable)",
                labels=["serial", "friendly_name"],
            )
            for dev in self.devices:
                device_up.add_metric([dev.serial, dev.friendly_name], 1.0 if dev.available else 0.0)
            for offline in offline_devices:
                device_up.add_metric(["n/a", offline.friendly_name], 0.0)
            yield device_up
            yield self._build_problem_metric()

            yield from collected
        except Exception:
            # An unexpected error aborts the whole scrape (the lock is released
            # below); record it so the exporter's own health reflects it.
            with self._scrape_stats_lock:
                self._scrape_failures += 1
            raise
        finally:
            self._collect_lock.release()


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
