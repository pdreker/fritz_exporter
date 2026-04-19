import logging
from pprint import pprint
from unittest.mock import MagicMock, call, patch

import pytest
from fritzconnection.core.exceptions import (
    FritzAuthorizationError,
    FritzConnectionException,
    FritzServiceError,
)
from prometheus_client.core import Metric

from fritzexporter.exceptions import FritzDeviceHasNoCapabilitiesError
from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice
from fritzexporter.fritz_aha import parse_aha_device_xml

from .fc_services_mock import (
    call_action_mock,
    call_action_no_basic_mock,
    call_http_mock,
    create_fc_services,
    fc_services_capabilities,
    fc_services_devices,
    fc_services_no_basic_info,
)

FRITZDEVICE_LOG_SOURCE = "fritzexporter.fritzdevice"


@patch("fritzexporter.fritzdevice.FritzConnection")
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
                f"Capability {capability} in WLAN 1 set to True on device somehost",
            ) in caplog.record_tuples
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} in WLAN 2 set to True on device somehost",
            ) in caplog.record_tuples
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} in WLAN 3 set to False on device somehost",
            ) in caplog.record_tuples
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} in WLAN 4 set to False on device somehost",
            ) in caplog.record_tuples
        else:
            assert (
                LOGSOURCE,
                logging.DEBUG,
                f"Capability {capability} set to True on device somehost",
            ) in caplog.record_tuples

    def test_should_raise_fritz_connection_exception(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        mock_fritzconnection.side_effect = FritzConnectionException("somehost: connection refused")

        # Act
        with pytest.raises(FritzConnectionException):
            _ = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Check
        assert (
            FRITZDEVICE_LOG_SOURCE,
            logging.ERROR,
            "unable to connect to somehost.",
        ) in caplog.record_tuples

    def test_should_invalidate_presented_service(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = FritzServiceError("Mock FritzServiceError")
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        with pytest.raises(FritzDeviceHasNoCapabilitiesError):
            _ = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=True)

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
            address="somehost", user="someuser", password="password"
        )

    def test_should_complain_about_password(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)
        password: str = "123456789012345678901234567890123"

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        _ = FritzDevice(FritzCredentials("somehost", "someuser", password), "Fritz!Mock", host_info=False)

        # Check
        assert (
            FRITZDEVICE_LOG_SOURCE,
            logging.WARN,
            "Password is longer than 32 characters! Login may not succeed, please see README!",
        ) in caplog.record_tuples

    def test_should_find_no_capabilities(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)
        password: str = "123456789012345678901234567890123"

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services({})

        # Act
        with pytest.raises(FritzDeviceHasNoCapabilitiesError):
            _ = FritzDevice(FritzCredentials("somehost", "someuser", password), "FritzMock", host_info=False)

        # Check
        assert (
            FRITZDEVICE_LOG_SOURCE,
            logging.CRITICAL,
            "Device somehost has no detected capabilities. Exiting.",
        ) in caplog.record_tuples

    def test_should_detect_no_basic_info(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_no_basic_mock
        fc.services = create_fc_services(fc_services_no_basic_info)

        # Act
        _ = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Check
        assert (
            FRITZDEVICE_LOG_SOURCE,
            logging.ERROR,
            "Fritz Device somehost does not provide basic device "
            "info (Service: DeviceInfo1, Action: GetInfo)."
            "Serial number and model name will be unavailable.",
        ) in caplog.record_tuples

    def test_should_correctly_parse_aha_xml(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        deviceinfo = """<?xml version="1.0" encoding="utf-8"?>
        <device>
            <present>1</present>
            <name>Fritz!DECT 200
            </name>
            <manufacturer>AVM</manufacturer>
            <manufacturerURL>http://www.avm.de</manufacturerURL>
            <model>Fritz!DECT 200</model>
            <battery>100</battery>
            <batterylow>0</batterylow>
        </device>
        """
        # Act
        device_data = parse_aha_device_xml(deviceinfo)

        # Check
        assert device_data["battery_level"] == "100"
        assert device_data["battery_low"] == "0"

    def test_should_correctly_parse_aha_xml_when_empty(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        deviceinfo = """<?xml version="1.0" encoding="utf-8"?>
        <device>
        </device>
        """
        # Act
        device_data = parse_aha_device_xml(deviceinfo)

        # Check
        assert "battery_level" not in device_data
        assert "battery_low" not in device_data



@patch("fritzexporter.fritzdevice.FritzConnection")
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
            "WanPPPConnectionStatus",
            "WanCommonInterfaceConfig",
            "WanCommonInterfaceDataBytes",
            "WanCommonInterfaceByteRate",
            "WanCommonInterfaceDataPackets",
            "WlanConfigurationInfo",
            "HostInfo",
            "HomeAutomation",
        ]

        assert list(collector.capabilities.capabilities.keys()) == all_capas

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
            for s in m.samples:
                assert "serial" in s.labels
                assert s.labels["serial"] == "1234567890"
                assert "friendly_name" in s.labels
                assert s.labels["friendly_name"] == "FritzMock"

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

    def test_should_exit_when_no_devices_registered(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        # Act
        collector = FritzCollector()

        # Check
        with pytest.raises(SystemExit) as exc_info:
            list(collector.collect())

        assert exc_info.value.code == 1

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

        # Check: fritz_device_up should be 1 for a reachable device
        device_up_metrics = [m for m in metrics if m.name == "fritz_device_up"]
        assert len(device_up_metrics) == 1
        assert device_up_metrics[0].samples[0].value == 1.0
        assert device_up_metrics[0].samples[0].labels["serial"] == "1234567890"
        assert device_up_metrics[0].samples[0].labels["friendly_name"] == "FritzMock"

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
        fc.call_action.side_effect = FritzConnectionException("device unreachable")

        # Act: collect() should not raise, but return status 0
        metrics: list[Metric] = list(collector.collect())

        # Check: fritz_device_up should be 0
        device_up_metrics = [m for m in metrics if m.name == "fritz_device_up"]
        assert len(device_up_metrics) == 1
        assert device_up_metrics[0].samples[0].value == 0.0
        assert device_up_metrics[0].samples[0].labels["friendly_name"] == "FritzMock"

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
                raise FritzConnectionException("device unreachable")
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = fail_on_device_info

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check: fritz_uptime metric is present but has no samples (DeviceInfo failed)
        uptime_metrics = [m for m in metrics if m.name == "fritz_uptime_seconds"]
        assert len(uptime_metrics) == 1
        assert len(uptime_metrics[0].samples) == 0

        # fritz_device_up should be 0 (device marked unavailable after DeviceInfo failure)
        device_up_metrics = [m for m in metrics if m.name == "fritz_device_up"]
        assert len(device_up_metrics) == 1
        assert device_up_metrics[0].samples[0].value == 0.0

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
        device_up_round1 = [m for m in metrics_round1 if m.name == "fritz_device_up"]
        assert device_up_round1[0].samples[0].value == 1.0

        # Second collection: device is unreachable
        fc.call_action.side_effect = FritzConnectionException("device unreachable")
        metrics_round2: list[Metric] = list(collector.collect())
        device_up_round2 = [m for m in metrics_round2 if m.name == "fritz_device_up"]
        assert device_up_round2[0].samples[0].value == 0.0

        # Third collection: device comes back up
        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = call_http_mock
        metrics_round3: list[Metric] = list(collector.collect())
        device_up_round3 = [m for m in metrics_round3 if m.name == "fritz_device_up"]
        assert device_up_round3[0].samples[0].value == 1.0

    def test_should_not_exit_when_only_offline_devices_registered(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare: no online devices, only offline
        caplog.set_level(logging.DEBUG)

        collector = FritzCollector()
        collector.register_offline("offlinehost", "OfflineDevice")

        # Act: collect() should not sys.exit
        metrics: list[Metric] = list(collector.collect())

        # Check: fritz_device_up = 0 for the offline device
        device_up_metrics = [m for m in metrics if m.name == "fritz_device_up"]
        assert len(device_up_metrics) == 1
        assert device_up_metrics[0].samples[0].value == 0.0
        assert device_up_metrics[0].samples[0].labels["serial"] == "n/a"
        assert device_up_metrics[0].samples[0].labels["friendly_name"] == "OfflineDevice"

    def test_should_not_yield_connection_mode_when_none(self, mock_fritzconnection: MagicMock, caplog):
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

        # Make get_connection_mode raise FritzConnectionException so it returns None
        original_side_effect = fc.call_action.side_effect

        def connection_mode_error(service, action, **kwargs):
            if service == "WANCommonInterfaceConfig" and action == "GetCommonLinkProperties":
                raise FritzConnectionException("no connection mode")
            return original_side_effect(service, action, **kwargs)

        fc.call_action.side_effect = connection_mode_error

        metrics: list[Metric] = list(collector.collect())

        # Check: no fritz_connection_mode metric should be in results
        metric_names = [m.name for m in metrics]
        assert "fritz_connection_mode" not in metric_names


@patch("fritzexporter.fritzdevice.FritzConnection")
class TestGetConnectionMode:
    """Tests for FritzDevice.get_connection_mode()"""

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
        metric = device.get_connection_mode()

        # Check
        assert metric is not None
        assert metric.name == "fritz_connection_mode"
        assert metric.samples[0].value == 1
        assert metric.samples[0].labels["serial"] == "1234567890"
        assert metric.samples[0].labels["friendly_name"] == "FritzMock"
        assert metric.samples[0].labels["access_type"] == "DSL"

    def test_get_connection_mode_mobile_fallback(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Down", "NewWANAccessType": "X_AVM-DE_Mobile"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = device.get_connection_mode()

        # Check
        assert metric is not None
        assert metric.samples[0].value == 2
        assert metric.samples[0].labels["serial"] == "1234567890"
        assert metric.samples[0].labels["access_type"] == "X_AVM-DE_Mobile"

    def test_get_connection_mode_mobile_only(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Up", "NewWANAccessType": "X_AVM-DE_Mobile"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = device.get_connection_mode()

        # Check
        assert metric is not None
        assert metric.samples[0].value == 3

    def test_get_connection_mode_offline(self, mock_fc: MagicMock):
        # Prepare
        device, fc = self._create_device(mock_fc)
        fc.call_action.side_effect = lambda s, a, **kw: (
            {"NewPhysicalLinkStatus": "Down", "NewWANAccessType": "DSL"}
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            else call_action_mock(s, a, **kw)
        )

        # Act
        metric = device.get_connection_mode()

        # Check
        assert metric is not None
        assert metric.samples[0].value == 0

    def test_get_connection_mode_returns_none_on_exception(self, mock_fc: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)
        device, fc = self._create_device(mock_fc)

        def raise_connection_exception(s, a, **kw):
            if (s, a) == ("WANCommonInterfaceConfig", "GetCommonLinkProperties"):
                raise FritzConnectionException("Connection failed")
            return call_action_mock(s, a, **kw)

        fc.call_action.side_effect = raise_connection_exception

        # Act
        metric = device.get_connection_mode()

        # Check
        assert metric is None
        assert (
            FRITZDEVICE_LOG_SOURCE,
            logging.WARNING,
            "Failed to retrieve connection mode info from somehost",
        ) in caplog.record_tuples


@patch("fritzexporter.fritzdevice.FritzConnection")
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
        with pytest.raises(FritzAuthorizationError):
            _ = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Check
        assert (
            FRITZDEVICE_LOG_SOURCE,
            logging.ERROR,
            "Not authorized to get device info from somehost. Check username/password.",
        ) in caplog.record_tuples


class TestParseAhaDeviceXml:
    def test_should_return_empty_dict_on_parse_error(self):
        # Prepare
        invalid_xml = "this is not valid xml <<<"

        # Act
        result = parse_aha_device_xml(invalid_xml)

        # Check
        assert result == {}
