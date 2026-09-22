"""Tests for the technology-agnostic Fritz!Box REST API collectors.

Covers the ``/api/v0/monitor/segment/<n>`` and ``/api/v0/generic/connections``
parsers (``fritz_rest_generic.py``) and the ``WanSegmentUtilization`` /
``WanConnectionStatus`` capabilities (``fritzcapabilities.py``).
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client.core import Metric

from fritzexporter.fritz_rest_generic import (
    parse_connections_response,
    parse_monitor_segment,
)
from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_capabilities


def _sample_map(metric: Metric) -> dict[tuple[tuple[str, str], ...], float]:
    return {
        tuple(sorted(sample.labels.items())): sample.value
        for sample in metric.samples
    }


# ---------------------------------------------------------------------------
# Sample /api/v0/monitor/segment/0 payload (modeled on api_segment_0.json)
# ---------------------------------------------------------------------------
# Newest sample of every series is the LAST element of each list.

SEGMENT_RAW = {
    "data": [
        {
            "mediaType": "cable",
            "type": "own",
            "downstream": [1.0, 2.0, 1.2345],
            "upstream": [3.0, 4.0, 9.87],
        },
        {
            "mediaType": "cable",
            "type": "total",
            "downstream": [5.0, 6.0, 12.345],
            "upstream": [7.0, 8.0, 54.321],
        },
    ],
    "lastSampleTime": 1789671960,
    "sampleInterval": 60000,
}


# ---------------------------------------------------------------------------
# parse_monitor_segment
# ---------------------------------------------------------------------------


class TestParseMonitorSegment:
    def test_parses_series_and_last_sample_time(self):
        data = parse_monitor_segment(SEGMENT_RAW)

        assert data["last_sample_time"] == 1789671960
        assert len(data["series"]) == 2
        assert [s["type"] for s in data["series"]] == ["own", "total"]

        own = data["series"][0]
        assert own["media_type"] == "cable"
        assert own["downstream"] == [1.0, 2.0, 1.2345]
        assert own["upstream"][-1] == pytest.approx(9.87)

    def test_handles_string_and_null_samples(self):
        raw = {
            "data": [
                {
                    "mediaType": "cable",
                    "type": "own",
                    "downstream": ["0.5", None, ""],
                    "upstream": [1],
                }
            ],
            "lastSampleTime": "1700000000",
        }
        data = parse_monitor_segment(raw)

        assert data["last_sample_time"] == 1700000000
        series = data["series"][0]
        assert series["downstream"][0] == pytest.approx(0.5)
        assert series["downstream"][1] is None
        assert series["downstream"][2] is None
        assert series["upstream"] == [1.0]

    def test_handles_empty_response(self):
        data = parse_monitor_segment({})
        assert data["last_sample_time"] is None
        assert data["series"] == []


# ---------------------------------------------------------------------------
# WanSegmentUtilization capability metrics
# ---------------------------------------------------------------------------


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanSegmentUtilization:
    def _collect(self, mock_fritzconnection: MagicMock, segment_raw: dict | None = None):
        fc = mock_fritzconnection.return_value

        def cable_call_action_mock(service, action, **kwargs):
            result = call_action_mock(service, action, **kwargs)
            if (service, action) == ("WANCommonInterfaceConfig1", "GetCommonLinkProperties"):
                result = dict(result)
                result["NewWANAccessType"] = "X_AVM-DE_Cable"
            return result

        fc.call_action.side_effect = cable_call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
            **fc_services_capabilities["WanCommonInterfaceConfig"],
        }
        fc.services = create_fc_services(services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        mock_client = MagicMock()
        mock_client.fetch_page.return_value = {}
        mock_client.fetch_api.return_value = segment_raw or SEGMENT_RAW
        device.webui_client = mock_client

        collector.register(device)
        return list(collector.collect())

    def test_exposes_latest_sample_for_all_scopes(self, mock_fritzconnection: MagicMock):
        metrics = self._collect(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        assert "fritz_cable_segment_utilization_percent" in by_name
        util = _sample_map(by_name["fritz_cable_segment_utilization_percent"])
        assert util  # at least one sample was emitted

        # Newest value of each of the four (direction, scope) combinations.
        keyed = {
            (s.labels["direction"], s.labels["scope"]): s.value
            for s in by_name["fritz_cable_segment_utilization_percent"].samples
        }
        assert keyed[("downstream", "own")] == pytest.approx(1.2345)
        assert keyed[("upstream", "own")] == pytest.approx(9.87)
        assert keyed[("downstream", "total")] == pytest.approx(12.345)
        assert keyed[("upstream", "total")] == pytest.approx(54.321)
        assert set(keyed) == {
            ("downstream", "own"),
            ("upstream", "own"),
            ("downstream", "total"),
            ("upstream", "total"),
        }

    def test_sample_age_uses_last_sample_time(self, mock_fritzconnection: MagicMock):
        metrics = self._collect(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        assert "fritz_cable_segment_sample_timestamp_seconds" in by_name
        age = by_name["fritz_cable_segment_sample_timestamp_seconds"]
        assert len(age.samples) == 1
        assert age.samples[0].value == 1789671960
        assert age.samples[0].labels["friendly_name"] == "FritzCable"

    def test_skips_null_newest_sample(self, mock_fritzconnection: MagicMock):
        raw = {
            "data": [
                {
                    "mediaType": "cable",
                    "type": "own",
                    "downstream": [1.0, 2.0, None],
                    "upstream": [],
                }
            ],
            "lastSampleTime": 1700000000,
        }
        metrics = self._collect(mock_fritzconnection, raw)
        util = next(m for m in metrics if m.name == "fritz_cable_segment_utilization_percent")
        # downstream newest is null and upstream is empty -> nothing emitted
        assert util.samples == []

    def test_enabled_on_non_cable_box(self, mock_fritzconnection: MagicMock):
        """The REST API is not cable-specific; present on any box with the
        common WAN interface service."""
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
            **fc_services_capabilities["WanCommonInterfaceConfig"],
        }
        fc.services = create_fc_services(services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        collector.register(device)
        metrics = list(collector.collect())

        by_name = {m.name: m for m in metrics}
        assert "fritz_cable_segment_utilization_percent" in by_name
        assert by_name["fritz_cable_segment_utilization_percent"].samples == []
        assert by_name["fritz_cable_segment_sample_timestamp_seconds"].samples == []

    def test_check_capability_enabled_on_cable(self, mock_fritzconnection: MagicMock):
        fc = mock_fritzconnection.return_value

        def cable_call_action_mock(service, action, **kwargs):
            result = call_action_mock(service, action, **kwargs)
            if (service, action) == ("WANCommonInterfaceConfig1", "GetCommonLinkProperties"):
                result = dict(result)
                result["NewWANAccessType"] = "X_AVM-DE_Cable"
            return result

        fc.call_action.side_effect = cable_call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
            **fc_services_capabilities["WanCommonInterfaceConfig"],
        }
        fc.services = create_fc_services(services)

        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        assert device.capabilities["WanSegmentUtilization"].present is True

    def test_fetch_error_does_not_break_collection(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        fc = mock_fritzconnection.return_value

        def cable_call_action_mock(service, action, **kwargs):
            result = call_action_mock(service, action, **kwargs)
            if (service, action) == ("WANCommonInterfaceConfig1", "GetCommonLinkProperties"):
                result = dict(result)
                result["NewWANAccessType"] = "X_AVM-DE_Cable"
            return result

        fc.call_action.side_effect = cable_call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
            **fc_services_capabilities["WanCommonInterfaceConfig"],
        }
        fc.services = create_fc_services(services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        mock_client = MagicMock()
        mock_client.fetch_page.return_value = {}
        mock_client.fetch_api.side_effect = FritzWebUiError("boom")
        device.webui_client = mock_client

        collector.register(device)
        with caplog.at_level(logging.ERROR):
            metrics = list(collector.collect())

        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_cable_segment_utilization_percent"].samples == []
        assert "fritz_device_reachable" in by_name


# ---------------------------------------------------------------------------
# Sample /api/v0/generic/connections payload (modeled on api_connections.json)
# ---------------------------------------------------------------------------

CONNECTIONS_RAW = {
    "connection": [
        {
            "ip6_mode": "ipv6_native",
            "ip4_uptime": "689223",
            "conn_date": "09.09.2026",
            "ip6_connstatus": "connected",
            "name": "internet",
            "media_type": "Cable",
            "UID": "connection0001",
            "ip4_connstatus": "connected",
            "ip6_uptime": "689224",
        },
        {
            "ip6_mode": "ipv6_off",
            "name": "internet2",
            "media_type": "LTE",
            "UID": "connection0002",
            "ip4_uptime": "",
            "ip6_uptime": "",
            "ip4_connstatus": "disabled",
            "ip6_connstatus": "disabled",
        },
    ],
    "opmode": "opmode_standard",
}


# ---------------------------------------------------------------------------
# parse_connections_response
# ---------------------------------------------------------------------------


class TestParseConnectionsResponse:
    def test_parses_entries(self):
        connections = parse_connections_response(CONNECTIONS_RAW["connection"])

        assert len(connections) == 2
        active, disabled = connections
        assert active["uid"] == "connection0001"
        assert active["name"] == "internet"
        assert active["media_type"] == "Cable"
        assert active["ip4_uptime"] == 689223
        assert active["ip6_uptime"] == 689224
        assert active["ip4_connstatus"] == "connected"
        assert active["ip6_connstatus"] == "connected"

        assert disabled["ip4_uptime"] is None
        assert disabled["ip6_uptime"] is None
        assert disabled["ip4_connstatus"] == "disabled"

    def test_handles_empty_list(self):
        assert parse_connections_response([]) == []


# ---------------------------------------------------------------------------
# WanConnectionStatus capability metrics
# ---------------------------------------------------------------------------


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanConnectionStatus:
    def _cable_fc(self, mock_fritzconnection: MagicMock) -> MagicMock:
        fc = mock_fritzconnection.return_value

        def cable_call_action_mock(service, action, **kwargs):
            result = call_action_mock(service, action, **kwargs)
            if (service, action) == ("WANCommonInterfaceConfig1", "GetCommonLinkProperties"):
                result = dict(result)
                result["NewWANAccessType"] = "X_AVM-DE_Cable"
            return result

        fc.call_action.side_effect = cable_call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
            **fc_services_capabilities["WanCommonInterfaceConfig"],
        }
        fc.services = create_fc_services(services)
        return fc

    def _collect(self, mock_fritzconnection: MagicMock, connections_raw: list | None = None):
        self._cable_fc(mock_fritzconnection)
        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        mock_client = MagicMock()
        mock_client.fetch_page.return_value = {}
        mock_client.fetch_api.return_value = (
            connections_raw if connections_raw is not None else CONNECTIONS_RAW
        )
        device.webui_client = mock_client

        collector.register(device)
        return list(collector.collect())

    def test_uptime_samples_per_stack(self, mock_fritzconnection: MagicMock):
        metrics = self._collect(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        assert "fritz_wan_connection_uptime_seconds" in by_name
        keyed = {
            (s.labels["connection"], s.labels["stack"]): s.value
            for s in by_name["fritz_wan_connection_uptime_seconds"].samples
        }
        assert keyed == {
            ("connection0001", "ipv4"): 689223,
            ("connection0001", "ipv6"): 689224,
        }
        # the disabled connection reports empty uptimes -> no samples
        sample = by_name["fritz_wan_connection_uptime_seconds"].samples[0]
        assert sample.labels["friendly_name"] == "FritzCable"
        assert sample.labels["connection_name"] == "internet"

    def test_status_samples_carry_state_label(self, mock_fritzconnection: MagicMock):
        metrics = self._collect(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        assert "fritz_wan_connection_status" in by_name
        keyed = {
            (s.labels["connection"], s.labels["stack"], s.labels["state"]): s.value
            for s in by_name["fritz_wan_connection_status"].samples
        }
        assert keyed == {
            ("connection0001", "ipv4", "connected"): 1,
            ("connection0001", "ipv6", "connected"): 1,
            ("connection0002", "ipv4", "disabled"): 0,
            ("connection0002", "ipv6", "disabled"): 0,
        }

    def test_no_client_no_samples(self, mock_fritzconnection: MagicMock):
        """Disabled capability (no web UI client) yields empty metric families."""
        self._cable_fc(mock_fritzconnection)
        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        device.webui_client = None
        collector.register(device)
        metrics = list(collector.collect())

        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_wan_connection_uptime_seconds"].samples == []
        assert by_name["fritz_wan_connection_status"].samples == []

    def test_enabled_on_non_cable_box(self, mock_fritzconnection: MagicMock):
        """The REST API is not cable-specific; present on any box with the
        common WAN interface service."""
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
            **fc_services_capabilities["WanCommonInterfaceConfig"],
        }
        fc.services = create_fc_services(services)

        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        assert device.capabilities["WanConnectionStatus"].present is True

        collector = FritzCollector()
        collector.register(device)
        metrics = list(collector.collect())
        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_wan_connection_uptime_seconds"].samples == []
        assert by_name["fritz_wan_connection_status"].samples == []

    def test_fetches_connection_status(
        self, mock_fritzconnection: MagicMock
    ):
        """The REST connection status is technology-independent: it is fetched
        and emitted regardless of the WAN access type."""
        fc = mock_fritzconnection.return_value
        # Plain call_action_mock reports NewWANAccessType "PPPoE"; the
        # capability is enabled via the common WAN interface service alone.
        fc.call_action.side_effect = call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
            **fc_services_capabilities["WanCommonInterfaceConfig"],
        }
        fc.services = create_fc_services(services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        assert device.capabilities["WanConnectionStatus"].present is True

        mock_client = MagicMock()
        mock_client.fetch_page.return_value = {}
        mock_client.fetch_api.return_value = CONNECTIONS_RAW
        device.webui_client = mock_client

        collector.register(device)
        metrics = list(collector.collect())
        by_name = {m.name: m for m in metrics}

        # Connection status was fetched and emitted on the non-cable box.
        keyed = {
            (s.labels["connection"], s.labels["stack"], s.labels["state"]): s.value
            for s in by_name["fritz_wan_connection_status"].samples
        }
        assert keyed == {
            ("connection0001", "ipv4", "connected"): 1,
            ("connection0001", "ipv6", "connected"): 1,
            ("connection0002", "ipv4", "disabled"): 0,
            ("connection0002", "ipv6", "disabled"): 0,
        }
        # WanSegmentUtilization shares the same web UI client, so fetch_api is
        # also called for the segment endpoint; assert the connections endpoint
        # was among the calls.
        mock_client.fetch_api.assert_any_call("/api/v0/generic/connections")

    def test_fetch_error_does_not_break_collection(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        self._cable_fc(mock_fritzconnection)
        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        mock_client = MagicMock()
        mock_client.fetch_page.return_value = {}
        mock_client.fetch_api.side_effect = FritzWebUiError("boom")
        device.webui_client = mock_client

        collector.register(device)
        with caplog.at_level(logging.ERROR):
            metrics = list(collector.collect())

        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_wan_connection_uptime_seconds"].samples == []
        assert by_name["fritz_wan_connection_status"].samples == []
        assert "fritz_device_reachable" in by_name
