import logging
from pprint import pprint
from unittest.mock import MagicMock, call, patch

import pytest
from fritzconnection.core.exceptions import FritzConnectionException, FritzServiceError
from prometheus_client.core import Metric

from fritzexporter.exceptions import FritzDeviceHasNoCapabilitiesError
from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice
from fritzexporter.fritz_aha import parse_aha_device_xml

from .fc_services_mock import (
    call_action_mock,
    call_action_no_basic_mock,
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
