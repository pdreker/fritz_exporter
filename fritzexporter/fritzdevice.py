import collections
import logging
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
from requests.exceptions import RequestException

from fritzexporter.exceptions import (
    FritzDeviceHasNoCapabilitiesError,
    FritzDeviceSerialUnknownError,
)
from fritzexporter.fritz_webui import FritzWebUiClient, FritzWebUiMd5DisabledError
from fritzexporter.fritzcapabilities import (
    FritzCapabilities,
    ProblemLevel,
    ProblemState,
    report_problem,
)
from fritzexporter.reply import DataError, read_action
from fritzexporter.tr064_remote import ConnectionOptions, create_fritz_connection

logger = logging.getLogger("fritzexporter.fritzdevice")


FRITZ_MAX_PASSWORD_LENGTH = 32

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


class FritzCredentials(NamedTuple):
    host: str
    user: str
    password: str


class OfflineDevice(NamedTuple):
    creds: FritzCredentials
    friendly_name: str
    host_info: bool
    connection_timeout: int | None
    wifi_client_info: bool
    use_tls: bool = False
    port: int | None = None
    remote_access: bool = False
    allow_unsafe_md5_password: bool = False
    # Why the last attempt failed (exception type), and how often the serial
    # number could not be read; see SERIAL_RETRIES.
    last_failure: str = ""
    serial_failures: int = 0

    @property
    def given_up(self) -> bool:
        return self.serial_failures > SERIAL_RETRIES

    @property
    def log_name(self) -> str:
        return device_log_name(self.friendly_name, self.creds.host)


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

        if len(creds.password) > FRITZ_MAX_PASSWORD_LENGTH:
            logger.warning(
                "Password for %s is longer than %d characters! Login may not succeed, "
                "please see README!",
                self.log_name,
                FRITZ_MAX_PASSWORD_LENGTH,
            )

        try:
            self.fc: FritzConnection = create_fritz_connection(
                address=creds.host,
                user=creds.user,
                password=creds.password,
                connection=connection,
            )
        except FritzConnectionException, RequestException:
            logger.exception("unable to connect to %s.", self.log_name)
            raise

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
        # The web UI client is shared by every capability that reads data from
        # the Fritz!Box web interface (AHA smart home data, DOCSIS channel data,
        # fibre optical levels, REST API endpoints).
        # Create it if any of them is present, so a DSL/fiber box can still use
        # the technology-agnostic REST capabilities.
        webui_capabilities = (
            "HomeAutomation",
            "WanDocsisCable",
            "WanFiberInterfaceConfig",
            "WanSegmentUtilization",
            "WanConnectionStatus",
        )
        if any(self.capabilities[name].present for name in webui_capabilities):
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
            logger.critical("Device %s has no detected capabilities.", self.log_name)
            raise FritzDeviceHasNoCapabilitiesError

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
        except FritzAuthorizationError:
            logger.exception(
                "Not authorized to get device info from %s. Check username/password.",
                self.log_name,
            )
            raise

    def get_connection_mode(self) -> GaugeMetricFamily | None:
        """
        Returns a metric to detect whether device is in DSL, fibre, mobile fallback or offline mode.
        """
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
            return None
        except FritzConnectionException, RequestException:
            logger.exception("Failed to retrieve connection mode info from %s", self.log_name)
            self.available = False
            return None
        except DataError as e:
            report_problem(
                self.connection_mode_state,
                "ConnectionMode",
                self.log_name,
                level=ProblemLevel.SKIPPED,
                headline=f"Could not read the connection mode of {self.log_name}",
                details=str(e),
            )
            return None
        report_problem(self.connection_mode_state, "ConnectionMode", self.log_name, level=None)

        if link_status == "Up" and access_type == "DSL":
            mode = 1  # DSL connection active
        elif link_status == "Down" and access_type == "X_AVM-DE_Mobile":
            mode = 2  # DSL disconnected -> Fallback mobile connection active
        elif link_status == "Up" and access_type == "X_AVM-DE_Mobile":
            mode = 3  # DSL disabled, only mobile connection active
        elif link_status == "Up" and access_type == "X_AVM-DE_Fiber":
            mode = 4  # Fibre connection active
        elif link_status == "Up" and access_type in ("Cable", "X_AVM-DE_Cable"):
            mode = 5  # Cable (DOCSIS) connection active
        else:
            mode = 0  # Disconnected or not available

        m = GaugeMetricFamily(
            "fritz_connection_mode",
            "Connection mode: 1=DSL, 2=Mobile fallback, 3=Mobile-only, "
            "4=Fiber, 5=Cable, 0=offline/unknown",
            labels=["serial", "friendly_name", "access_type"],
        )
        m.add_metric([self.serial, self.friendly_name, access_type], mode)
        return m


class FritzCollector(Collector):
    def __init__(self) -> None:
        self.devices: list[FritzDevice] = []
        self.offline_devices: list[OfflineDevice] = []
        # One shared instance per capability class, used to drive the scrape loop and
        # accumulate metrics across all devices. Distinct from per-device capabilities,
        # which are the authority on what each device actually supports.
        self._capability_instances: FritzCapabilities = FritzCapabilities()
        self._collect_lock = threading.RLock()
        # Counters describing the exporter's own operation (self-monitoring).
        # Guarded by a dedicated lock because scrapes increment/read them from
        # threads that do NOT hold _collect_lock (e.g. the lock-timeout path).
        self._scrape_stats_lock = threading.Lock()
        self._scrapes_per_device: dict[tuple[str, str], int] = {}
        self._scrape_timeouts: int = 0
        self._scrape_failures: int = 0
        # Set when an error must stop the exporter. Scrapes run in the HTTP
        # server's threads, where sys.exit() would end only that thread, so the
        # main thread waits on this event and exits instead.
        self.fatal_error = threading.Event()

    def register(self, fritzdev: FritzDevice) -> None:
        self.devices.append(fritzdev)
        logger.debug("registered device %s, %s, to collector", fritzdev.log_name, fritzdev.model)

    def register_offline(  # noqa: PLR0913
        self,
        creds: FritzCredentials,
        friendly_name: str,
        *,
        host_info: bool = False,
        wifi_client_info: bool = False,
        connection: ConnectionOptions | None = None,
        failure: Exception | None = None,
    ) -> None:
        connection = connection or ConnectionOptions()
        self.offline_devices.append(
            OfflineDevice(
                creds,
                friendly_name,
                host_info,
                connection.connection_timeout,
                wifi_client_info,
                connection.use_tls,
                connection.port,
                connection.remote_access,
                connection.allow_unsafe_md5_password,
                last_failure=type(failure).__name__ if failure else "",
                serial_failures=int(isinstance(failure, FritzDeviceSerialUnknownError)),
            )
        )
        logger.debug(
            "registered offline device %s to collector",
            device_log_name(friendly_name, creds.host),
        )

    def _retry_offline_devices(self) -> None:
        still_offline: list[OfflineDevice] = []
        for offline in self.offline_devices:
            if offline.given_up:
                still_offline.append(offline)
                continue
            try:
                fritz_device = FritzDevice(
                    offline.creds,
                    offline.friendly_name,
                    host_info=offline.host_info,
                    wifi_client_info=offline.wifi_client_info,
                    connection=ConnectionOptions(
                        connection_timeout=offline.connection_timeout,
                        use_tls=offline.use_tls,
                        port=offline.port,
                        remote_access=offline.remote_access,
                        allow_unsafe_md5_password=offline.allow_unsafe_md5_password,
                    ),
                )
                logger.info("Device %s is back online, registering to collector.", offline.log_name)
                self.register(fritz_device)
            except (
                FritzConnectionException,
                FritzAuthorizationError,
                FritzDeviceHasNoCapabilitiesError,
                FritzDeviceSerialUnknownError,
                RequestException,
            ) as e:
                still_offline.append(self._offline_failure(offline, e))
        self.offline_devices = still_offline

    @staticmethod
    def _offline_failure(offline: OfflineDevice, error: Exception) -> OfflineDevice:
        """Record a failed retry; log it when the reason changes or the device is given up."""
        name = offline.log_name
        failed = offline._replace(
            last_failure=type(error).__name__,
            serial_failures=offline.serial_failures
            + isinstance(error, FritzDeviceSerialUnknownError),
        )
        if failed.given_up:
            logger.error(
                "Giving up on device %s after %d attempts: %s. It stays reported as "
                "unreachable until the exporter is restarted.",
                name,
                failed.serial_failures,
                error,
            )
        elif failed.last_failure != offline.last_failure:
            logger.warning("Device %s is still offline, now because: %s", name, error)
        else:
            logger.debug("Device %s is still offline: %s", name, error)
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

    def _collect_devices(self) -> list[CounterMetricFamily | GaugeMetricFamily]:
        """Collect all device metrics eagerly, so device availability is known first."""
        # Attempt to bring offline devices back online before collecting
        self._retry_offline_devices()

        # Reset availability for this collection cycle
        for dev in self.devices:
            dev.available = True

        collected: list[CounterMetricFamily | GaugeMetricFamily] = []

        for dev in self.devices:
            mode_metric = dev.get_connection_mode()
            if mode_metric:
                collected.append(mode_metric)

        for name, capa in self._capability_instances.items():
            collected.extend(list(capa.get_metrics(self.devices, name)))
        return collected

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
            try:
                collected = self._collect_devices()
            except FritzWebUiMd5DisabledError as e:
                logger.critical("%s Exiting.", e)
                self.fatal_error.set()
                return

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
            for offline in self.offline_devices:
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
