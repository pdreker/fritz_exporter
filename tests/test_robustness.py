"""A failure stays at the level it affects: value, item, capability or device.

Every test runs two devices, "good" and "bad", and renders the scrape the way
Prometheus gets it, so a failure that would only show while rendering is caught.
"""

import argparse
import logging
from unittest.mock import MagicMock, patch

import pytest
from fritzconnection.core.exceptions import FritzConnectionException
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

from fritzexporter.__main__ import _register_device
from fritzexporter.config import DeviceConfig
from fritzexporter.fritzcapabilities import LanInterfaceConfigStatistics, _check_samples
from fritzexporter.fritzdevice import FritzCollector
from fritzexporter.reply import DataError

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_devices

SECRET = "192.0.2.77"
LAN_STATS = ("LANEthernetInterfaceConfig1", "GetStatistics")


def lan_stats(**fields):
    stats = {
        "NewBytesReceived": 1234,
        "NewBytesSent": 9876,
        "NewPacketsReceived": 123,
        "NewPacketsSent": 987,
    }
    stats.update(fields)
    return {k: v for k, v in stats.items() if v is not None}


def wlan1_info(**fields):
    info = call_action_mock("WLANConfiguration1", "GetInfo")
    return {**info, **fields}


class Scenario:
    """Two devices; "bad" answers ``overrides`` instead of the mock data."""

    def __init__(self, overrides: dict | None = None):
        self.overrides = {"good": {}, "bad": dict(overrides or {})}
        self.fcs = {host: self._fake_fc(host) for host in ("good", "bad")}
        self.collector = FritzCollector()
        with self._patched():
            for host in ("good", "bad"):
                _register_device(
                    DeviceConfig(hostname=host, username="user", password="pw", name=host.upper()),
                    argparse.Namespace(donate_data=None),
                    self.collector,
                )
        self.registry = CollectorRegistry()
        self.registry.register(self.collector)

    def _fake_fc(self, host: str) -> MagicMock:
        def call_action(service, action, **kwargs):
            if (service, action) in self.overrides[host]:
                return self.overrides[host][(service, action)]
            result = call_action_mock(service, action, **kwargs)
            if (service, action) == ("DeviceInfo1", "GetInfo"):
                result = {**result, "NewSerialNumber": host.upper()}
            return result

        fc = MagicMock()
        fc.call_action.side_effect = call_action
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        return fc

    def _patched(self):
        # The web UI capabilities are not under test here.
        return patch.multiple(
            "fritzexporter.fritzdevice",
            create_fritz_connection=lambda **kw: self.fcs[kw["address"]],
            FritzWebUiClient=MagicMock(return_value=None),
        )

    def scrape(self) -> str:
        with self._patched():
            for device in self.collector.devices:
                device.webui_client = None
            return generate_latest(self.registry).decode()


def samples(output: str, metric: str, serial: str) -> list[str]:
    return [
        line
        for line in output.splitlines()
        if line.startswith(metric + "{") and f'serial="{serial}"' in line
    ]


def reachable(output: str, name: str, serial: str) -> float:
    prefix = f'fritz_device_reachable{{friendly_name="{name}",serial="{serial}"}} '
    return float(next(line for line in output.splitlines() if line.startswith(prefix))[len(prefix) :])


class TestValueLevel:
    def test_missing_value_skips_only_its_sample(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario({LAN_STATS: lan_stats(NewBytesSent=None)})

        output = scenario.scrape()

        bad = samples(output, "fritz_lan_data_bytes_total", "BAD")
        assert len(bad) == 1
        assert 'direction="rx"' in bad[0]
        assert len(samples(output, "fritz_lan_packet_count_total", "BAD")) == 2
        assert len(samples(output, "fritz_lan_data_bytes_total", "GOOD")) == 2
        assert (
            "Skipped part of the LanInterfaceConfigStatistics data from bad: "
            "LANEthernetInterfaceConfig1.GetStatistics NewBytesSent: missing"
        ) in caplog.text

    def test_wrong_type_is_reported_without_the_value(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario({LAN_STATS: lan_stats(NewPacketsSent=SECRET)})

        output = scenario.scrape()

        bad = samples(output, "fritz_lan_packet_count_total", "BAD")
        assert len(bad) == 1
        assert 'direction="rx"' in bad[0]
        assert "NewPacketsSent: expected a number, got str" in caplog.text
        assert SECRET not in caplog.text
        assert SECRET not in output


class TestItemLevel:
    def test_unreadable_label_skips_only_that_item(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario({("WLANConfiguration1", "GetInfo"): wlan1_info(NewEnable="maybe")})

        output = scenario.scrape()

        for metric in ("fritz_wifi_status", "fritz_wifi_channel", "fritz_wifi_packets_count_total"):
            wlans = samples(output, metric, "BAD")
            assert wlans
            assert not any('wifi_index="1"' in line for line in wlans)
            assert any('wifi_index="2"' in line for line in wlans)
        assert len(samples(output, "fritz_wifi_status", "GOOD")) == 3
        assert (
            "Skipped part of the WlanConfigurationInfo data from bad: WLAN 1 skipped: "
            "WLANConfiguration1.GetInfo NewEnable: expected a flag, got str"
        ) in caplog.text


class TestCapabilityLevel:
    def test_reply_of_the_wrong_shape_skips_the_capability(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario({LAN_STATS: "garbage"})

        output = scenario.scrape()

        assert samples(output, "fritz_lan_data_bytes_total", "BAD") == []
        assert samples(output, "fritz_lan_packet_count_total", "BAD") == []
        assert samples(output, "fritz_lan_status", "BAD")
        assert reachable(output, "BAD", "BAD") == 1.0
        assert (
            "Could not read LanInterfaceConfigStatistics data from bad, skipping it for this "
            "cycle: LANEthernetInterfaceConfig1.GetStatistics: expected an object, got str"
        ) in caplog.text

    def test_unexpected_error_is_logged_by_type_and_place_only(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario()

        with patch.object(
            LanInterfaceConfigStatistics,
            "_generate_metric_values",
            side_effect=KeyError(SECRET),
        ):
            output = scenario.scrape()

        assert samples(output, "fritz_lan_data_bytes_total", "GOOD") == []
        assert "Could not read LanInterfaceConfigStatistics data from good" in caplog.text
        assert "KeyError at " in caplog.text
        assert SECRET not in caplog.text


class TestDeviceLevel:
    def test_device_without_serial_is_offline_and_retried(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario({("DeviceInfo1", "GetInfo"): {"NewModelName": "Fritz!Box"}})

        output = scenario.scrape()

        assert [d.friendly_name for d in scenario.collector.devices] == ["GOOD"]
        assert reachable(output, "BAD", "n/a") == 0.0
        assert reachable(output, "GOOD", "GOOD") == 1.0
        assert samples(output, "fritz_lan_status", "GOOD")
        assert "Could not read the serial number of bad" in caplog.text

        # Once the serial can be read, the device is collected.
        del scenario.overrides["bad"][("DeviceInfo1", "GetInfo")]
        output = scenario.scrape()

        assert reachable(output, "BAD", "BAD") == 1.0
        assert samples(output, "fritz_lan_status", "BAD")

    def test_connection_errors_still_mark_the_device_unreachable(self):
        scenario = Scenario()
        original = scenario.fcs["bad"].call_action.side_effect

        def failing(service, action, **kwargs):
            if (service, action) == LAN_STATS:
                raise FritzConnectionException("down")
            return original(service, action, **kwargs)

        scenario.fcs["bad"].call_action.side_effect = failing
        output = scenario.scrape()

        assert reachable(output, "BAD", "BAD") == 0.0
        assert reachable(output, "GOOD", "GOOD") == 1.0


class TestLogging:
    def test_lasting_problem_warns_once_and_recovery_is_logged(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario({LAN_STATS: lan_stats(NewBytesSent=None)})

        def records(level):
            return [
                r
                for r in caplog.records
                if r.levelno == level and "LanInterfaceConfigStatistics" in r.getMessage()
            ]

        for _ in range(3):
            scenario.scrape()
        assert len(records(logging.WARNING)) == 1
        assert len(records(logging.DEBUG)) >= 2

        scenario.overrides["bad"][LAN_STATS] = lan_stats()
        output = scenario.scrape()

        assert len(samples(output, "fritz_lan_data_bytes_total", "BAD")) == 2
        assert [r.getMessage() for r in records(logging.INFO)] == [
            "Reading LanInterfaceConfigStatistics data from bad works again"
        ]

    def test_a_different_problem_warns_again(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario({LAN_STATS: lan_stats(NewBytesSent=None)})
        scenario.scrape()

        scenario.overrides["bad"][LAN_STATS] = "garbage"
        scenario.scrape()

        warnings = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING and "LanInterfaceConfigStatistics" in r.getMessage()
        ]
        assert len(warnings) == 2
        assert warnings[0].startswith("Skipped part of")
        assert warnings[1].startswith("Could not read")


class TestCheckSamples:
    def test_numbers_and_flags_pass(self):
        family = GaugeMetricFamily("m", "h", labels=["a"])
        family.add_metric(["x"], 1)
        family.add_metric(["y"], 2.5)
        family.add_metric(["z"], True)

        _check_samples({"m": family}, {"m": 0})

    def test_only_new_samples_are_checked(self):
        family = GaugeMetricFamily("m", "h", labels=["a"])
        family.add_metric(["x"], "old and not mine")
        family.add_metric(["y"], 1)

        _check_samples({"m": family}, {"m": 1})

    @pytest.mark.parametrize(("labels", "value"), [(["x"], "1"), (["x"], None), ([5], 1)])
    def test_unrenderable_samples_raise(self, labels, value):
        family = GaugeMetricFamily("m", "h", labels=["a"])
        family.add_metric(labels, value)

        with pytest.raises(DataError, match="metric m:"):
            _check_samples({"m": family}, {"m": 0})
