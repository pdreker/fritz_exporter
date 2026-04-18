import logging
from unittest.mock import MagicMock, patch

import pytest
from fritzconnection.core.exceptions import (
    FritzActionError,
    FritzArrayIndexError,
    FritzHttpInterfaceError,
    FritzServiceError,
)
from prometheus_client.core import Metric

from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice
from fritzexporter.fritzcapabilities import FritzCapabilities

from .fc_services_mock import (
    call_action_mock,
    call_http_mock,
    create_fc_services,
    fc_services_capabilities,
    fc_services_devices,
)


@patch("fritzexporter.fritzdevice.FritzConnection")
class TestFritzCapabilitiesMethods:
    """Tests for FritzCapabilities container methods."""

    def test_iter_over_capabilities(self, mock_fritzconnection: MagicMock):
        # Prepare
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        fd = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Act - iterate using __iter__
        cap_names = list(fd.capabilities)

        # Check
        assert "DeviceInfo" in cap_names
        assert "HomeAutomation" in cap_names
        assert len(cap_names) > 0

    def test_len_of_capabilities(self, mock_fritzconnection: MagicMock):
        # Prepare
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        fd = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Act - get length using __len__
        num_caps = len(fd.capabilities)

        # Check
        assert num_caps == 15  # All known capabilities

    def test_empty_capabilities_is_true_when_all_absent(self, mock_fritzconnection: MagicMock):
        # Prepare - use an empty service set so no capability is present
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services({})

        caps = FritzCapabilities()  # No device, so nothing is checked

        # Check - empty() should return True since no capability is present
        assert caps.empty() is True

    def test_capability_check_disables_when_call_action_raises(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare - service is present in services dict but call_action raises FritzServiceError
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value

        def error_on_hosts(service, action, **kwargs):
            if service == "Hosts1" and action == "GetHostNumberOfEntries":
                raise FritzServiceError("Mock FritzServiceError for HostNumberOfEntries")
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = error_on_hosts
        # Include Hosts1/GetHostNumberOfEntries so the service IS present in services dict
        services = dict(fc_services_devices["FritzBox 7590"])
        fc.services = create_fc_services(services)

        # Act
        fd = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Check - HostNumberOfEntries capability should be disabled
        assert fd.capabilities["HostNumberOfEntries"].present is False

        # Warning should have been logged
        assert any(
            "disabling metrics at service Hosts1, action GetHostNumberOfEntries" in record.message
            for record in caplog.records
            if record.levelno == logging.WARNING
        )


@patch("fritzexporter.fritzdevice.FritzConnection")
class TestUserInterfaceCapability:
    """Tests for UserInterface capability edge cases."""

    def test_user_interface_no_update_available(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare - return no upgrade available
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value

        def no_update_mock(service, action, **kwargs):
            if service == "UserInterface1" and action == "GetInfo":
                return {"NewUpgradeAvailable": 0, "NewX_AVM-DE_Version": ""}
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = no_update_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - fritz_update_available should have newsoftwareversion = "n/a"
        update_metrics = [m for m in metrics if m.name == "fritz_update_available"]
        assert len(update_metrics) == 1
        assert update_metrics[0].samples[0].value == 0
        assert update_metrics[0].samples[0].labels["newsoftwareversion"] == "n/a"


@patch("fritzexporter.fritzdevice.FritzConnection")
class TestHostInfoCapability:
    """Tests for HostInfo capability edge cases."""

    def test_host_info_with_empty_ip(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare - host has empty IP address
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value

        def empty_ip_mock(service, action, **kwargs):
            if service == "Hosts1" and action == "GetGenericHostEntry":
                return {
                    "NewIPAddress": "",  # empty IP
                    "NewMACAddress": "AA:BB:CC:DD:EE:FF",
                    "NewHostName": "no-ip-host",
                    "NewActive": 0,
                }
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = empty_ip_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=True)
        collector.register(device)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - fritz_host_active and fritz_host_speed should be present
        host_active_metrics = [m for m in metrics if m.name == "fritz_host_active"]
        assert len(host_active_metrics) == 1
        # With empty IP, interface/port/model should be "n/a"
        sample = host_active_metrics[0].samples[0]
        assert sample.labels["interface"] == "n/a"
        assert sample.labels["port"] == "n/a"
        assert sample.labels["model"] == "n/a"
        assert sample.value == 0.0


@patch("fritzexporter.fritzdevice.FritzConnection")
class TestHomeAutomationCapability:
    """Tests for HomeAutomation capability edge cases."""

    def _make_ha_response(
        self,
        multimeter_enabled=True,
        multimeter_valid=True,
        temperature_enabled=True,
        temperature_valid=True,
        switch_enabled=True,
        switch_valid=True,
        hkr_enabled=True,
        hkr_valid=True,
    ) -> dict:
        return {
            "NewAIN": "123456789012",
            "NewDeviceId": 123,
            "NewFunctionBitMask": 1,
            "NewFirmwareVersion": "1.2",
            "NewManufacturer": "AVM",
            "NewProductName": "MockDevice",
            "NewDeviceName": "MockDeviceName",
            "NewPresent": "CONNECTED",
            "NewMultimeterIsEnabled": "ENABLED" if multimeter_enabled else "DISABLED",
            "NewMultimeterIsValid": "VALID" if multimeter_valid else "INVALID",
            "NewMultimeterPower": 1234,
            "NewMultimeterEnergy": 12345,
            "NewTemperatureIsEnabled": "ENABLED" if temperature_enabled else "DISABLED",
            "NewTemperatureIsValid": "VALID" if temperature_valid else "INVALID",
            "NewTemperatureCelsius": 234,
            "NewTemperatureOffset": 0,
            "NewSwitchIsEnabled": "ENABLED" if switch_enabled else "DISABLED",
            "NewSwitchIsValid": "VALID" if switch_valid else "INVALID",
            "NewSwitchState": "ON",
            "NewSwitchMode": "MANUAL",
            "NewSwitchLock": False,
            "NewHkrIsEnabled": "ENABLED" if hkr_enabled else "DISABLED",
            "NewHkrIsValid": "VALID" if hkr_valid else "INVALID",
            "NewHkrIsTemperature": 245,
            "NewHkrSetVentilStatus": "OPEN",
            "NewHkrSetTemperature": 234,
            "NewHkrReduceVentilStatus": "CLOSED",
            "NewHkrReduceTemperature": 234,
            "NewHkrComfortVentilStatus": "OPEN",
            "NewHkrComfortTemperature": 234,
        }

    def _setup_ha_device(self, mock_fc, ha_response: dict) -> tuple:
        fc = mock_fc.return_value

        def ha_mock(service, action, **kwargs):
            if service == "X_AVM-DE_Homeauto1" and action == "GetGenericDeviceInfos":
                if kwargs.get("NewIndex", 0) == 0:
                    return ha_response
                raise FritzArrayIndexError
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = ha_mock
        fc.call_http.side_effect = call_http_mock
        fc.services = create_fc_services(fc_services_capabilities["HomeAutomation"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)
        return collector, device, fc

    def test_homeautomation_with_disabled_multimeter(self, mock_fritzconnection: MagicMock):
        # Prepare
        ha_response = self._make_ha_response(multimeter_enabled=False)
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, ha_response)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - multimeter metrics should have no samples
        power_metrics = [m for m in metrics if m.name == "fritz_ha_multimeter_power_W"]
        assert len(power_metrics) == 1
        assert len(power_metrics[0].samples) == 0

    def test_homeautomation_with_invalid_multimeter(self, mock_fritzconnection: MagicMock):
        # Prepare
        ha_response = self._make_ha_response(multimeter_valid=False)
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, ha_response)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - multimeter metrics should have no samples
        power_metrics = [m for m in metrics if m.name == "fritz_ha_multimeter_power_W"]
        assert len(power_metrics) == 1
        assert len(power_metrics[0].samples) == 0

    def test_homeautomation_with_disabled_temperature(self, mock_fritzconnection: MagicMock):
        # Prepare
        ha_response = self._make_ha_response(temperature_enabled=False)
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, ha_response)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - temperature metrics should have no samples
        temp_metrics = [m for m in metrics if m.name == "fritz_ha_temperature_C"]
        assert len(temp_metrics) == 1
        assert len(temp_metrics[0].samples) == 0

    def test_homeautomation_with_disabled_switch(self, mock_fritzconnection: MagicMock):
        # Prepare
        ha_response = self._make_ha_response(switch_enabled=False)
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, ha_response)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - switch metrics should have no samples
        switch_metrics = [m for m in metrics if m.name == "fritz_ha_switch_state"]
        assert len(switch_metrics) == 1
        assert len(switch_metrics[0].samples) == 0

    def test_homeautomation_with_disabled_heater(self, mock_fritzconnection: MagicMock):
        # Prepare
        ha_response = self._make_ha_response(hkr_enabled=False)
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, ha_response)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - heater metrics should have no samples
        heater_metrics = [m for m in metrics if m.name == "fritz_ha_heater_temperature_C"]
        assert len(heater_metrics) == 1
        assert len(heater_metrics[0].samples) == 0

    def test_homeautomation_with_fritz_http_interface_error(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)
        ha_response = self._make_ha_response()
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, ha_response)

        # Make call_http raise FritzHttpInterfaceError
        fc.call_http.side_effect = FritzHttpInterfaceError("HTTP interface error")

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - should still produce device present metric, no battery metrics
        device_present = [m for m in metrics if m.name == "fritz_ha_device_present"]
        assert len(device_present) == 1
        assert len(device_present[0].samples) == 1

        battery_metrics = [m for m in metrics if m.name == "fritz_ha_battery_level_percent"]
        assert len(battery_metrics) == 1
        assert len(battery_metrics[0].samples) == 0

        # Warning should be logged
        assert any(
            "Got FritzHttpInterfaceError" in record.message
            for record in caplog.records
        )

    def test_homeautomation_no_content_in_http_result(self, mock_fritzconnection: MagicMock):
        # Prepare
        ha_response = self._make_ha_response()
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, ha_response)

        # Make call_http return a response without 'content'
        fc.call_http.side_effect = lambda action, ain, **kw: {"status": "ok"}

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - battery metrics should have no samples since no content
        battery_metrics = [m for m in metrics if m.name == "fritz_ha_battery_level_percent"]
        assert len(battery_metrics) == 1
        assert len(battery_metrics[0].samples) == 0

    def test_homeautomation_no_battery_low_in_http_data(self, mock_fritzconnection: MagicMock):
        # Prepare - return XML without batterylow element
        ha_response = self._make_ha_response()
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, ha_response)

        # Make call_http return XML without batterylow
        fc.call_http.side_effect = lambda action, ain, **kw: {
            "content": """<?xml version="1.0" encoding="utf-8"?>
            <device>
                <present>1</present>
                <name>Fritz!DECT 200</name>
                <battery>75</battery>
            </device>""",
            "content-type": "text/xml",
            "encoding": "utf-8",
        }

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - battery_level has samples, battery_low does not
        battery_level = [m for m in metrics if m.name == "fritz_ha_battery_level_percent"]
        assert len(battery_level) == 1
        assert len(battery_level[0].samples) == 1
        assert battery_level[0].samples[0].value == 75.0

        battery_low = [m for m in metrics if m.name == "fritz_ha_battery_low"]
        assert len(battery_low) == 1
        assert len(battery_low[0].samples) == 0

    def test_homeautomation_no_battery_in_xml(self, mock_fritzconnection: MagicMock):
        # Prepare - return XML without battery element (but has content)
        ha_response = self._make_ha_response()
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, ha_response)

        fc.call_http.side_effect = lambda action, ain, **kw: {
            "content": """<?xml version="1.0" encoding="utf-8"?>
            <device>
                <present>1</present>
                <name>Fritz!DECT 200</name>
            </device>""",
            "content-type": "text/xml",
            "encoding": "utf-8",
        }

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - neither battery_level nor battery_low should have samples
        battery_level = [m for m in metrics if m.name == "fritz_ha_battery_level_percent"]
        assert len(battery_level) == 1
        assert len(battery_level[0].samples) == 0

        battery_low = [m for m in metrics if m.name == "fritz_ha_battery_low"]
        assert len(battery_low) == 1
        assert len(battery_low[0].samples) == 0
