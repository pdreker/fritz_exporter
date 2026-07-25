import logging
from unittest.mock import MagicMock, patch

import pytest
from fritzconnection.core.exceptions import (
    FritzActionError,
    FritzArgumentError,
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


@patch("fritzexporter.tr064_remote.FritzConnection")
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
        assert num_caps == 20  # All known capabilities

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


@patch("fritzexporter.tr064_remote.FritzConnection")
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


@patch("fritzexporter.tr064_remote.FritzConnection")
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

    def test_host_table_shrinking_mid_scan_keeps_device_reachable(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # A host leaving mid-scan makes GetGenericHostEntry raise FritzArrayIndexError
        # (UPnP errorCode 713, SpecifiedArrayIndexInvalid). This is a benign race on the
        # DHCP server's host table, not a device outage: the box is fully reachable, the
        # host table just shrank between GetHostNumberOfEntries and the per-index reads.
        # The device must stay reachable and the hosts read before the error must still
        # be exported.
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value

        def shrinking_table_mock(service, action, **kwargs):
            if service == "Hosts1" and action == "GetHostNumberOfEntries":
                return {"NewHostNumberOfEntries": 3}
            if service == "Hosts1" and action == "GetGenericHostEntry":
                index = kwargs.get("NewIndex", 0)
                if index >= 2:
                    # table shrank from 3 to 2 hosts while we were iterating
                    raise FritzArrayIndexError
                return {
                    "NewIPAddress": "",  # empty IP -> skip the AVM specific-entry lookup
                    "NewMACAddress": f"AA:BB:CC:DD:EE:0{index}",
                    "NewHostName": f"host-{index}",
                    "NewActive": 1,
                }
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = shrinking_table_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=True
        )
        collector.register(device)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - device stays reachable despite the mid-scan table shrink
        reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(reachable_metrics) == 1
        fritzmock_samples = [
            s for s in reachable_metrics[0].samples if s.labels["friendly_name"] == "FritzMock"
        ]
        assert len(fritzmock_samples) == 1
        assert fritzmock_samples[0].value == 1.0

        # Check - the hosts read before the error are still exported (graceful degradation)
        host_active_metrics = [m for m in metrics if m.name == "fritz_host_active"]
        assert len(host_active_metrics) == 1
        assert len(host_active_metrics[0].samples) == 2

        # Check - benign shrink path is logged and no unreachable error is emitted
        assert any(
            "Host table shrank during scan of device serial" in record.message
            for record in caplog.records
            if record.levelno == logging.DEBUG
        )
        assert not any(
            "is unreachable, skipping HostInfo metrics for this collection cycle"
            in record.message
            for record in caplog.records
        )


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestHomeAutomationCapability:
    """Tests for HomeAutomation capability edge cases."""

    def _make_device_xml(
        self,
        ain="123456789012",
        include_powermeter=True,
        include_temperature=True,
        include_switch=True,
        include_hkr=True,
        battery: str | None = "100",
        batterylow: str | None = "0",
    ) -> str:
        parts = [
            f'<device identifier="{ain}" id="123" functionbitmask="1" '
            'fwversion="1.2" manufacturer="AVM" productname="MockDevice">',
            "<present>1</present>",
            "<name>MockDeviceName</name>",
        ]
        if battery is not None:
            parts.append(f"<battery>{battery}</battery>")
        if batterylow is not None:
            parts.append(f"<batterylow>{batterylow}</batterylow>")
        if include_powermeter:
            parts.append("<powermeter><power>1234</power><energy>12345</energy></powermeter>")
        if include_temperature:
            parts.append("<temperature><celsius>234</celsius><offset>0</offset></temperature>")
        if include_switch:
            parts.append("<switch><state>1</state><mode>manuell</mode><lock>0</lock></switch>")
        if include_hkr:
            parts.append(
                "<hkr><tist>245</tist><tsoll>234</tsoll><absenk>234</absenk>"
                "<komfort>234</komfort></hkr>"
            )
        parts.append("</device>")
        return "".join(parts)

    def _make_devicelist_xml(self, *device_xml: str) -> str:
        return f'<devicelist version="1">{"".join(device_xml)}</devicelist>'

    def _setup_ha_device(self, mock_fc, devicelist_xml: str) -> tuple:
        fc = mock_fc.return_value

        fc.call_action.side_effect = call_action_mock
        fc.call_http.side_effect = lambda action, ain=None, **kw: {
            "content": devicelist_xml,
            "content-type": "text/xml",
            "encoding": "utf-8",
        }
        fc.services = create_fc_services(fc_services_capabilities["HomeAutomation"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        collector.register(device)
        return collector, device, fc

    def test_homeautomation_with_disabled_multimeter(self, mock_fritzconnection: MagicMock):
        # Prepare
        xml = self._make_devicelist_xml(self._make_device_xml(include_powermeter=False))
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, xml)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - multimeter metrics should have no samples
        power_metrics = [m for m in metrics if m.name == "fritz_ha_multimeter_power_W"]
        assert len(power_metrics) == 1
        assert len(power_metrics[0].samples) == 0

    def test_homeautomation_with_disabled_temperature(self, mock_fritzconnection: MagicMock):
        # Prepare
        xml = self._make_devicelist_xml(self._make_device_xml(include_temperature=False))
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, xml)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - temperature metrics should have no samples
        temp_metrics = [m for m in metrics if m.name == "fritz_ha_temperature_C"]
        assert len(temp_metrics) == 1
        assert len(temp_metrics[0].samples) == 0

    def test_homeautomation_with_disabled_switch(self, mock_fritzconnection: MagicMock):
        # Prepare
        xml = self._make_devicelist_xml(self._make_device_xml(include_switch=False))
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, xml)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - switch metrics should have no samples
        switch_metrics = [m for m in metrics if m.name == "fritz_ha_switch_state"]
        assert len(switch_metrics) == 1
        assert len(switch_metrics[0].samples) == 0

    def test_homeautomation_with_disabled_heater(self, mock_fritzconnection: MagicMock):
        # Prepare
        xml = self._make_devicelist_xml(self._make_device_xml(include_hkr=False))
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, xml)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - heater metrics should have no samples
        heater_metrics = [m for m in metrics if m.name == "fritz_ha_heater_temperature_C"]
        assert len(heater_metrics) == 1
        assert len(heater_metrics[0].samples) == 0

        # No hkr block means no need to fetch valve state via TR-064
        valve_metrics = [m for m in metrics if m.name == "fritz_ha_heater_valve_set_state"]
        assert len(valve_metrics) == 1
        assert len(valve_metrics[0].samples) == 0

    def test_homeautomation_heater_includes_valve_state(self, mock_fritzconnection: MagicMock):
        # Prepare - a device with an hkr block should also get its valve
        # state via a supplemental TR-064 GetSpecificDeviceInfos call.
        xml = self._make_devicelist_xml(self._make_device_xml())
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, xml)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check
        heater_temp = [m for m in metrics if m.name == "fritz_ha_heater_temperature_C"]
        assert heater_temp[0].samples[0].value == 245 / 2.0

        valve_set = [m for m in metrics if m.name == "fritz_ha_heater_valve_set_state"]
        assert len(valve_set[0].samples) == 1
        assert valve_set[0].samples[0].value == 1  # OPEN

    def test_homeautomation_valve_state_lookup_failure_is_skipped(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare - GetSpecificDeviceInfos can fail for a device (e.g. it
        # disappeared between the two calls); the heater temperatures from
        # the bulk HTTP call should still be reported.
        caplog.set_level(logging.DEBUG)
        xml = self._make_devicelist_xml(self._make_device_xml())
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, xml)

        def failing_action(service, action, **kwargs):
            if service == "X_AVM-DE_Homeauto1" and action == "GetSpecificDeviceInfos":
                raise FritzArgumentError("unknown ain")
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = failing_action

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check
        heater_temp = [m for m in metrics if m.name == "fritz_ha_heater_temperature_C"]
        assert len(heater_temp[0].samples) == 1

        valve_set = [m for m in metrics if m.name == "fritz_ha_heater_valve_set_state"]
        assert len(valve_set[0].samples) == 0

        assert any(
            "Could not fetch HKR valve state" in record.message for record in caplog.records
        )

    def test_homeautomation_multiple_devices_in_one_response(self, mock_fritzconnection: MagicMock):
        # Prepare - two devices returned from a single getdevicelistinfos call
        xml = self._make_devicelist_xml(
            self._make_device_xml(ain="111111111111", include_hkr=False),
            self._make_device_xml(ain="222222222222", include_hkr=False),
        )
        collector, device, _ = self._setup_ha_device(mock_fritzconnection, xml)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - both devices show up as separate label sets
        device_present = [m for m in metrics if m.name == "fritz_ha_device_present"]
        assert len(device_present) == 1
        assert len(device_present[0].samples) == 2
        ains = {s.labels["ain"] for s in device_present[0].samples}
        assert ains == {"111111111111", "222222222222"}

    def test_homeautomation_with_fritz_http_interface_error(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)
        xml = self._make_devicelist_xml(self._make_device_xml())
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, xml)

        # Make call_http raise FritzHttpInterfaceError
        fc.call_http.side_effect = FritzHttpInterfaceError("HTTP interface error")

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - the whole device list comes from this one call, so on
        # failure no home automation devices are reported at all.
        device_present = [m for m in metrics if m.name == "fritz_ha_device_present"]
        assert len(device_present) == 1
        assert len(device_present[0].samples) == 0

        # Warning should be logged
        assert any(
            "Got FritzHttpInterfaceError" in record.message
            for record in caplog.records
        )

    def test_homeautomation_no_content_in_http_result(self, mock_fritzconnection: MagicMock):
        # Prepare
        xml = self._make_devicelist_xml(self._make_device_xml())
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, xml)

        # Make call_http return a response without 'content'
        fc.call_http.side_effect = lambda action, ain=None, **kw: {"status": "ok"}

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - no devices at all since there is no content to parse
        device_present = [m for m in metrics if m.name == "fritz_ha_device_present"]
        assert len(device_present) == 1
        assert len(device_present[0].samples) == 0

    def test_homeautomation_no_battery_low_in_http_data(self, mock_fritzconnection: MagicMock):
        # Prepare - device XML without batterylow element
        xml = self._make_devicelist_xml(
            self._make_device_xml(battery="75", batterylow=None, include_hkr=False)
        )
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, xml)

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
        # Prepare - device XML without battery element (but has content)
        xml = self._make_devicelist_xml(
            self._make_device_xml(battery=None, batterylow=None, include_hkr=False)
        )
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, xml)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - neither battery_level nor battery_low should have samples
        battery_level = [m for m in metrics if m.name == "fritz_ha_battery_level_percent"]
        assert len(battery_level) == 1
        assert len(battery_level[0].samples) == 0

        battery_low = [m for m in metrics if m.name == "fritz_ha_battery_low"]
        assert len(battery_low) == 1
        assert len(battery_low[0].samples) == 0


def _sample_map(metric: Metric) -> dict[tuple[tuple[str, str], ...], float]:
    return {
        tuple(sorted(sample.labels.items())): sample.value
        for sample in metric.samples
    }


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanFiberCapabilities:
    """Tests for fibre WAN capability metrics."""

    def _collect_fiber_metrics(self, mock_fritzconnection: MagicMock) -> list[Metric]:
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fiber_services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
            "X_AVM-DE_WANFiber1": ["GetInfo", "GetInfoGPON", "GetStatistics"],
        }
        fc.services = create_fc_services(fiber_services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzFiber",
            host_info=False,
        )
        collector.register(device)
        return list(collector.collect())

    def test_fiber_optical_and_sfp_metrics(self, mock_fritzconnection: MagicMock):
        metrics = self._collect_fiber_metrics(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        assert "fritz_fiber_optical_signal_level_dBm" in by_name
        assert by_name["fritz_fiber_optical_signal_level_dBm"].samples[0].value == pytest.approx(
            -15.6
        )

        thresholds = _sample_map(by_name["fritz_fiber_optical_threshold_dBm"])
        assert thresholds[
            (("bound", "lower"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == pytest.approx(-28.0)
        assert thresholds[
            (("bound", "upper"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == pytest.approx(-1.2)

        info = by_name["fritz_fiber_info"].samples[0]
        assert info.value == 1
        assert info.labels["fiber_mode"] == "GPON"
        assert info.labels["sfp_vendor"] == "AVM"
        assert info.labels["sfp_serial_number"] == "SFP123"
        assert by_name["fritz_fiber_tx_wavelength_nm"].samples[0].value == 1310

    def test_fiber_gpon_metrics_include_serial(self, mock_fritzconnection: MagicMock):
        metrics = self._collect_fiber_metrics(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        gpon_info = by_name["fritz_fiber_gpon_info"].samples[0]
        assert gpon_info.value == 1
        assert gpon_info.labels["gpon_serial"] == "AVMG7B765C11"
        assert gpon_info.labels["pon_id"] == "pon-1"
        assert gpon_info.labels["uni_type"] == "Unknown"
        assert by_name["fritz_fiber_gpon_onu_id"].samples[0].value == 6
        assert by_name["fritz_fiber_gpon_gem_port_count"].samples[0].value == 3

    def test_fiber_statistics_metrics(self, mock_fritzconnection: MagicMock):
        metrics = self._collect_fiber_metrics(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        bytes_map = _sample_map(by_name["fritz_fiber_data_bytes"])
        assert bytes_map[
            (("direction", "tx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 97008448189
        assert bytes_map[
            (("direction", "rx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 123456789

        rates = _sample_map(by_name["fritz_fiber_connection_rate"])
        assert rates[
            (("direction", "rx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 2500000
        assert rates[
            (("direction", "tx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 1250000

    def test_layer1_64bit_max_bitrate_from_addon_infos(self, mock_fritzconnection: MagicMock):
        metrics = self._collect_fiber_metrics(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        layer1 = _sample_map(by_name["fritz_wan_layer1_max_bitrate_bps"])
        assert layer1[
            (("direction", "rx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 2500000000
        assert layer1[
            (("direction", "tx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 1250000000


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanCommonInterfaceByteRateCableWan:
    """Regression tests for WAN types (e.g. cable) that don't populate the
    AVM 64-bit Layer1 max bitrate fields. FRITZ!OS reports them as an empty
    string rather than omitting them or returning None in this case, which
    previously crashed metric exposition (the client library does
    float("") to format the sample value)."""

    def test_empty_string_64bit_fields_are_skipped(self, mock_fritzconnection: MagicMock):
        # Prepare - same as GetAddonInfos in fc_services_mock.py, except the
        # two 64-bit fields come back as empty strings, as observed on a
        # cable WAN connection.
        fc = mock_fritzconnection.return_value

        def call_action_cable_addon_infos(service, action, **kwargs):
            if (service, action) == ("WANCommonIFC1", "GetAddonInfos"):
                return {
                    "NewByteReceiveRate": 12345,
                    "NewByteSendRate": 23456,
                    "NewX_AVM_DE_Layer1DownstreamMaxBitRate64": "",
                    "NewX_AVM_DE_Layer1UpstreamMaxBitRate64": "",
                }
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = call_action_cable_addon_infos
        fc.services = create_fc_services(
            {
                **fc_services_capabilities["DeviceInfo"],
                **fc_services_capabilities["WanCommonInterfaceByteRate"],
            }
        )

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        collector.register(device)

        # Act - this used to raise ValueError: could not convert string to
        # float: '' while formatting the layer1max sample.
        metrics: list[Metric] = list(collector.collect())

        # Check - the field is silently skipped rather than emitted empty...
        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_wan_layer1_max_bitrate_bps"].samples == []

        # ...and the sibling byte-rate metric from the same call is unaffected.
        byterate = _sample_map(by_name["fritz_wan_datarate_bytes"])
        assert byterate[
            (("direction", "rx"), ("friendly_name", "FritzCable"), ("serial", "1234567890"))
        ] == 12345
        assert byterate[
            (("direction", "tx"), ("friendly_name", "FritzCable"), ("serial", "1234567890"))
        ] == 23456
