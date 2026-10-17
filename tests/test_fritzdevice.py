import json
import logging
import threading
from pprint import pprint
from unittest.mock import MagicMock, call, patch

import pytest
from prometheus_client.core import GaugeMetricFamily
from fritzconnection.core.exceptions import (
    FritzActionError,
    FritzAuthorizationError,
    FritzConnectionException,
    FritzServiceError,
)
from http.client import RemoteDisconnected

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import Metric
from requests.exceptions import ConnectionError as RequestsConnectionError

from fritzexporter.exceptions import (
    FritzDeviceHasNoCapabilitiesError,
    FritzDeviceSerialUnknownError,
)
from fritzexporter.reply import Issues
from fritzexporter.fritzdevice import (
    DeviceSpec,
    FritzCollector,
    FritzCredentials,
    FritzDevice,
    OfflineDevice,
)
from fritzexporter.tr064_remote import ConnectionOptions, FritzUnreachableError

from .web_replies import web_reply
from .fc_services_mock import (
    call_action_mock,
    call_action_no_basic_mock,
    call_http_mock,
    create_fc_services,
    fc_services_capabilities,
    fc_services_devices,
    fc_services_no_basic_info,
)

def _register_offline(
    collector: FritzCollector,
    creds: FritzCredentials,
    name: str,
    connection: ConnectionOptions | None = None,
) -> None:
    """Register a device as offline without trying to set it up first."""
    spec = DeviceSpec(creds, name, connection=connection or ConnectionOptions())
    collector.offline_devices.append(OfflineDevice(spec))


FRITZDEVICE_LOG_SOURCE = "fritzexporter.fritzdevice"


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestFritzDevice:
    @pytest.mark.parametrize("capability", fc_services_capabilities.keys())
    def test_should_create_a_device_with_presented_capability(
        self, mock_fritzconnection, capability, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_capabilities[capability])

        # Act
        if capability == "HostInfo":
            fd = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=True)
        else:
            fd = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Check
        print(caplog.text)
        assert fd.model == "Fritz!MockBox 9790"
        assert fd.serial == "1234567890"
        LOGSOURCE = "fritzexporter.fritzcapability"
        if capability == "WlanConfigurationInfo":
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} in WLAN 1 set to True on device FritzMock (somehost)",
            ) in caplog.record_tuples
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} in WLAN 2 set to True on device FritzMock (somehost)",
            ) in caplog.record_tuples
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} in WLAN 3 set to False on device FritzMock (somehost)",
            ) in caplog.record_tuples
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} in WLAN 4 set to False on device FritzMock (somehost)",
            ) in caplog.record_tuples
        else:
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} set to True on device FritzMock (somehost)",
            ) in caplog.record_tuples

    def test_should_raise_fritz_connection_exception(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        mock_fritzconnection.side_effect = FritzConnectionException("somehost: connection refused")

        # Act
        creds = FritzCredentials("somehost", "someuser", "password")
        with pytest.raises(FritzConnectionException):
            FritzDevice(creds, "FritzMock", host_info=False)
        collector = FritzCollector()
        spec = DeviceSpec(FritzCredentials("somehost", "someuser", "password"), "FritzMock")
        assert collector.add_device(spec) is None

        # Check: logged once, by the collector, without a traceback
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert [r.getMessage() for r in errors] == [
            "Failed to initialize device FritzMock (somehost), it will be reported as down: "
            "FritzConnectionException: somehost: connection refused"
        ]
        assert errors[0].exc_info is None
        assert [o.spec for o in collector.offline_devices] == [spec]

    def test_should_invalidate_presented_service(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        identified = []

        def call_action(service, action, **kwargs):
            # The first device info call works, so the device is identified, but
            # every advertised capability (DeviceInfo included) fails its probe.
            if (service, action) == ("DeviceInfo1", "GetInfo") and not identified:
                identified.append(True)
                return call_action_mock(service, action, **kwargs)
            raise FritzServiceError("Mock FritzServiceError")

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        creds = FritzCredentials("somehost", "someuser", "password")
        with pytest.raises(FritzDeviceHasNoCapabilitiesError):
            FritzDevice(creds, "FritzMock", host_info=True)

    def test_should_create_fritz_device_with_correct_capabilities(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        fd = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Check
        assert fd.model == "Fritz!MockBox 9790"
        assert fd.serial == "1234567890"

        assert mock_fritzconnection.call_count == 1
        assert mock_fritzconnection.call_args == call(
            address="somehost",
            user="someuser",
            password="password",
            timeout=10,
            use_tls=False,
            port=None,
        )

    def test_should_find_no_capabilities(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)
        password: str = "123456789012345678901234567890123"

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services({})

        # Act
        creds = FritzCredentials("somehost", "someuser", password)
        with pytest.raises(FritzDeviceHasNoCapabilitiesError) as exc:
            FritzDevice(creds, "FritzMock", host_info=False)

        # Check
        assert str(exc.value) == "FritzMock (somehost) has no detected capabilities"

    def test_should_not_collect_a_device_without_serial(self, mock_fritzconnection: MagicMock, caplog):
        # The serial tells devices apart in Prometheus; without it the device is skipped.
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_no_basic_mock
        fc.services = create_fc_services(fc_services_no_basic_info)

        creds = FritzCredentials("somehost", "someuser", "password")
        with pytest.raises(FritzDeviceSerialUnknownError, match=r"could not read the serial number of FritzMock \(somehost\)"):
            FritzDevice(creds, "FritzMock")

    @pytest.mark.parametrize(
        "info",
        [
            {"NewModelName": "Fritz!Box", "NewSoftwareVersion": "1.2", "NewUpTime": 1},
            {"NewSerialNumber": "", "NewModelName": "Fritz!Box"},
            {"NewSerialNumber": ["not", "text"]},
            "not an object",
        ],
    )
    def test_unreadable_serial_skips_the_device(self, mock_fritzconnection: MagicMock, caplog, info):
        def call_action(service, action, **kwargs):
            if (service, action) == ("DeviceInfo1", "GetInfo"):
                return info
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        creds = FritzCredentials("somehost", "someuser", "password")
        with pytest.raises(FritzDeviceSerialUnknownError):
            FritzDevice(creds, "FritzMock")

    def test_missing_model_name_is_not_fatal(self, mock_fritzconnection: MagicMock):
        def call_action(service, action, **kwargs):
            if (service, action) == ("DeviceInfo1", "GetInfo"):
                return {"NewSerialNumber": "1234567890"}
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock")

        assert device.serial == "1234567890"
        assert device.model == "n/a"

    def test_connection_timeout_passed_to_fritzconnection(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        _ = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzMock",
            connection=ConnectionOptions(connection_timeout=10),
        )

        # Check: timeout must be forwarded to FritzConnection
        assert mock_fritzconnection.call_args == call(
            address="somehost",
            user="someuser",
            password="password",
            timeout=10,
            use_tls=False,
            port=None,
        )

    def test_connection_timeout_defaults_to_the_config_default(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        _ = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock")

        # Check: no explicit timeout → the config's default (10 s) is passed
        assert mock_fritzconnection.call_args == call(
            address="somehost",
            user="someuser",
            password="password",
            timeout=10,
            use_tls=False,
            port=None,
        )

    def test_use_tls_and_port_passed_to_fritzconnection(self, mock_fritzconnection: MagicMock):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        _ = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzMock",
            connection=ConnectionOptions(use_tls=True, port=49443),
        )

        assert mock_fritzconnection.call_args == call(
            address="somehost",
            user="someuser",
            password="password",
            timeout=10,
            use_tls=True,
            port=49443,
        )

    @patch("fritzexporter.fritzdevice.FritzWebUiClient")
    @pytest.mark.parametrize(
        "timeout, expected",
        [
            (10, 10),
            (None, None),
        ],
    )
    def test_connection_timeout_passed_to_webui_client(
        self,
        mock_webui_client: MagicMock,
        mock_fritzconnection: MagicMock,
        timeout: int | None,
        expected: int | None,
    ):
        # Prepare: the FritzBox 7590 mock exposes WANCommonInterfaceConfig1, so
        # the REST capabilities are present and the web UI client is created.
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        _ = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzMock",
            connection=ConnectionOptions(connection_timeout=timeout, port=12345),
        )

        # The Web UI uses its local default port; the configured port is for TR-064 only.
        # The timeout remains shared with TR-064 (None disables it).
        assert mock_webui_client.call_args == call(
            "somehost",
            "someuser",
            "password",
            use_tls=False,
            port=None,
            timeout=expected,
            allow_unsafe_md5_password=False,
            name="FritzMock (somehost)",
        )

    @patch("fritzexporter.fritzdevice.FritzWebUiClient")
    def test_allow_unsafe_md5_password_passed_to_webui_client(
        self, mock_webui_client: MagicMock, mock_fritzconnection: MagicMock
    ):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        _ = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzMock",
            connection=ConnectionOptions(allow_unsafe_md5_password=True),
        )

        assert mock_webui_client.call_args.kwargs["allow_unsafe_md5_password"] is True

    def test_md5_refusal_disables_only_the_web_interface(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_capabilities["HomeAutomation"])

        with patch("fritzexporter.fritz_webui.requests.Session") as mock_session_cls:
            session = mock_session_cls.return_value
            session.get.return_value = web_reply(
                "<SessionInfo><SID>0000000000000000</SID>"
                "<Challenge>12345678</Challenge><BlockTime>0</BlockTime></SessionInfo>"
            )
            collector = FritzCollector()
            collector.register(
                FritzDevice(FritzCredentials("fritz.box", "someuser", "password"), "FritzMock")
            )
            list(collector.collect())
            metrics = {m.name: m for m in collector.collect()}

        # Asked once, then never again until restart.
        assert session.get.call_count == 1
        session.post.assert_not_called()
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "offers only the MD5 web login" in errors[0]
        assert "disabled until restart" in errors[0]
        reachable = metrics["fritz_device_reachable"].samples
        assert [s.value for s in reachable] == [1.0]
        problems = {
            (s.labels["capability"], s.labels["level"]) for s in metrics["fritz_read_problem"].samples
        }
        assert problems == {("HomeAutomation", "skipped")}

    @patch("fritzexporter.fritzdevice.FritzWebUiClient")
    @pytest.mark.parametrize(
        "port, expected_tr064_port, expected_web_port",
        [
            # Local: port is the TR-064 port only; the web UI keeps 80/443.
            (None, None, None),
            (49443, 49443, None),
        ],
    )
    def test_local_tls_ports(
        self,
        mock_webui_client: MagicMock,
        mock_fritzconnection: MagicMock,
        port: int | None,
        expected_tr064_port: int | None,
        expected_web_port: int | None,
    ):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        _ = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzMock",
            connection=ConnectionOptions(use_tls=True, port=port),
        )

        assert mock_fritzconnection.call_args.kwargs["port"] == expected_tr064_port
        assert mock_webui_client.call_args.kwargs["port"] == expected_web_port

    @patch("fritzexporter.fritzdevice.FritzWebUiClient")
    @pytest.mark.parametrize(
        "port, expected",
        [
            # Remote: TR-064 and web UI share the remote HTTPS port.
            (None, 443),
            (11243, 11243),
        ],
    )
    def test_remote_access_ports(
        self,
        mock_webui_client: MagicMock,
        mock_fritzconnection: MagicMock,
        port: int | None,
        expected: int,
    ):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        _ = FritzDevice(
            FritzCredentials("myfritz.example", "someuser", "password"),
            "FritzMock",
            connection=ConnectionOptions(use_tls=True, port=port, remote_access=True),
        )

        assert mock_fritzconnection.call_args.kwargs["port"] == expected
        assert mock_webui_client.call_args.kwargs["port"] == expected

    def test_local_tls_aha_ignores_remote_access_port(self, mock_fritzconnection: MagicMock):
        # Regression for #700: a box that advertises a WAN remote-access port
        # must not have AHA sent there when scraped over local TLS.
        def remote_port_mock(service, action, **kwargs):
            if service == "X_AVM-DE_RemoteAccess1":
                return {"NewPort": "18228", "NewEnabled": "1"}
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = remote_port_mock
        fc.services = create_fc_services(fc_services_capabilities["HomeAutomation"])

        with patch("fritzexporter.fritz_webui.requests.Session") as mock_session_cls:
            session = mock_session_cls.return_value
            login = web_reply("<SessionInfo><SID>1234567890abcdef</SID></SessionInfo>")
            aha = web_reply('<devicelist version="1"></devicelist>')
            session.get.side_effect = [login, aha]

            collector = FritzCollector()
            device = FritzDevice(
                FritzCredentials("fritz.box", "someuser", "password"),
                "FritzMock",
                connection=ConnectionOptions(use_tls=True),
            )
            collector.register(device)
            metrics = list(collector.collect())

        urls = [c.args[0] for c in session.get.call_args_list]
        assert urls == [
            "https://fritz.box/login_sid.lua",
            "https://fritz.box/webservices/homeautoswitch.lua",
        ]
        assert session.verify is False
        assert all(c.args[0] != "X_AVM-DE_RemoteAccess1" for c in fc.call_action.call_args_list)

        reachable = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert reachable[0].samples[0].value == 1.0

    def test_transport_error_on_connect_is_logged_without_url(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: the connection fails at transport level, not at protocol level
        caplog.set_level(logging.DEBUG)
        mock_fritzconnection.side_effect = RequestsConnectionError(
            "Connection aborted.",
            RemoteDisconnected("Remote end closed connection without response"),
        )

        # Act / Check: the reason names the kind of failure, not the request URL
        collector = FritzCollector()
        collector.add_device(DeviceSpec(FritzCredentials("somehost", "someuser", "password"), "FritzMock"))

        assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == [
            "Failed to initialize device FritzMock (somehost), it will be reported as down: "
            "Fritz device did not answer (ConnectionError)"
        ]



def _scrape(collector: FritzCollector) -> list[Metric]:
    """One scrape, then wait for the background retry of offline devices it started."""
    metrics: list[Metric] = list(collector.collect())
    if collector._retry_thread is not None:
        collector._retry_thread.join(5)
    return metrics


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestFritzCollector:
    def test_should_instantiate_empty_collector(self, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        # Act
        collector = FritzCollector()

        # Check
        assert collector.devices == []

        all_capas = [
            "DeviceInfo",
            "HostNumberOfEntries",
            "UserInterface",
            "LanInterfaceConfig",
            "LanInterfaceConfigStatistics",
            "WanDSLInterfaceConfig",
            "WanDSLInterfaceConfigAVM",
            "WanFiberInterfaceConfig",
            "WanFiberGPONInfo",
            "WanFiberStatistics",
            "WanPPPConnectionStatus",
            "WanCommonInterfaceConfig",
            "WanCommonInterfaceDataBytes",
            "WanCommonInterfaceByteRate",
            "WanCommonInterfaceDataPackets",
            "WlanConfigurationInfo",
            "WlanAssociatedDevices",
            "MeshTopology",
            "HostInfo",
            "HomeAutomation",
            "WanDocsisCable",
            "WanSegmentUtilization",
            "WanConnectionStatus",
            "ManagementServerInfo",
            "UspControllers",
            "TelephonyNumbers",
            "WanIPv6Prefix",
        ]

        assert list(collector._capability_instances.capabilities.keys()) == all_capas

    @patch("fritzexporter.fritzdevice.FritzWebUiClient")
    def test_each_scrape_lets_the_web_client_try_the_device_again(
        self, mock_webui_client: MagicMock, mock_fritzconnection: MagicMock
    ):
        # After a timeout the web client fails the rest of the scrape at once;
        # without start_scrape() it would never send a request again.
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        client = mock_webui_client.return_value
        collector = FritzCollector()
        collector.register(
            FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock")
        )

        for scrapes in (1, 2):
            client.reset_mock()
            _scrape(collector)
            calls = [name for name, *_ in client.mock_calls]
            assert calls.count("start_scrape") == 1, scrapes
            assert calls[0] == "start_scrape", calls

    def test_should_register_device_to_collector(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        # Check
        assert len(collector.devices) == 1
        assert device is collector.devices[0]

    def test_should_collect_metrics_from_device(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)
        metrics: list[Metric] = list(collector.collect())

        # Check
        assert len(collector.devices) == 1
        assert device is collector.devices[0]
        for m in metrics:
            # Exporter self-monitoring counters carry no device labels.
            if m.name in {"fritz_scrapes_timeouts", "fritz_scrapes_failed"}:
                continue
            for s in m.samples:
                # fritz_device_reachable is keyed by name only.
                if m.name != "fritz_device_reachable":
                    assert s.labels["serial"] == "1234567890"
                assert "friendly_name" in s.labels
                assert s.labels["friendly_name"] == "FritzMock"

    def test_should_render_full_exposition_without_raising(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare - a full-fixture device exercising every capability, fed
        # through the actual Prometheus text-exposition formatter. Unlike
        # every other test here (which only inspects the collect() Metric/
        # Sample objects), this is the one place that renders exposition
        # text: add_metric() stores a value as-is with no conversion, and
        # a non-numeric value (see #668/#669) only blows up later, inside
        # generate_latest()'s float() formatting - so only rendering can
        # actually catch that class of bug.
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        registry = CollectorRegistry()
        registry.register(collector)

        # Act / Check - must not raise
        output = generate_latest(registry)

        assert b"fritz_uptime_seconds" in output

    def test_should_collect_host_info_from_device(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=True)
        collector.register(device)
        metrics: list[Metric] = list(collector.collect())

        # Check
        assert len(collector.devices) == 1
        assert device is collector.devices[0]
        pprint(metrics)

        prom_metrics = [m.name for m in metrics]
        assert "fritz_host_speed" in prom_metrics
        assert "fritz_host_active" in prom_metrics

    def test_should_only_expose_one_metric_for_multiple_devices(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        collector = FritzCollector()
        device1 = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock1", host_info=True)
        device2 = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock2", host_info=True)
        device3 = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock3", host_info=True)
        collector.register(device1)
        collector.register(device2)
        collector.register(device3)

        # TODO: Might be worth to check this for every metric?
        metrics: list[Metric] = []
        for m in collector.collect():
            if m.name == "fritz_uptime_seconds":
                metrics.append(m)

        # Check
        assert len(metrics) == 1
        assert len(metrics[0].samples) == 3

    def test_should_emit_device_up_metric_for_working_device(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)
        metrics: list[Metric] = list(collector.collect())

        # Check: fritz_device_reachable should be 1 for a reachable device
        device_reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(device_reachable_metrics) == 1
        assert device_reachable_metrics[0].samples[0].value == 1.0
        assert device_reachable_metrics[0].samples[0].labels == {"friendly_name": "FritzMock"}

    def test_should_emit_device_up_0_when_device_unreachable_during_collection(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        # Simulate device becoming unreachable after registration
        fc.call_action.side_effect = FritzUnreachableError("device unreachable")

        # Act: collect() should not raise, but return status 0
        metrics: list[Metric] = list(collector.collect())

        # Check: fritz_device_reachable should be 0
        device_reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(device_reachable_metrics) == 1
        assert device_reachable_metrics[0].samples[0].value == 0.0
        assert device_reachable_metrics[0].samples[0].labels["friendly_name"] == "FritzMock"

    def test_should_skip_capability_metrics_when_device_goes_down_mid_collection(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: register device, then have it fail on DeviceInfo calls
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        # Simulate connection failure during metric collection (only after setup)
        def fail_on_device_info(service, action, **kwargs):
            if service == "DeviceInfo1" and action == "GetInfo":
                raise FritzUnreachableError("device unreachable")
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = fail_on_device_info

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check: fritz_uptime metric is present but has no samples (DeviceInfo failed)
        uptime_metrics = [m for m in metrics if m.name == "fritz_uptime_seconds"]
        assert len(uptime_metrics) == 1
        assert len(uptime_metrics[0].samples) == 0

        # fritz_device_reachable should be 0 (device marked unavailable after DeviceInfo failure)
        device_reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(device_reachable_metrics) == 1
        assert device_reachable_metrics[0].samples[0].value == 0.0

    def test_should_report_offline_device_on_second_collection_cycle(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: device works on first collection, fails on second
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        # First collection: device is reachable
        metrics_round1: list[Metric] = list(collector.collect())
        device_reachable_round1 = [m for m in metrics_round1 if m.name == "fritz_device_reachable"]
        assert device_reachable_round1[0].samples[0].value == 1.0

        # Second collection: device is unreachable
        fc.call_action.side_effect = FritzUnreachableError("device unreachable")
        metrics_round2: list[Metric] = list(collector.collect())
        device_reachable_round2 = [m for m in metrics_round2 if m.name == "fritz_device_reachable"]
        assert device_reachable_round2[0].samples[0].value == 0.0

        # Third collection: device comes back up
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        metrics_round3: list[Metric] = list(collector.collect())
        device_reachable_round3 = [m for m in metrics_round3 if m.name == "fritz_device_reachable"]
        assert device_reachable_round3[0].samples[0].value == 1.0

    def test_should_not_exit_when_only_offline_devices_registered(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: no online devices, only offline
        caplog.set_level(logging.DEBUG)

        collector = FritzCollector()
        _register_offline(collector, 
            FritzCredentials("offlinehost", "user", "pass"), "OfflineDevice"
        )

        # Act: collect() should not sys.exit
        metrics: list[Metric] = list(collector.collect())

        # Check: fritz_device_reachable = 0 for the offline device
        device_reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(device_reachable_metrics) == 1
        assert device_reachable_metrics[0].samples[0].value == 0.0
        # No "n/a" serial: the series stays the same when the device comes back.
        assert device_reachable_metrics[0].samples[0].labels == {"friendly_name": "OfflineDevice"}

    def test_offline_device_promoted_when_it_comes_back_online(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: device is offline at startup
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = FritzUnreachableError("not reachable")
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        creds = FritzCredentials("somehost", "someuser", "password")
        _register_offline(collector, creds, "FritzMock")

        # First collect: device still offline → reachable=0, stays in offline_devices
        metrics_round1: list[Metric] = _scrape(collector)
        reachable_round1 = [m for m in metrics_round1 if m.name == "fritz_device_reachable"]
        assert reachable_round1[0].samples[0].value == 0.0
        assert len(collector.devices) == 0
        assert len(collector.offline_devices) == 1

        # Device comes back: second collect retries and promotes it
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        _scrape(collector)  # brings it back in the background
        metrics_round2: list[Metric] = _scrape(collector)
        reachable_round2 = [m for m in metrics_round2 if m.name == "fritz_device_reachable"]
        assert reachable_round2[0].samples[0].value == 1.0
        # The same series as while it was offline.
        assert reachable_round2[0].samples[0].labels == {"friendly_name": "FritzMock"}
        assert len(collector.devices) == 1
        assert len(collector.offline_devices) == 0

    def test_register_offline_preserves_connection_timeout(self, mock_fritzconnection: MagicMock):
        # Prepare
        collector = FritzCollector()
        creds = FritzCredentials("offlinehost", "user", "pass")

        # Act
        _register_offline(collector, 
            creds, "OfflineDevice", connection=ConnectionOptions(connection_timeout=15)
        )

        # Check: timeout stored so that retry uses the same bound
        assert len(collector.offline_devices) == 1
        assert collector.offline_devices[0].spec.connection.connection_timeout == 15

    def test_retry_offline_devices_uses_connection_timeout(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare: register device offline with a specific timeout
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = FritzUnreachableError("not reachable")
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        creds = FritzCredentials("somehost", "someuser", "password")
        _register_offline(collector, 
            creds, "FritzMock", connection=ConnectionOptions(connection_timeout=7)
        )

        # Device comes back during retry
        fc.call_action.side_effect = call_action_mock

        _scrape(collector)
        _scrape(collector)

        # Check: FritzConnection was called with the stored timeout during the retry
        assert mock_fritzconnection.call_args == call(
            address="somehost",
            user="someuser",
            password="password",
            timeout=7,
            use_tls=False,
            port=None,
        )
        assert len(collector.devices) == 1
        assert len(collector.offline_devices) == 0

    def test_register_offline_preserves_tls_and_port(self, mock_fritzconnection: MagicMock):
        collector = FritzCollector()
        creds = FritzCredentials("offlinehost", "user", "pass")

        _register_offline(collector, 
            creds,
            "OfflineDevice",
            connection=ConnectionOptions(use_tls=True, port=49443, remote_access=True),
        )

        assert len(collector.offline_devices) == 1
        connection = collector.offline_devices[0].spec.connection
        assert connection.use_tls is True
        assert connection.port == 49443
        assert connection.remote_access is True
        assert connection.allow_unsafe_md5_password is False

    @patch("fritzexporter.fritzdevice.FritzWebUiClient")
    def test_retry_offline_devices_keeps_allow_unsafe_md5_password(
        self, mock_webui_client: MagicMock, mock_fritzconnection: MagicMock
    ):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = FritzUnreachableError("not reachable")
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        _register_offline(collector, 
            FritzCredentials("somehost", "someuser", "password"),
            "FritzMock",
            connection=ConnectionOptions(allow_unsafe_md5_password=True),
        )
        assert collector.offline_devices[0].spec.connection.allow_unsafe_md5_password is True

        fc.call_action.side_effect = call_action_mock
        _scrape(collector)
        _scrape(collector)

        assert len(collector.devices) == 1
        assert mock_webui_client.call_args.kwargs["allow_unsafe_md5_password"] is True

    def test_retry_offline_devices_uses_tls_and_port(self, mock_fritzconnection: MagicMock):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = FritzUnreachableError("not reachable")
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        creds = FritzCredentials("somehost", "someuser", "password")
        _register_offline(collector, 
            creds,
            "FritzMock",
            connection=ConnectionOptions(use_tls=True, port=49443, remote_access=True),
        )

        fc.call_action.side_effect = call_action_mock
        _scrape(collector)
        _scrape(collector)

        assert mock_fritzconnection.call_args == call(
            address="somehost",
            user="someuser",
            password="password",
            timeout=10,
            use_tls=True,
            port=49443,
        )
        assert len(collector.devices) == 1
        assert len(collector.offline_devices) == 0

    def test_connection_mode_error_is_a_read_problem(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        # The device answers the connection mode request with an error
        original_side_effect = fc.call_action.side_effect

        def connection_mode_error(service, action, **kwargs):
            if service == "WANCommonInterfaceConfig" and action == "GetCommonLinkProperties":
                raise FritzConnectionException("no connection mode")
            return original_side_effect(service, action, **kwargs)

        fc.call_action.side_effect = connection_mode_error

        metrics: list[Metric] = list(collector.collect())

        # Check: no connection mode sample, reported as a read problem, device reachable
        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_connection_mode"].samples == []
        problems = {(s.labels["capability"], s.labels["level"]) for s in by_name["fritz_read_problem"].samples}
        assert ("ConnectionMode", "skipped") in problems
        assert by_name["fritz_device_reachable"].samples[0].value == 1.0

    def test_should_collect_capabilities_from_wanless_repeater(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Regression: a WAN-less mesh repeater must still export its capabilities
        # (uptime, WLAN, ...) and report reachable=1. The WAN-based connection-mode
        # probe failing with a missing-service error must not mark it unavailable.
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_devices["FritzRepeater 2400"])

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("repeater", "someuser", "password"), "RepeaterMock", host_info=False
        )
        collector.register(device)

        # A repeater exposes no WAN service: the connection-mode probe raises a
        # missing-service error (as fritzconnection does for an absent service).
        def no_wan_service(service, action, **kwargs):
            if service == "WANCommonInterfaceConfig" and action == "GetCommonLinkProperties":
                raise FritzServiceError("no such service")
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = no_wan_service

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check: device stays reachable ...
        reachable = next((m for m in metrics if m.name == "fritz_device_reachable"), None)
        assert reachable is not None
        assert reachable.samples
        assert reachable.samples[0].value == 1.0
        # ... and its capability metrics are still collected (empty before the fix)
        uptime = next((m for m in metrics if m.name == "fritz_uptime_seconds"), None)
        assert uptime is not None
        assert len(uptime.samples) == 1

    def test_should_collect_wifi_client_info_when_enabled(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Opt-in per-station metrics: with wifi_client_info=True the exporter emits
        # one signal/speed sample per associated client per detected radio.
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_capabilities["WlanConfigurationInfo"])

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"), "FritzMock", wifi_client_info=True
        )
        collector.register(device)

        # Two associated clients per radio, with per-index info.
        def assoc_mock(service, action, **kwargs):
            if action == "GetTotalAssociations":
                return {"NewTotalAssociations": 2}
            if action == "GetGenericAssociatedDeviceInfo":
                i = kwargs["NewAssociatedDeviceIndex"]
                radio = service.removeprefix("WLANConfiguration")
                return {
                    "NewAssociatedDeviceMACAddress": f"AA:BB:CC:00:0{radio}:0{i}",
                    "NewAssociatedDeviceIPAddress": f"192.168.178.{i}",
                    "NewX_AVM-DE_SignalStrength": 80 - i,
                    "NewX_AVM-DE_Speed": 100 + i,
                }
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = assoc_mock

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check: signal metric present, 2 clients on each of the 2 detected radios
        signal = [m for m in metrics if m.name == "fritz_wifi_client_signal_strength"]
        assert len(signal) == 1
        assert len(signal[0].samples) == 4
        assert any(s.labels["client_mac"] == "AA:BB:CC:00:01:00" for s in signal[0].samples)
        # Band and address are in fritz_wifi_client_info: they change.
        [info] = [m for m in metrics if m.name == "fritz_wifi_client_info"]
        assert {"2.4GHz", "5GHz"} <= {s.labels["wifi_name"] for s in info.samples}

    def test_should_not_collect_wifi_client_info_by_default(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Without the opt-in flag the capability stays disabled (no per-client samples).
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_capabilities["WlanConfigurationInfo"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock")
        collector.register(device)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check: metric family is exposed but empty (capability not enabled)
        signal = [m for m in metrics if m.name == "fritz_wifi_client_signal_strength"]
        assert len(signal) == 1
        assert len(signal[0].samples) == 0

    @patch("fritzexporter.fritzcapabilities.MeshTopology._mesh_list")
    def test_should_collect_mesh_backhaul_links(
        self, mock_fritzhosts: MagicMock, mock_fritzconnection: MagicMock, caplog
    ):
        # The mesh master exports one series per backhaul link between mesh nodes;
        # client links (to non-meshed devices) are excluded. A node pair can have
        # more than one concurrent link of the same type (e.g. simultaneous 2.4GHz
        # + 5GHz WLAN backhaul, as seen on real hardware) - each physical link is
        # listed once under each endpoint's own node record (same uid, same data
        # both times - deduped), but distinct links (different uid) between the
        # same pair must stay separate, disambiguated by the `interface` label.
        caplog.set_level(logging.DEBUG)

        def call_with_mesh(service, action, **kwargs):
            if service == "Hosts1" and action == "X_AVM-DE_GetMeshListPath":
                return {"NewX_AVM-DE_MeshListPath": "/meshlist.lua"}
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_with_mesh
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        mock_fritzhosts.return_value = {
            "nodes": [
                {
                    "uid": "n1",
                    "device_name": "fritzbox",
                    "is_meshed": True,
                    "node_interfaces": [
                        {
                            "type": "WLAN",
                            "name": "AP:2G:0",
                            "node_links": [
                                {
                                    "uid": "l1", "node_1_uid": "n1", "node_2_uid": "n2",
                                    "state": "CONNECTED",
                                    "cur_data_rate_rx": 71800, "cur_data_rate_tx": 72200,
                                    "max_data_rate_rx": 144400, "max_data_rate_tx": 144400,
                                },
                                {
                                    "uid": "l2", "node_1_uid": "n1", "node_2_uid": "c1",
                                    "state": "CONNECTED",
                                    "cur_data_rate_rx": 100, "cur_data_rate_tx": 100,
                                    "max_data_rate_rx": 200, "max_data_rate_tx": 200,
                                },
                            ],
                        },
                        {
                            "type": "WLAN",
                            "name": "AP:5G:0",
                            "node_links": [
                                {
                                    # Concurrent 5GHz backhaul to the same peer as l1.
                                    # AVM reports node_1_uid/node_2_uid reversed here
                                    # relative to l1, matching what was observed on
                                    # real hardware.
                                    "uid": "l3", "node_1_uid": "n2", "node_2_uid": "n1",
                                    "state": "CONNECTED",
                                    "cur_data_rate_rx": 288200, "cur_data_rate_tx": 864000,
                                    "max_data_rate_rx": 2401000, "max_data_rate_tx": 2401000,
                                },
                            ],
                        },
                    ],
                },
                {
                    "uid": "n2",
                    "device_name": "repeater",
                    "is_meshed": True,
                    "node_interfaces": [
                        {
                            "type": "WLAN",
                            "name": "UPLINK:2G:0",
                            "node_links": [
                                {
                                    "uid": "l1", "node_1_uid": "n1", "node_2_uid": "n2",
                                    "state": "CONNECTED",
                                    "cur_data_rate_rx": 71800, "cur_data_rate_tx": 72200,
                                    "max_data_rate_rx": 144400, "max_data_rate_tx": 144400,
                                }
                            ],
                        },
                        {
                            "type": "WLAN",
                            "name": "UPLINK:5G:0",
                            "node_links": [
                                {
                                    "uid": "l3", "node_1_uid": "n2", "node_2_uid": "n1",
                                    "state": "CONNECTED",
                                    "cur_data_rate_rx": 288200, "cur_data_rate_tx": 864000,
                                    "max_data_rate_rx": 2401000, "max_data_rate_tx": 2401000,
                                },
                            ],
                        },
                    ],
                },
                {"uid": "c1", "device_name": "phone", "is_meshed": False, "node_interfaces": []},
            ]
        }

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock")
        collector.register(device)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check: two distinct backhaul links (l1 2.4GHz + l3 5GHz, each deduped
        # across both endpoints' reports), client link l2 excluded
        available = [m for m in metrics if m.name == "fritz_mesh_link_available"]
        assert len(available) == 1
        assert len(available[0].samples) == 2
        for sample in available[0].samples:
            assert sample.value == 1.0
            assert {sample.labels["node"], sample.labels["peer"]} == {"fritzbox", "repeater"}
        assert all(s.labels["peer"] != "phone" for s in available[0].samples)

        # The two links must be disambiguated by `interface`, not collapsed
        interfaces = {s.labels["interface"] for s in available[0].samples}
        assert interfaces == {"AP:2G:0", "AP:5G:0"}

        # rx + tx series for each of the two backhaul links
        datarate = [m for m in metrics if m.name == "fritz_mesh_link_current_data_rate_kbps"]
        assert len(datarate[0].samples) == 4

    def test_should_survive_transport_error_from_the_aha_interface(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: TR-064 works, but the AHA HTTP interface fails at transport level
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        with patch("fritzexporter.fritz_webui.requests.Session") as mock_session_cls:
            device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        mock_session_cls.return_value.get.side_effect = RequestsConnectionError(
            "Connection aborted.",
            RemoteDisconnected("Remote end closed connection without response"),
        )

        # Act: one failing sub-collector must not abort the whole collection cycle
        metrics: list[Metric] = list(collector.collect())

        # Check: exposition still works, the device stays reachable, and the
        # capabilities after HomeAutomation still run
        registry = CollectorRegistry()
        registry.register(collector)
        generate_latest(registry)

        device_reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(device_reachable_metrics) == 1
        assert device_reachable_metrics[0].samples[0].value == 1.0

        for capability in ("HomeAutomation", "WanSegmentUtilization", "WanConnectionStatus"):
            assert f"Could not read {capability} data from FritzMock (somehost)" in caplog.text

    def test_should_survive_transport_error_on_connection_mode(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: the very first TR-064 call of a cycle fails at transport level
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        fc.call_action.side_effect = RequestsConnectionError(
            "Connection aborted.",
            RemoteDisconnected("Remote end closed connection without response"),
        )

        # Act: collect() must not propagate the transport error
        metrics: list[Metric] = list(collector.collect())

        # Check: the device is reported as unreachable instead
        device_reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(device_reachable_metrics) == 1
        assert device_reachable_metrics[0].samples[0].value == 0.0

    def test_offline_device_stays_offline_on_transport_error(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: retrying an offline device fails at transport level
        caplog.set_level(logging.DEBUG)
        mock_fritzconnection.side_effect = RequestsConnectionError(
            "Connection aborted.",
            RemoteDisconnected("Remote end closed connection without response"),
        )

        collector = FritzCollector()
        _register_offline(collector, FritzCredentials("offlinehost", "user", "pass"), "OfflineDevice")

        # Act: the failed retry must not abort the whole collection cycle
        metrics: list[Metric] = list(collector.collect())

        # Check: the device just stays offline and is retried on the next cycle
        assert len(collector.devices) == 0
        assert len(collector.offline_devices) == 1
        device_reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(device_reachable_metrics) == 1
        assert device_reachable_metrics[0].samples[0].value == 0.0


def _mesh_link(**overrides):
    link = {
        "uid": "l1", "node_1_uid": "n1", "node_2_uid": "n2", "state": "CONNECTED",
        "cur_data_rate_rx": 1000, "cur_data_rate_tx": 2000,
        "max_data_rate_rx": 3000, "max_data_rate_tx": 4000,
    }
    link.update(overrides)
    return link


def _mesh_topology(link=None, **node_overrides):
    master = {
        "uid": "n1", "device_name": "fritzbox", "is_meshed": True,
        "node_interfaces": [
            {"type": "WLAN", "name": "AP:5G:0", "node_links": [link or _mesh_link()]}
        ],
    }
    master.update(node_overrides)
    repeater = {"uid": "n2", "device_name": "repeater", "is_meshed": True, "node_interfaces": []}
    return {"nodes": [master, repeater]}


@patch("fritzexporter.tr064_remote.FritzConnection")
@patch("fritzexporter.fritzcapabilities.MeshTopology._mesh_list")
class TestMeshTopologyParsing:
    """Bad mesh data costs only what it affects, and never the scrape."""

    def _scrape(self, mock_fritzhosts, mock_fritzconnection, topology):
        def call_with_mesh(service, action, **kwargs):
            if service == "Hosts1" and action == "X_AVM-DE_GetMeshListPath":
                return {"NewX_AVM-DE_MeshListPath": "/meshlist.lua"}
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_with_mesh
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        mock_fritzhosts.return_value = topology

        collector = FritzCollector()
        collector.register(
            FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock")
        )
        registry = CollectorRegistry()
        registry.register(collector)
        # Renders the scrape the way Prometheus gets it; a bad sample value only
        # fails here, not in collect().
        return generate_latest(registry).decode()

    REACHABLE = 'fritz_device_reachable{friendly_name="FritzMock"} 1.0'

    @pytest.mark.parametrize(
        "topology",
        [
            pytest.param([], id="topology-is-a-list"),
            pytest.param({"nodes": "abc"}, id="nodes-is-a-string"),
        ],
    )
    def test_topology_of_the_wrong_shape_skips_mesh_metrics(
        self, mock_fritzhosts, mock_fritzconnection, caplog, topology
    ):
        output = self._scrape(mock_fritzhosts, mock_fritzconnection, topology)

        assert "fritz_mesh_link_available{" not in output
        assert self.REACHABLE in output
        assert "Could not read MeshTopology data from FritzMock (somehost)" in caplog.text

    @pytest.mark.parametrize(
        ("topology", "skipped"),
        [
            pytest.param(
                _mesh_topology(node_interfaces=5),
                "mesh node 0 skipped: mesh topology nodes[0] node_interfaces: expected a list",
                id="interfaces-not-a-list",
            ),
            pytest.param(
                _mesh_topology(link="l1"),
                "node_links[0] skipped: mesh topology nodes[0] node_interfaces[0] node_links[0]: "
                "expected an object",
                id="link-not-a-dict",
            ),
            pytest.param(
                _mesh_topology(_mesh_link(uid=["x"])),
                "mesh link 0/0/0 skipped:",
                id="unhashable-uid",
            ),
        ],
    )
    def test_unreadable_node_or_link_is_skipped(
        self, mock_fritzhosts, mock_fritzconnection, caplog, topology, skipped
    ):
        output = self._scrape(mock_fritzhosts, mock_fritzconnection, topology)

        assert "fritz_mesh_link_available{" not in output
        assert self.REACHABLE in output
        assert "Skipped part of the MeshTopology data from FritzMock (somehost)" in caplog.text
        assert skipped in caplog.text

    @pytest.mark.parametrize(
        ("link", "missing"),
        [
            pytest.param(
                _mesh_link(cur_data_rate_rx="invalid"),
                'fritz_mesh_link_current_data_rate_kbps{direction="rx"',
                id="rate-text",
            ),
            pytest.param(
                _mesh_link(max_data_rate_rx={"not": "a number"}),
                'fritz_mesh_link_max_data_rate_kbps{direction="rx"',
                id="rate-dict",
            ),
        ],
    )
    def test_unreadable_rate_skips_only_that_sample(
        self, mock_fritzhosts, mock_fritzconnection, caplog, link, missing
    ):
        output = self._scrape(mock_fritzhosts, mock_fritzconnection, _mesh_topology(link))

        assert missing not in output
        # The link and its other samples are still there.
        assert "fritz_mesh_link_available{" in output
        assert 'fritz_mesh_link_current_data_rate_kbps{direction="tx"' in output
        assert 'fritz_mesh_link_max_data_rate_kbps{direction="tx"' in output
        assert "expected a number" in caplog.text
        assert "Skipped part of the MeshTopology data from FritzMock (somehost)" in caplog.text

    def test_link_labels_come_from_the_first_endpoint_in_device_order(
        self, mock_fritzhosts, mock_fritzconnection
    ):
        # Each link is listed under both endpoints, with each side's own interface.
        # The master comes first in the topology, although its uid sorts last; its
        # side must keep supplying the labels, as in earlier releases.
        link = _mesh_link(uid="l1", node_1_uid="z-master", node_2_uid="a-rep")
        topology = {
            "nodes": [
                {
                    "uid": "z-master", "device_name": "fritzbox", "is_meshed": True,
                    "node_interfaces": [{"type": "WLAN", "name": "AP:5G:0", "node_links": [link]}],
                },
                {
                    "uid": "a-rep", "device_name": "repeater", "is_meshed": True,
                    "node_interfaces": [{"type": "WLAN", "name": "UPLINK:5G:0", "node_links": [link]}],
                },
            ]
        }

        output = self._scrape(mock_fritzhosts, mock_fritzconnection, topology)

        [available] = [l for l in output.splitlines() if l.startswith("fritz_mesh_link_available{")]
        assert 'interface="AP:5G:0"' in available
        assert 'node="fritzbox"' in available

    def test_numeric_strings_and_non_string_names_are_accepted(
        self, mock_fritzhosts, mock_fritzconnection, caplog
    ):
        topology = _mesh_topology(
            _mesh_link(cur_data_rate_rx="1500", max_data_rate_tx=4000.5), device_name=7590
        )

        output = self._scrape(mock_fritzhosts, mock_fritzconnection, topology)

        assert (
            'fritz_mesh_link_current_data_rate_kbps{direction="rx",friendly_name="FritzMock",'
            'interface="AP:5G:0",node="7590",peer="repeater",serial="1234567890",type="WLAN"} 1500.0'
        ) in output
        assert (
            'fritz_mesh_link_max_data_rate_kbps{direction="tx",friendly_name="FritzMock",'
            'interface="AP:5G:0",node="7590",peer="repeater",serial="1234567890",type="WLAN"} 4000.5'
        ) in output
        assert "Could not read MeshTopology data" not in caplog.text


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestConcurrentScrapes:
    """Two Prometheus instances scraping in parallel must not deadlock.

    Regression: with the collector lock held across the whole scrape (including
    blocking TR-064 calls), a first scrape that hangs inside a call blocks every
    concurrent scrape in ``_collect_lock.acquire()`` forever — all Prometheus
    instances scraping the exporter stop receiving metrics until it is restarted.
    The second scrape must give up after the (bounded) lock deadline instead.
    """

    def _create_collector(self, mock_fritzconnection: MagicMock) -> FritzCollector:
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock")
        # These tests exercise the collector lock, not the web UI fetch path.
        # Disable the web UI client so the REST capabilities (enabled on this
        # device via WANCommonInterfaceConfig1) skip their HTTP fetch.
        device.webui_client = None
        collector.register(device)
        return collector

    def test_second_parallel_scrape_returns_when_first_scrape_is_wedged(
        self, mock_fritzconnection: MagicMock, monkeypatch
    ):
        # Shrink the lock deadline so the test does not wait the real 30s.
        monkeypatch.setattr("fritzexporter.fritzdevice.SCRAPE_LOCK_TIMEOUT", 0.2)

        entered_block = threading.Event()
        release = threading.Event()  # never set -> first scrape stays wedged

        collector = self._create_collector(mock_fritzconnection)

        def wedged_call_action(service, action, **kwargs):
            if (service, action) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties"):
                entered_block.set()
                release.wait()  # the device "hangs" inside the TR-064 call
            return call_action_mock(service, action, **kwargs)

        mock_fritzconnection.return_value.call_action.side_effect = wedged_call_action

        results: list = []
        failures: list = []

        def scrape() -> None:
            try:
                results.append(list(collector.collect()))
            except BaseException as exc:  # noqa: BLE001 - surface thread failures
                failures.append(exc)

        first = threading.Thread(target=scrape)
        first.start()
        entered_ok = entered_block.wait(5)

        # Second Prometheus instance scrapes while the first scrape is wedged.
        second = threading.Thread(target=scrape)
        second.start()

        try:
            assert entered_ok, "first scrape never entered the blocked call"
            # The second scrape must release instead of blocking forever.
            second.join(timeout=5)
            assert not second.is_alive(), "concurrent scrape deadlocked behind wedged scrape"
            assert not failures, failures
        finally:
            # Always let the wedged first scrape finish so it can never leak out
            # of this test (a leaked non-daemon thread would hang the test run).
            release.set()
            first.join(timeout=5)
        assert not first.is_alive()

    def test_wedged_device_marked_unreachable_and_parallel_scrapes_complete(
        self, mock_fritzconnection: MagicMock
    ):
        # Models the new finite (10s) default connection_timeout: the hung call
        # raises after the deadline instead of blocking forever, so the first
        # scrape completes, marks the device unreachable, and parallel scrapes
        # finish as well.
        collector = self._create_collector(mock_fritzconnection)

        def timeout_call_action(service, action, **kwargs):
            if (service, action) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties"):
                raise FritzUnreachableError("device unreachable (timed out)")
            return call_action_mock(service, action, **kwargs)

        mock_fritzconnection.return_value.call_action.side_effect = timeout_call_action

        results: list = []

        def scrape() -> None:
            results.append(list(collector.collect()))

        threads = [threading.Thread(target=scrape) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert not any(thread.is_alive() for thread in threads), "scrape deadlocked"
        assert len(results) == 2
        for metrics in results:
            reachable = [m for m in metrics if m.name == "fritz_device_reachable"]
            assert reachable
            assert reachable[0].samples[0].value == 0.0


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestGetConnectionMode:
    """Tests for FritzDevice.add_connection_mode()"""

    def _mode(self, device: FritzDevice) -> GaugeMetricFamily | None:
        family = GaugeMetricFamily("fritz_connection_mode", "", labels=["serial", "friendly_name"])
        info = GaugeMetricFamily(
            "fritz_wan_access_type_info", "", labels=["serial", "friendly_name", "access_type"]
        )
        device.add_connection_mode(family, info)
        # The access type is in its own info metric.
        self.access_types = [s.labels["access_type"] for s in info.samples]
        return family if family.samples else None

    def _create_device(self, mock_fc: MagicMock) -> tuple:
        fc = mock_fc.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        return device, fc

    def test_get_connection_mode_dsl(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Up", "NewWANAccessType": "DSL"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = self._mode(device)

        # Check
        assert metric is not None
        assert metric.name == "fritz_connection_mode"
        assert metric.samples[0].value == 1
        assert metric.samples[0].labels["serial"] == "1234567890"
        assert metric.samples[0].labels["friendly_name"] == "FritzMock"
        assert self.access_types == ["DSL"]

    def test_get_connection_mode_mobile_fallback(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Down", "NewWANAccessType": "X_AVM-DE_Mobile"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = self._mode(device)

        # Check
        assert metric is not None
        assert metric.samples[0].value == 2
        assert metric.samples[0].labels["serial"] == "1234567890"
        assert self.access_types == ["X_AVM-DE_Mobile"]

    def test_get_connection_mode_mobile_only(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Up", "NewWANAccessType": "X_AVM-DE_Mobile"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = self._mode(device)

        # Check
        assert metric is not None
        assert metric.samples[0].value == 3

    def test_get_connection_mode_fiber(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Up", "NewWANAccessType": "X_AVM-DE_Fiber"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = self._mode(device)

        # Check
        assert metric is not None
        assert metric.samples[0].value == 4
        assert self.access_types == ["X_AVM-DE_Fiber"]

    def test_get_connection_mode_cable(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Up", "NewWANAccessType": "X_AVM-DE_Cable"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = self._mode(device)

        # Check
        assert metric is not None
        assert metric.samples[0].value == 5
        assert self.access_types == ["X_AVM-DE_Cable"]

    def test_get_connection_mode_offline(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Down", "NewWANAccessType": "DSL"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = self._mode(device)

        # Check
        assert metric is not None
        assert metric.samples[0].value == 0

    @pytest.mark.parametrize("exc", [FritzServiceError, FritzActionError])
    def test_get_connection_mode_keeps_device_available_on_missing_wan(self, mock_fc: MagicMock, exc):
        # A device without a WAN interface (e.g. a mesh repeater) reports no
        # connection mode, but must NOT be marked unavailable — otherwise the
        # collect loop would skip all its other capabilities every scrape.
        # Prepare
        device, fc = self._create_device(mock_fc)

        def raise_missing_service(s, a, **kw):
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties"):
                raise exc("no WAN service on this device")
            return call_action_mock(s, a, **kw)

        fc.call_action.side_effect = raise_missing_service

        # Act
        metric = self._mode(device)

        # Check: no metric, but the device stays available
        assert metric is None
        assert device.available is True

    def test_get_connection_mode_returns_none_on_exception(self, mock_fc: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)
        device, fc = self._create_device(mock_fc)

        def raise_connection_exception(s, a, **kw):
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties"):
                raise FritzUnreachableError("Connection failed")
            return call_action_mock(s, a, **kw)

        fc.call_action.side_effect = raise_connection_exception

        # Act
        metric = self._mode(device)

        # Check
        assert metric is None
        assert device.available is False
        assert (
            FRITZDEVICE_LOG_SOURCE,
            logging.WARNING,
            "Device FritzMock (somehost) is unreachable: Connection failed",
        ) in caplog.record_tuples


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestFritzDeviceAuthError:
    def test_should_raise_authorization_error_on_get_device_info(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value

        def auth_error_mock(service, action, **kwargs):
            if service == "DeviceInfo1" and action == "GetInfo":
                raise FritzAuthorizationError("Not authorized")
            return {}

        fc.call_action.side_effect = auth_error_mock
        fc.services = create_fc_services({})

        # Act
        creds = FritzCredentials("somehost", "someuser", "password")
        with pytest.raises(FritzAuthorizationError):
            FritzDevice(creds, "FritzMock", host_info=False)

        collector = FritzCollector()
        collector.add_device(DeviceSpec(FritzCredentials("somehost", "someuser", "password"), "FritzMock"))

        # Check: one error, with the hint
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == [
            "Failed to initialize device FritzMock (somehost), it will be reported as down: "
            "FritzAuthorizationError: Not authorized (check username and password)"
        ]


class TestNoPasswordInRepr:
    def test_device_objects(self):
        creds = FritzCredentials("fritz.box", "user", "s3cret-pw")
        spec = DeviceSpec(creds, "Box")
        for obj in (creds, spec, OfflineDevice(spec)):
            assert "s3cret-pw" not in repr(obj)

    def test_device_config(self):
        from fritzexporter.config import DeviceConfig

        config = DeviceConfig(hostname="fritz.box", username="user", password="s3cret-pw", name="Box")
        assert "s3cret-pw" not in repr(config)


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestMeshListFetch:
    """The mesh list: path over TR-064, the list itself over HTTP from the mesh master."""

    TOPOLOGY = '{"nodes": []}'

    def _scrape(self, mock_fritzconnection, *, path_error=None, reply=None, get_error=None):
        def call_with_mesh(service, action, **kwargs):
            if service == "Hosts1" and action == "X_AVM-DE_GetMeshListPath":
                if path_error is not None and device_ready:
                    raise path_error
                return {"NewX_AVM-DE_MeshListPath": "/meshlist.lua"}
            return call_action_mock(service, action, **kwargs)

        device_ready = False  # the probe during setup must succeed
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_with_mesh
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        fc.session.get.side_effect = get_error
        fc.session.get.return_value = reply
        collector = FritzCollector()
        collector.register(FritzDevice(FritzCredentials("somehost", "u", "p"), "FritzMock"))
        device_ready = True
        by_name = {m.name: m for m in collector.collect()}
        problems = {
            (s.labels["capability"], s.labels["level"]) for s in by_name["fritz_read_problem"].samples
        }
        reachable = by_name["fritz_device_reachable"].samples[0].value
        return by_name, problems, reachable

    def test_topology_from_the_mesh_master(self, mock_fritzconnection):
        topology = json.dumps(_mesh_topology())
        by_name, problems, reachable = self._scrape(mock_fritzconnection, reply=web_reply(topology))
        assert len(by_name["fritz_mesh_link_available"].samples) == 1
        assert ("MeshTopology", "skipped") not in problems
        assert reachable == 1.0

    def test_other_nodes_answer_404_quietly(self, mock_fritzconnection):
        by_name, problems, _ = self._scrape(mock_fritzconnection, reply=web_reply(status=404))
        assert by_name["fritz_mesh_link_available"].samples == []
        assert not any(capability == "MeshTopology" for capability, _ in problems)

    @pytest.mark.parametrize(
        "error",
        [
            RequestsConnectionError("aborted"),
            FritzUnreachableError("Fritz device did not answer (ReadTimeout)"),
        ],
    )
    def test_list_not_answering_skips_only_the_mesh(self, mock_fritzconnection, caplog, error):
        _, problems, reachable = self._scrape(mock_fritzconnection, get_error=error)
        assert ("MeshTopology", "skipped") in problems
        assert reachable == 1.0
        assert "mesh list request failed: Fritz device did not answer" in caplog.text

    def test_error_status_from_the_master_is_a_read_problem(self, mock_fritzconnection, caplog):
        # Only 404 means "not the mesh master"; a failing master must not look like one.
        _, problems, reachable = self._scrape(mock_fritzconnection, reply=web_reply(status=503))
        assert ("MeshTopology", "skipped") in problems
        assert reachable == 1.0
        assert "mesh list request failed: HTTP 503" in caplog.text

    def test_bad_json_is_a_read_problem_not_a_missing_answer(self, mock_fritzconnection, caplog):
        _, problems, reachable = self._scrape(mock_fritzconnection, reply=web_reply("{oops"))
        assert ("MeshTopology", "skipped") in problems
        assert reachable == 1.0
        assert "mesh list: not valid JSON" in caplog.text
        assert "mesh list request failed" not in caplog.text

    def test_path_not_answering_makes_the_device_unreachable(self, mock_fritzconnection):
        _, _, reachable = self._scrape(
            mock_fritzconnection,
            path_error=FritzUnreachableError("Fritz device did not answer (ReadTimeout)"),
        )
        assert reachable == 0.0


class TestDuplicateSeries:
    """Two samples with the same labels would make Prometheus reject the scrape."""

    def test_mesh_links_to_nodes_of_the_same_name_keep_the_first(self):
        from fritzexporter.fritzcapabilities import _MeshParser

        def node(uid, name, links=()):
            interfaces = [{"type": "WLAN", "name": "AP:5G:0", "node_links": list(links)}]
            return {"uid": uid, "device_name": name, "is_meshed": True, "node_interfaces": interfaces}

        topology = {
            "nodes": [
                node("box", "FRITZ!Box", [
                    _mesh_link(uid="l1", node_1_uid="box", node_2_uid="r1"),
                    _mesh_link(uid="l2", node_1_uid="box", node_2_uid="r2"),
                ]),
                node("r1", "FRITZ!Repeater"),
                node("r2", "FRITZ!Repeater"),
            ]
        }
        issues = Issues()

        links = _MeshParser(topology, issues).links()

        assert [link.labels for link in links] == [["FRITZ!Box", "FRITZ!Repeater", "WLAN", "AP:5G:0"]]
        assert issues.messages == [
            "mesh link 0/0/1 skipped: same node names and interface as another link "
            "(give the mesh nodes different names)"
        ]


@patch("fritzexporter.tr064_remote.FritzConnection")
def test_wifi_client_read_twice_during_a_scan_is_kept_once(mock_fritzconnection: MagicMock):
    # A client joined at a lower index mid-scan: the next index repeats the last one.
    def assoc(service, action, **kwargs):
        if action == "GetTotalAssociations":
            return {"NewTotalAssociations": 2 if service == "WLANConfiguration1" else 0}
        if action == "GetGenericAssociatedDeviceInfo":
            return {
                "NewAssociatedDeviceMACAddress": "AA:BB:CC:00:00:01",
                "NewAssociatedDeviceIPAddress": "192.168.178.9",
                "NewX_AVM-DE_SignalStrength": 70,
                "NewX_AVM-DE_Speed": 100,
            }
        return call_action_mock(service, action, **kwargs)

    fc = mock_fritzconnection.return_value
    fc.call_action.side_effect = assoc
    fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
    collector = FritzCollector()
    collector.register(
        FritzDevice(FritzCredentials("somehost", "u", "p"), "FritzMock", wifi_client_info=True)
    )

    by_name = {m.name: m for m in collector.collect()}

    assert len(by_name["fritz_wifi_client_signal_strength"].samples) == 1
    problems = {s.labels["capability"] for s in by_name["fritz_read_problem"].samples}
    assert "WlanAssociatedDevices" not in problems


@patch("fritzexporter.tr064_remote.FritzConnection")
def test_wifi_client_count_that_cannot_be_read_skips_that_wlan_and_says_so(
    mock_fritzconnection: MagicMock,
):
    def assoc(service, action, **kwargs):
        if action == "GetTotalAssociations" and service == "WLANConfiguration1":
            return {"NewTotalAssociations": ""}
        return call_action_mock(service, action, **kwargs)

    fc = mock_fritzconnection.return_value
    fc.call_action.side_effect = assoc
    fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
    collector = FritzCollector()
    collector.register(
        FritzDevice(FritzCredentials("somehost", "u", "p"), "FritzMock", wifi_client_info=True)
    )

    by_name = {m.name: m for m in collector.collect()}

    problems = {
        (s.labels["capability"], s.labels["level"]) for s in by_name["fritz_read_problem"].samples
    }
    assert ("WlanAssociatedDevices", "partial") in problems
