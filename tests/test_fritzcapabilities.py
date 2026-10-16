import logging
from unittest.mock import MagicMock, patch

import pytest
from fritzconnection.core.exceptions import (
    FritzActionError,
    FritzArgumentError,
    FritzArrayIndexError,
    FritzInternalError,
    FritzLookUpError,
    FritzServiceError,
)
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import Metric

from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice
from fritzexporter.fritzcapabilities import (
    FritzCapabilities,
    ProblemLevel,
    ProblemState,
    report_problem,
)
from fritzexporter.fritz_webui import FritzWebUiClient, FritzWebUiError

from .fc_services_mock import (
    call_action_mock,
    call_http_mock,
    create_fc_services,
    fc_services_capabilities,
    fc_services_devices,
)
from .collect import collect_device, fake_device, sample_map, services


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
        assert num_caps == 27  # All known capabilities

    def test_empty_capabilities_is_true_when_all_absent(self, mock_fritzconnection: MagicMock):
        # Prepare - use an empty service set so no capability is present
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services({})

        caps = FritzCapabilities()  # No device, so nothing is checked

        # Check - empty() should return True since no capability is present
        assert caps.empty() is True

    @pytest.mark.parametrize(
        ("error", "level", "message"),
        [
            # The box advertises the action but does not implement it (UPnP 401).
            (
                FritzActionError,
                logging.INFO,
                "device FritzMock (somehost) does not support service Hosts1, action GetHostNumberOfEntries",
            ),
            (
                FritzServiceError,
                logging.INFO,
                "device FritzMock (somehost) does not support service Hosts1, action GetHostNumberOfEntries",
            ),
            (
                FritzInternalError,
                logging.WARNING,
                "disabling metrics of FritzMock (somehost) at service Hosts1, action GetHostNumberOfEntries",
            ),
        ],
    )
    def test_capability_check_disables_when_call_action_raises(
        self, mock_fritzconnection: MagicMock, caplog, error, level, message
    ):
        # Prepare - service is present in services dict but call_action raises
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value

        def error_on_hosts(service, action, **kwargs):
            if service == "Hosts1" and action == "GetHostNumberOfEntries":
                raise error("Mock error for HostNumberOfEntries")
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = error_on_hosts
        # Include Hosts1/GetHostNumberOfEntries so the service IS present in services dict
        services = dict(fc_services_devices["FritzBox 7590"])
        fc.services = create_fc_services(services)

        # Act
        fd = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)

        # Check - HostNumberOfEntries capability should be disabled
        assert fd.capabilities["HostNumberOfEntries"].present is False

        logged = [r for r in caplog.records if "GetHostNumberOfEntries" in r.message]
        assert [r.levelno for r in logged] == [level]
        assert message in logged[0].message


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

        # Check - no update: 0, and no version info
        update_metrics = [m for m in metrics if m.name == "fritz_update_available"]
        assert len(update_metrics) == 1
        assert update_metrics[0].samples[0].value == 0
        assert "newsoftwareversion" not in update_metrics[0].samples[0].labels
        [info] = [m for m in metrics if m.name == "fritz_update_info"]
        assert info.samples == []


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
        assert host_active_metrics[0].samples[0].value == 0.0
        # With empty IP, interface/port/model should be "n/a"
        [info] = [m for m in metrics if m.name == "fritz_host_info"]
        sample = info.samples[0]
        assert sample.labels["interface"] == "n/a"
        assert sample.labels["port"] == "n/a"
        assert sample.labels["model"] == "n/a"

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
            "Hosts1.GetGenericHostEntry on FritzMock (somehost): the table shrank to 2 entries" in record.message
            for record in caplog.records
            if record.levelno == logging.DEBUG
        )
        assert not any(
            "is unreachable, skipping HostInfo metrics for this collection cycle"
            in record.message
            for record in caplog.records
        )

    def test_host_leaving_mid_scan_keeps_device_reachable(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # A host leaving mid-scan makes X_AVM-DE_GetSpecificHostEntryByIP raise
        # FritzLookUpError (UPnP errorCode 714, NoSuchEntryInArray): the host's IP
        # disappeared from the host table between GetGenericHostEntry and the
        # per-IP lookup. This is a benign race on the DHCP server's host table, not
        # a device outage. The device must stay reachable; skip the extended info
        # for the vanished host (fall back to "n/a") and keep the other hosts.
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value

        def host_leaving_mock(service, action, **kwargs):
            if service == "Hosts1" and action == "GetHostNumberOfEntries":
                return {"NewHostNumberOfEntries": 2}
            if service == "Hosts1" and action == "GetGenericHostEntry":
                index = kwargs.get("NewIndex", 0)
                return {
                    "NewIPAddress": f"192.168.178.{10 + index}",
                    "NewMACAddress": f"AA:BB:CC:DD:EE:0{index}",
                    "NewHostName": f"host-{index}",
                    "NewActive": 1,
                }
            if service == "Hosts1" and action == "X_AVM-DE_GetSpecificHostEntryByIP":
                ip = kwargs.get("NewIPAddress")
                if ip == "192.168.178.10":
                    # host 0 left the table between the generic read and this lookup
                    raise FritzLookUpError
                return {
                    "NewInterfaceType": "eth",
                    "NewX_AVM-DE_Port": "LAN1",
                    "NewX_AVM-DE_Model": "Mockgear",
                    "NewX_AVM-DE_Speed": 1000,
                }
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = host_leaving_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=True
        )
        device.webui_client = None
        collector.register(device)

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - device stays reachable despite the mid-scan host leaving
        reachable_metrics = [m for m in metrics if m.name == "fritz_device_reachable"]
        assert len(reachable_metrics) == 1
        fritzmock_samples = [
            s for s in reachable_metrics[0].samples if s.labels["friendly_name"] == "FritzMock"
        ]
        assert len(fritzmock_samples) == 1
        assert fritzmock_samples[0].value == 1.0

        # Check - both hosts are still exported; the vanished host falls back to "n/a"
        host_active_metrics = [m for m in metrics if m.name == "fritz_host_active"]
        assert len(host_active_metrics) == 1
        assert len(host_active_metrics[0].samples) == 2
        [info] = [
            m for m in metrics if m.name == "fritz_host_info"
        ]
        [vanished] = [s for s in info.samples if s.labels["ip_address"] == "192.168.178.10"]
        assert vanished.labels["interface"] == "n/a"
        assert vanished.labels["port"] == "n/a"
        assert vanished.labels["model"] == "n/a"
        [active] = [
            s for s in host_active_metrics[0].samples
            if s.labels["mac_address"] == vanished.labels["mac_address"]
        ]
        assert active.value == 1.0

        # Check - benign lookup-miss path is logged and no unreachable error is emitted
        assert any(
            "left the host table during scan of FritzMock (somehost)" in record.message
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
        fc.services = create_fc_services(fc_services_capabilities["HomeAutomation"])

        collector = FritzCollector()
        device = FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock", host_info=False)
        # AHA data comes from the web UI client, not from fritzconnection.
        device.webui_client = MagicMock(spec=FritzWebUiClient)
        device.webui_client.fetch_aha.return_value = devicelist_xml
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

    def test_homeautomation_valve_state_lookup_failure_skips_only_the_valve_state(
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

        # Reported as a partial read: the valve state of that device is skipped.
        assert (
            "valve state of smart home device 0 skipped: FritzArgumentError" in caplog.text
        )
        problems = [m for m in metrics if m.name == "fritz_read_problem"][0].samples
        assert ("HomeAutomation", "partial") in {
            (s.labels["capability"], s.labels["level"]) for s in problems
        }

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

    def test_homeautomation_with_webui_error(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)
        xml = self._make_devicelist_xml(self._make_device_xml())
        collector, device, fc = self._setup_ha_device(mock_fritzconnection, xml)

        device.webui_client.fetch_aha.side_effect = FritzWebUiError("AHA request failed")

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - the whole device list comes from this one call, so on
        # failure no home automation devices are reported at all.
        device_present = [m for m in metrics if m.name == "fritz_ha_device_present"]
        assert len(device_present) == 1
        assert len(device_present[0].samples) == 0

        # Warning should be logged
        assert any(
            "Could not read HomeAutomation data from FritzMock (somehost), skipping it: AHA request failed"
            in record.message
            for record in caplog.records
        )

    def test_homeautomation_empty_devicelist(self, mock_fritzconnection: MagicMock):
        # Prepare - a box without any smart home devices
        collector, device, fc = self._setup_ha_device(
            mock_fritzconnection, self._make_devicelist_xml()
        )

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - no devices at all
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


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanFiberCapabilities:
    """Tests for fibre WAN capability metrics."""

    def _collect_fiber_metrics(self, mock_fritzconnection: MagicMock) -> dict[str, Metric]:
        return collect_device(
            mock_fritzconnection,
            services(
                "DeviceInfo",
                "WanCommonInterfaceByteRate",
                **{"X_AVM-DE_WANFiber1": ["GetInfo", "GetInfoGPON", "GetStatistics"]},
            ),
            name="FritzFiber",
        )

    def test_fiber_optical_and_sfp_metrics(self, mock_fritzconnection: MagicMock):
        by_name = self._collect_fiber_metrics(mock_fritzconnection)

        assert "fritz_fiber_optical_signal_level_dBm" in by_name
        assert by_name["fritz_fiber_optical_signal_level_dBm"].samples[0].value == pytest.approx(
            -15.6
        )

        thresholds = sample_map(by_name["fritz_fiber_optical_threshold_dBm"])
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
        by_name = self._collect_fiber_metrics(mock_fritzconnection)

        gpon_info = by_name["fritz_fiber_gpon_info"].samples[0]
        assert gpon_info.value == 1
        assert gpon_info.labels["gpon_serial"] == "AVMG7B765C11"
        assert gpon_info.labels["pon_id"] == "pon-1"
        assert gpon_info.labels["uni_type"] == "Unknown"
        assert by_name["fritz_fiber_gpon_onu_id"].samples[0].value == 6
        assert by_name["fritz_fiber_gpon_gem_port_count"].samples[0].value == 3

    def test_fiber_statistics_metrics(self, mock_fritzconnection: MagicMock):
        by_name = self._collect_fiber_metrics(mock_fritzconnection)

        bytes_map = sample_map(by_name["fritz_fiber_data_bytes"])
        assert bytes_map[
            (("direction", "tx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 97008448189
        assert bytes_map[
            (("direction", "rx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 123456789

        rates = sample_map(by_name["fritz_fiber_connection_rate"])
        assert rates[
            (("direction", "rx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 2500000
        assert rates[
            (("direction", "tx"), ("friendly_name", "FritzFiber"), ("serial", "1234567890"))
        ] == 1250000

    def test_layer1_64bit_max_bitrate_from_addon_infos(self, mock_fritzconnection: MagicMock):
        by_name = self._collect_fiber_metrics(mock_fritzconnection)

        layer1 = sample_map(by_name["fritz_wan_layer1_max_bitrate_bps"])
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

        # Act
        metrics: list[Metric] = list(collector.collect())

        # Check - the field is silently skipped rather than emitted empty...
        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_wan_layer1_max_bitrate_bps"].samples == []

        # ...and the sibling byte-rate metric from the same call is unaffected.
        byterate = sample_map(by_name["fritz_wan_datarate_bytes"])
        assert byterate[
            (("direction", "rx"), ("friendly_name", "FritzCable"), ("serial", "1234567890"))
        ] == 12345
        assert byterate[
            (("direction", "tx"), ("friendly_name", "FritzCable"), ("serial", "1234567890"))
        ] == 23456

        # ...and, crucially, rendering the actual exposition text does not
        # raise. add_metric() stores a value as-is with no conversion, so
        # asserting on samples alone (above) does NOT prove the ValueError
        # this regression is named after is actually fixed - only rendering
        # does, since that's where floatToGoString() calls float(value).
        registry = CollectorRegistry()
        registry.register(collector)
        generate_latest(registry)


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestManagementServer:
    """Tests for the TR-069 management server metrics."""

    def _collect(self, mock_fritzconnection: MagicMock, overrides=None):
        return collect_device(
            mock_fritzconnection, services("DeviceInfo", "ManagementServerInfo"), overrides=overrides
        )

    def test_management_server_metrics(self, mock_fritzconnection: MagicMock):
        by_name = self._collect(mock_fritzconnection)

        acs = by_name["fritz_tr069_acs_info"].samples
        assert len(acs) == 1
        assert acs[0].labels["acs_host"] == "acs.example.invalid"
        assert by_name["fritz_tr069_periodic_inform_enabled"].samples[0].value == 1
        assert by_name["fritz_tr069_periodic_inform_interval_seconds"].samples[0].value == 21600
        assert by_name["fritz_tr069_upgrades_managed"].samples[0].value == 1

    def test_no_credentials_or_identifiers_are_exported(self, mock_fritzconnection: MagicMock):
        by_name = self._collect(mock_fritzconnection)

        exposed = " ".join(
            f"{sample.name} {sample.labels} {sample.value}"
            for metric in by_name.values()
            for sample in metric.samples
        )
        for secret in (
            "secret",
            "token",
            "acs-user",
            "cr-user",
            "198.51.100.7",
            "8089",
            "/cwmp",
        ):
            assert secret not in exposed

    def test_empty_acs_url_has_no_info_sample(self, mock_fritzconnection: MagicMock):
        by_name = self._collect(
            mock_fritzconnection,
            {
                ("ManagementServer1", "GetInfo"): lambda **_: {
                    "NewURL": "",
                    "NewPeriodicInformEnable": False,
                    "NewPeriodicInformInterval": 0,
                    "NewUpgradesManaged": False,
                }
            },
        )

        assert by_name["fritz_tr069_acs_info"].samples == []
        assert by_name["fritz_tr069_periodic_inform_enabled"].samples[0].value == 0


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestUspControllers:
    """Tests for the USP controller metrics."""

    def _collect(self, mock_fritzconnection: MagicMock, overrides=None):
        return collect_device(
            mock_fritzconnection, services("DeviceInfo", "UspControllers"), overrides=overrides
        )

    @pytest.mark.parametrize(
        ("error", "present"), [(FritzArrayIndexError, True), (FritzActionError, False)]
    )
    def test_controller_lookup_is_probed(self, mock_fritzconnection: MagicMock, error, present):
        # No controller at index 0 still shows the action works; an error
        # reply means the device only advertises it.
        def by_index(**_):
            raise error

        device = fake_device(
            mock_fritzconnection,
            services("DeviceInfo", "UspControllers"),
            overrides={("X_AVM-DE_USPController1", "GetUSPControllerByIndex"): by_index},
        )
        assert device.capabilities["UspControllers"].present is present

    def test_usp_controller_flags(self, mock_fritzconnection: MagicMock):
        by_name = self._collect(mock_fritzconnection)

        controllers = {
            sample.labels["index"]: sample.value
            for sample in by_name["fritz_usp_controller_enabled"].samples
        }
        assert controllers == {"0": 1, "1": 1}
        assert by_name["fritz_usp_myfritz_enabled"].samples[0].value == 1

        exposed = " ".join(
            f"{sample.labels}" for metric in by_name.values() for sample in metric.samples
        )
        assert "endpoint-secret-id" not in exposed
        assert "usp.example.invalid" not in exposed

    def test_usp_without_controllers_has_no_controller_sample(
        self, mock_fritzconnection: MagicMock
    ):
        by_name = self._collect(
            mock_fritzconnection,
            {
                ("X_AVM-DE_USPController1", "GetUSPControllerNumberOfEntries"): lambda **_: {
                    "NewUSPControllerNumberOfEntries": 0
                }
            },
        )

        assert by_name["fritz_usp_controller_enabled"].samples == []
        assert by_name["fritz_usp_myfritz_enabled"].samples[0].value == 1


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestTelephonyNumbers:
    """Tests for the telephony number count metric."""

    def _collect_numbers_metric(self, mock_fritzconnection: MagicMock, count: int | None):
        reply = {"NewNumberOfNumbers": count}
        overrides = {("X_VoIP1", "X_AVM-DE_GetNumberOfNumbers"): reply} if count is not None else {}
        return collect_device(
            mock_fritzconnection, services("DeviceInfo", "TelephonyNumbers"), overrides=overrides
        )

    def test_number_count_metric(self, mock_fritzconnection: MagicMock):
        by_name = self._collect_numbers_metric(mock_fritzconnection, None)

        samples = by_name["fritz_telephony_numbers"].samples
        assert len(samples) == 1
        assert samples[0].value == 2
        assert samples[0].labels == {"serial": "1234567890", "friendly_name": "FritzMock"}

    def test_no_numbers_configured_reports_zero(self, mock_fritzconnection: MagicMock):
        by_name = self._collect_numbers_metric(mock_fritzconnection, 0)

        samples = by_name["fritz_telephony_numbers"].samples
        assert len(samples) == 1
        assert samples[0].value == 0


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanIPv6Prefix:
    """Tests for the delegated IPv6 prefix info metric."""

    def _collect_prefix_metric(self, mock_fritzconnection: MagicMock, prefix_result: dict | None):
        overrides = {("WANIPConn1", "X_AVM_DE_GetIPv6Prefix"): lambda **_: prefix_result} if prefix_result else {}
        return collect_device(
            mock_fritzconnection, services("DeviceInfo", "WanIPv6Prefix"), overrides=overrides
        )

    def test_prefix_info_metric(self, mock_fritzconnection: MagicMock):
        by_name = self._collect_prefix_metric(mock_fritzconnection, None)

        samples = by_name["fritz_wan_ipv6_prefix_info"].samples
        assert len(samples) == 1
        assert samples[0].value == 1
        assert samples[0].labels["prefix"] == "2001:db8:1234:5600::"
        assert samples[0].labels["prefix_length"] == "56"

    def test_empty_prefix_is_skipped(self, mock_fritzconnection: MagicMock):
        by_name = self._collect_prefix_metric(
            mock_fritzconnection, {"NewIPv6Prefix": "", "NewPrefixLength": 0}
        )

        assert by_name["fritz_wan_ipv6_prefix_info"].samples == []


def _argument(kwargs: dict, name: str):
    """An action argument, given as keyword (reads) or in ``arguments`` (probes)."""
    return kwargs.get(name, kwargs.get("arguments", {}).get(name))


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestProbing:
    @pytest.mark.parametrize("hosts", [0, 1])
    def test_host_info_works_with_a_small_host_table(self, mock_fritzconnection: MagicMock, hosts):
        # The probe used index 1, which a table of 0 or 1 hosts does not have:
        # the device never came up.
        def host_table(service, action, **kwargs):
            if (service, action) == ("Hosts1", "GetHostNumberOfEntries"):
                return {"NewHostNumberOfEntries": hosts}
            if (service, action) == ("Hosts1", "GetGenericHostEntry"):
                if _argument(kwargs, "NewIndex") >= hosts:
                    raise FritzArrayIndexError
                return {"NewIPAddress": "", "NewMACAddress": "AA:BB:CC:DD:EE:00", "NewHostName": "h", "NewActive": 1}
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = host_table
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        device = FritzDevice(FritzCredentials("somehost", "u", "p"), "FritzMock", host_info=True)
        collector = FritzCollector()
        collector.register(device)
        by_name = {m.name: m for m in collector.collect()}

        assert device.capabilities["HostInfo"].present
        assert len(by_name["fritz_host_active"].samples) == hosts

    def test_device_not_answering_a_probe_fails_the_setup(self, mock_fritzconnection: MagicMock):
        # A timeout must not disable a capability until restart: the device is
        # set up again later instead.
        from fritzexporter.tr064_remote import FritzUnreachableError

        def timing_out(service, action, **kwargs):
            if (service, action) == ("LANEthernetInterfaceConfig1", "GetStatistics"):
                raise FritzUnreachableError("Fritz device did not answer (ReadTimeout)")
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = timing_out
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        creds = FritzCredentials("somehost", "u", "p")
        with pytest.raises(FritzUnreachableError):
            FritzDevice(creds, "FritzMock")

    def test_error_reply_to_an_item_skips_only_that_item(self, mock_fritzconnection: MagicMock):
        def one_bad_host(service, action, **kwargs):
            if (service, action) == ("Hosts1", "GetHostNumberOfEntries"):
                return {"NewHostNumberOfEntries": 2}
            if (service, action) == ("Hosts1", "GetGenericHostEntry"):
                index = _argument(kwargs, "NewIndex")
                return {"NewIPAddress": f"192.0.2.{index + 1}", "NewMACAddress": f"AA:BB:CC:DD:EE:0{index}", "NewHostName": "h", "NewActive": 1}
            if (service, action) == ("Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP"):
                if _argument(kwargs, "NewIPAddress") == "192.0.2.1":
                    raise FritzInternalError("820")
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = one_bad_host
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        device = FritzDevice(FritzCredentials("somehost", "u", "p"), "FritzMock", host_info=True)
        collector = FritzCollector()
        collector.register(device)

        by_name = {m.name: m for m in collector.collect()}

        hosts = [s.labels["mac_address"] for s in by_name["fritz_host_active"].samples]
        assert hosts == ["AA:BB:CC:DD:EE:01"]
        problems = {(s.labels["capability"], s.labels["level"]) for s in by_name["fritz_read_problem"].samples}
        assert ("HostInfo", "partial") in problems
        assert by_name["fritz_device_reachable"].samples[0].value == 1.0


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestHostInfoKeyedByMac:
    @staticmethod
    def _collect(mock_fritzconnection: MagicMock, hosts: list[dict]) -> tuple[dict, list]:
        def host_table(service, action, **kwargs):
            if (service, action) == ("Hosts1", "GetHostNumberOfEntries"):
                return {"NewHostNumberOfEntries": len(hosts)}
            if (service, action) == ("Hosts1", "GetGenericHostEntry"):
                index = _argument(kwargs, "NewIndex")
                if index >= len(hosts):
                    raise FritzArrayIndexError
                return {"NewIPAddress": "", "NewHostName": "h", "NewActive": 1, **hosts[index]}
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = host_table
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        collector = FritzCollector()
        collector.register(FritzDevice(FritzCredentials("somehost", "u", "p"), "FritzMock", host_info=True))
        by_name = {m.name: m for m in collector.collect()}
        problems = [
            (s.labels["capability"], s.labels["level"]) for s in by_name["fritz_read_problem"].samples
        ]
        return by_name, problems

    def test_active_and_speed_carry_only_the_mac(self, mock_fritzconnection: MagicMock):
        by_name, _ = self._collect(mock_fritzconnection, [{"NewMACAddress": "AA:BB:CC:DD:EE:01"}])

        for name in ("fritz_host_active", "fritz_host_speed"):
            [sample] = by_name[name].samples
            assert set(sample.labels) == {"serial", "friendly_name", "mac_address"}
        [info] = by_name["fritz_host_info"].samples
        assert info.labels["hostname"] == "h"
        assert info.value == 1

    def test_host_without_mac_is_left_out_quietly(self, mock_fritzconnection: MagicMock):
        by_name, problems = self._collect(
            mock_fritzconnection, [{"NewMACAddress": ""}, {"NewMACAddress": "AA:BB:CC:DD:EE:01"}]
        )

        assert [s.labels["mac_address"] for s in by_name["fritz_host_active"].samples] == [
            "AA:BB:CC:DD:EE:01"
        ]
        assert ("HostInfo", "partial") not in problems

    def test_mac_listed_twice_keeps_the_first(self, mock_fritzconnection: MagicMock):
        by_name, problems = self._collect(
            mock_fritzconnection,
            [
                {"NewMACAddress": "AA:BB:CC:DD:EE:01", "NewHostName": "first"},
                {"NewMACAddress": "AA:BB:CC:DD:EE:01", "NewHostName": "second"},
            ],
        )

        assert [s.labels["hostname"] for s in by_name["fritz_host_info"].samples] == ["first"]
        assert len(by_name["fritz_host_active"].samples) == 1
        assert ("HostInfo", "partial") in problems


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestExitReviewFixes:
    """Values and items the 4.0 exit review found handled wrongly."""

    def test_thermostat_off_and_on_are_no_temperatures(self, mock_fritzconnection: MagicMock):
        helper = TestHomeAutomationCapability()
        xml = helper._make_devicelist_xml(helper._make_device_xml()).replace(
            "<tsoll>234</tsoll><absenk>234</absenk><komfort>234</komfort>",
            "<tsoll>253</tsoll><absenk>254</absenk><komfort>42</komfort>",
        )
        collector, _, _ = helper._setup_ha_device(mock_fritzconnection, xml)

        by_name = {m.name: m for m in collector.collect()}

        assert by_name["fritz_ha_heater_set_temperature_C"].samples == []
        assert by_name["fritz_ha_heater_reduced_temperature_C"].samples == []
        assert [s.value for s in by_name["fritz_ha_heater_comfort_temperature_C"].samples] == [21.0]

    def test_error_reply_for_one_host_skips_only_that_host(self, mock_fritzconnection: MagicMock):
        def host_table(service, action, **kwargs):
            if (service, action) == ("Hosts1", "GetHostNumberOfEntries"):
                return {"NewHostNumberOfEntries": 2}
            if (service, action) == ("Hosts1", "GetGenericHostEntry"):
                index = _argument(kwargs, "NewIndex")
                if index == 0 and "NewIndex" in kwargs:
                    raise FritzInternalError("820")
                return {"NewIPAddress": "", "NewMACAddress": f"AA:BB:CC:DD:EE:0{index}", "NewHostName": "h", "NewActive": 1}
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = host_table
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        collector = FritzCollector()
        collector.register(FritzDevice(FritzCredentials("somehost", "u", "p"), "FritzMock", host_info=True))

        by_name = {m.name: m for m in collector.collect()}

        assert [s.labels["mac_address"] for s in by_name["fritz_host_active"].samples] == ["AA:BB:CC:DD:EE:01"]
        problems = {(s.labels["capability"], s.labels["level"]) for s in by_name["fritz_read_problem"].samples}
        assert ("HostInfo", "partial") in problems



def test_a_lasting_bug_adds_no_log_lines_of_its_own():
    # The caller logs a bug with its traceback on every scrape; report_problem
    # only keeps count and must not add an empty line.
    state = ProblemState()
    with patch("fritzexporter.fritzcapabilities.logger") as log:
        for _ in range(3):
            report_problem(state, "HostInfo", "host", level=ProblemLevel.ERROR)
    assert log.mock_calls == []
    assert state.problem is not None
    assert state.problem.scrapes == 3


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanDataBytes:
    """fritz_wan_data_bytes_total prefers the 64-bit totals: the others wrap at 4 GiB."""

    ADDON = ("WANCommonIFC1", "GetAddonInfos")

    def _totals(self, mock_fritzconnection, capabilities, overrides=None):
        by_name = collect_device(mock_fritzconnection, services(*capabilities), overrides=overrides)
        return {s.labels["direction"]: s.value for s in by_name["fritz_wan_data_bytes"].samples}

    def test_64_bit_totals_from_addon_infos(self, mock_fritzconnection: MagicMock):
        wide = {"NewX_AVM_DE_TotalBytesReceived64": 540473814524, "NewX_AVM_DE_TotalBytesSent64": "6000000000"}
        totals = self._totals(
            mock_fritzconnection,
            ("DeviceInfo", "WanCommonInterfaceDataBytes", "WanCommonInterfaceByteRate"),
            {self.ADDON: wide},
        )
        assert totals == {"rx": 540473814524, "tx": 6000000000}

    def test_empty_64_bit_value_falls_back_to_the_classic_total(self, mock_fritzconnection: MagicMock):
        wide = {"NewX_AVM_DE_TotalBytesReceived64": "", "NewX_AVM_DE_TotalBytesSent64": 6000000000}
        totals = self._totals(
            mock_fritzconnection,
            ("DeviceInfo", "WanCommonInterfaceDataBytes", "WanCommonInterfaceByteRate"),
            {self.ADDON: wide},
        )
        assert totals == {"rx": 1234567, "tx": 6000000000}

    def test_without_addon_infos_the_classic_totals(self, mock_fritzconnection: MagicMock):
        totals = self._totals(mock_fritzconnection, ("DeviceInfo", "WanCommonInterfaceDataBytes"))
        assert totals == {"rx": 1234567, "tx": 234567}
