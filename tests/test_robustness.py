"""A malformed reply skips one capability of one device and nothing else."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

from fritzexporter.fritzcapabilities import LanInterfaceConfigStatistics, _check_samples
from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice
from fritzexporter.reply import DataError

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_devices

SECRET = "192.0.2.77"


def fake_fc(serial: str, overrides: dict | None = None) -> MagicMock:
    """A FritzConnection double; ``overrides`` replaces whole action results."""
    overrides = overrides or {}

    def call_action(service, action, **kwargs):
        if (service, action) in overrides:
            return overrides[(service, action)]
        result = call_action_mock(service, action, **kwargs)
        if (service, action) == ("DeviceInfo1", "GetInfo"):
            result = {**result, "NewSerialNumber": serial}
        return result

    fc = MagicMock()
    fc.call_action.side_effect = call_action
    fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
    return fc


def lan_stats(**fields):
    stats = {
        "NewBytesReceived": 1234,
        "NewBytesSent": 9876,
        "NewPacketsReceived": 123,
        "NewPacketsSent": 987,
    }
    stats.update(fields)
    return {k: v for k, v in stats.items() if v is not None}


class Scenario:
    """Two devices: "good" always answers well, "bad" answers what the test says."""

    def __init__(self, bad_stats: dict):
        self.bad_overrides = {("LANEthernetInterfaceConfig1", "GetStatistics"): bad_stats}
        fcs = {"good": fake_fc("GOOD"), "bad": fake_fc("BAD", self.bad_overrides)}
        with (
            patch(
                "fritzexporter.fritzdevice.create_fritz_connection",
                side_effect=lambda **kw: fcs[kw["address"]],
            ),
            patch("fritzexporter.fritzdevice.FritzWebUiClient"),
        ):
            self.collector = FritzCollector()
            for host in ("good", "bad"):
                self.collector.register(
                    FritzDevice(FritzCredentials(host, "user", "pw"), host.upper())
                )
        # The web UI capabilities are not under test here.
        for device in self.collector.devices:
            device.webui_client = None
        self.registry = CollectorRegistry()
        self.registry.register(self.collector)

    def set_bad_stats(self, stats: dict) -> None:
        self.bad_overrides[("LANEthernetInterfaceConfig1", "GetStatistics")] = stats

    def scrape(self) -> str:
        return generate_latest(self.registry).decode()


def samples(output: str, metric: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(metric + "{")]


class TestMalformedReply:
    def test_missing_field_drops_only_that_capability_of_that_device(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario(lan_stats(NewBytesSent=None))

        output = scenario.scrape()

        # The rx sample was added before the tx field failed, and was dropped again.
        lan = samples(output, "fritz_lan_data_bytes_total")
        assert len(lan) == 2
        assert all('serial="GOOD"' in line for line in lan)
        # Everything else of the bad device is still there.
        assert 'fritz_device_reachable{friendly_name="BAD",serial="BAD"} 1.0' in output
        assert any('serial="BAD"' in line for line in samples(output, "fritz_lan_status"))
        assert (
            "Could not read LanInterfaceConfigStatistics data from bad, skipping it for this "
            "cycle: LANEthernetInterfaceConfig1.GetStatistics NewBytesSent: missing"
        ) in caplog.text

    def test_wrong_type_is_reported_without_the_value(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario(lan_stats(NewPacketsSent=SECRET))

        output = scenario.scrape()

        assert not any('serial="BAD"' in line for line in samples(output, "fritz_lan_packet_count_total"))
        assert "NewPacketsSent: expected a number, got str" in caplog.text
        assert SECRET not in caplog.text
        assert SECRET not in output

    def test_lasting_failure_warns_once_and_recovery_is_logged(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario(lan_stats(NewBytesSent=None))

        def records(level):
            return [
                r for r in caplog.records
                if r.levelno == level and "LanInterfaceConfigStatistics" in r.getMessage()
            ]

        for _ in range(3):
            scenario.scrape()
        assert len(records(logging.WARNING)) == 1
        assert len(records(logging.DEBUG)) >= 2

        scenario.set_bad_stats(lan_stats())
        output = scenario.scrape()

        assert len(samples(output, "fritz_lan_data_bytes_total")) == 4
        assert [r.getMessage() for r in records(logging.INFO)] == [
            "Reading LanInterfaceConfigStatistics data from bad works again"
        ]

    def test_unexpected_error_is_logged_by_type_and_place_only(self, caplog):
        caplog.set_level(logging.DEBUG)
        scenario = Scenario(lan_stats())

        with patch.object(
            LanInterfaceConfigStatistics,
            "_generate_metric_values",
            side_effect=KeyError(SECRET),
        ):
            output = scenario.scrape()

        assert samples(output, "fritz_lan_data_bytes_total") == []
        assert "Could not read LanInterfaceConfigStatistics data from good" in caplog.text
        assert "KeyError at " in caplog.text
        assert SECRET not in caplog.text

    def test_connection_errors_still_mark_the_device_unreachable(self, caplog):
        from fritzconnection.core.exceptions import FritzConnectionException

        scenario = Scenario(lan_stats())
        bad = scenario.collector.devices[1]
        original = bad.fc.call_action.side_effect

        def failing(service, action, **kwargs):
            if (service, action) == ("LANEthernetInterfaceConfig1", "GetStatistics"):
                raise FritzConnectionException("down")
            return original(service, action, **kwargs)

        bad.fc.call_action.side_effect = failing
        output = scenario.scrape()

        assert 'fritz_device_reachable{friendly_name="BAD",serial="BAD"} 0.0' in output
        assert 'fritz_device_reachable{friendly_name="GOOD",serial="GOOD"} 1.0' in output


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
