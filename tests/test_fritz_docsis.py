"""Tests for the DOCSIS cable collector (fritz_docsis.py + WanDocsisCable)."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client.core import Metric

from fritzexporter.fritz_docsis import (
    FritzDocsisClient,
    FritzDocsisError,
    parse_connections_response,
    parse_docsis_response,
    parse_monitor_segment,
)
from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_capabilities


# ---------------------------------------------------------------------------
# Sample data.lua?page=docInfo payload (modeled on the real Fritz!Box response)
# ---------------------------------------------------------------------------

DOCSIS_RAW = {
    "pid": "docInfo",
    "data": {
        "readyState": "ready",
        "channelDs": {
            "docsis31": [
                {
                    "channelID": 33,
                    "modulation": "4096QAM",
                    "frequency": "618.000",
                    "powerLevel": "1.2",
                    "mer": "39.8",
                    "corrErrors": 12345,
                    "nonCorrErrors": 3,
                    "latency": 0.32,
                    "fft": "4K",
                    "plc": "1",
                }
            ],
            "docsis30": [
                {
                    "channelID": 5,
                    "modulation": "256QAM",
                    "frequency": "186.000",
                    "powerLevel": "0.2",
                    "mse": "-38.6",
                    "corrErrors": 16,
                    "nonCorrErrors": 0,
                    "latency": 0.31,
                }
            ],
        },
        "channelUs": {
            "docsis31": [
                {
                    "channelID": 2,
                    "modulation": "64QAM",
                    "frequency": "41.800",
                    "powerLevel": "4.0",
                    "multiplex": "ATDMA",
                }
            ],
            "docsis30": [],
        },
    },
}


def _sample_map(metric: Metric) -> dict[tuple[tuple[str, str], ...], float]:
    return {
        tuple(sorted(sample.labels.items())): sample.value
        for sample in metric.samples
    }


# ---------------------------------------------------------------------------
# parse_docsis_response
# ---------------------------------------------------------------------------


class TestParseDocsisResponse:
    def test_parses_downstream_and_upstream(self):
        data = parse_docsis_response(DOCSIS_RAW)

        assert data["ready_state"] == "ready"
        assert len(data["downstream"]) == 2
        assert len(data["upstream"]) == 1

        ds31 = data["downstream"][0]
        assert ds31["channel_id"] == 33
        assert ds31["standard"] == "DOCSIS 3.1"
        assert ds31["modulation"] == "4096QAM"
        assert ds31["frequency"] == "618.000"
        assert ds31["power_dbmv"] == pytest.approx(1.2)
        assert ds31["mer_db"] == pytest.approx(39.8)
        assert ds31["mse_db"] is None
        assert ds31["corrected_errors"] == 12345
        assert ds31["uncorrected_errors"] == 3
        assert ds31["latency_ms"] == pytest.approx(0.32)
        assert ds31["fft"] == "4K"

        ds30 = data["downstream"][1]
        assert ds30["standard"] == "DOCSIS 3.0"
        assert ds30["mse_db"] == pytest.approx(-38.6)
        assert ds30["mer_db"] is None

        us31 = data["upstream"][0]
        assert us31["channel_id"] == 2
        assert us31["standard"] == "DOCSIS 3.1"
        assert us31["power_dbmv"] == pytest.approx(4.0)

    def test_handles_missing_sections(self):
        data = parse_docsis_response({"data": {}})
        assert data["ready_state"] == "unknown"
        assert data["downstream"] == []
        assert data["upstream"] == []

    def test_handles_string_and_number_values(self):
        raw = {
            "data": {
                "channelDs": {
                    "docsis30": [
                        {
                            "channelID": "7",
                            "modulation": "256QAM",
                            "frequency": "186.000",
                            "powerLevel": "-0.5",
                            "mse": "-38.6",
                            "corrErrors": "16",
                            "nonCorrErrors": "0",
                            "latency": "0.31",
                        }
                    ]
                },
                "channelUs": {"docsis30": []},
            }
        }
        data = parse_docsis_response(raw)
        ch = data["downstream"][0]
        assert ch["channel_id"] == 7
        assert ch["power_dbmv"] == pytest.approx(-0.5)
        assert ch["corrected_errors"] == 16
        assert ch["latency_ms"] == pytest.approx(0.31)


# ---------------------------------------------------------------------------
# FritzDocsisClient authentication
# ---------------------------------------------------------------------------


class TestFritzDocsisClientAuth:
    def test_md5_response(self):
        # Known-good vector: challenge "12345678", password "test"
        # MD5 over UTF-16LE of "12345678-test"
        response = FritzDocsisClient._md5_response("12345678", "test")
        assert response.startswith("12345678-")
        assert len(response) == 8 + 1 + 32

    def test_pbkdf2_response(self):
        challenge = "2$1000$00112233445566778899aabbccddeeff$1000$ffeeddccbbaa99887766554433221100"
        response = FritzDocsisClient._pbkdf2_response(challenge, "test")
        assert response.startswith(challenge + "$")
        assert len(response) == len(challenge) + 1 + 64

    def test_pbkdf2_response_rejects_bad_challenge(self):
        with pytest.raises(FritzDocsisError):
            FritzDocsisClient._pbkdf2_response("not-a-challenge", "test")

    @patch("fritzexporter.fritz_docsis.requests.Session")
    def test_login_uses_pbkdf2_for_modern_firmware(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        # First GET returns a challenge (no valid SID)
        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>2$1000$00112233445566778899aabbccddeeff$1000$"
            "ffeeddccbbaa99887766554433221100</Challenge>"
            "<BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        # POST returns a valid SID
        post_resp = MagicMock()
        post_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.post.return_value = post_resp

        client = FritzDocsisClient("fritz.box", "user", "pass")
        sid = client._login()

        assert sid == "0123456789abcdef"
        # The POST must carry the PBKDF2 response
        posted_data = session.post.call_args.kwargs["data"]
        assert posted_data["response"].startswith("2$")

    @patch("fritzexporter.fritz_docsis.requests.Session")
    def test_login_uses_md5_for_legacy_firmware(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>12345678</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        post_resp = MagicMock()
        post_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.post.return_value = post_resp

        client = FritzDocsisClient("fritz.box", "user", "pass")
        sid = client._login()

        assert sid == "0123456789abcdef"
        posted_data = session.post.call_args.kwargs["data"]
        assert posted_data["response"].startswith("12345678-")

    @patch("fritzexporter.fritz_docsis.requests.Session")
    def test_login_raises_on_wrong_password(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>12345678</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        post_resp = MagicMock()
        post_resp.text = "<SessionInfo><SID>0000000000000000</SID></SessionInfo>"
        session.post.return_value = post_resp

        client = FritzDocsisClient("fritz.box", "user", "wrong")
        with pytest.raises(FritzDocsisError):
            client._login()

    @patch("fritzexporter.fritz_docsis.requests.Session")
    def test_login_raises_when_blocked(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>12345678</Challenge><BlockTime>60</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        client = FritzDocsisClient("fritz.box", "user", "pass")
        with pytest.raises(FritzDocsisError, match="blocked"):
            client._login()


# ---------------------------------------------------------------------------
# FritzDocsisClient data fetching
# ---------------------------------------------------------------------------


class TestFritzDocsisClientFetch:
    @patch("fritzexporter.fritz_docsis.requests.Session")
    def test_fetch_docsis_data_returns_json(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        # Login: return a valid SID directly (no challenge needed)
        get_resp = MagicMock()
        get_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.get.return_value = get_resp

        # data.lua returns the DOCSIS JSON
        post_resp = MagicMock()
        post_resp.text = '{"pid":"docInfo","data":{"readyState":"ready"}}'
        post_resp.json.return_value = {"pid": "docInfo", "data": {"readyState": "ready"}}
        session.post.return_value = post_resp

        client = FritzDocsisClient("fritz.box", "user", "pass")
        result = client.fetch_docsis_data()

        assert result["data"]["readyState"] == "ready"
        # data.lua must be called with the docInfo page params
        posted_data = session.post.call_args.kwargs["data"]
        assert posted_data["page"] == "docInfo"
        assert posted_data["xhrId"] == "all"
        assert posted_data["xhr"] == "1"

    @patch("fritzexporter.fritz_docsis.requests.Session")
    def test_fetch_raises_when_html_returned(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        get_resp = MagicMock()
        get_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.get.return_value = get_resp

        post_resp = MagicMock()
        post_resp.text = "<html><body>login page</body></html>"
        session.post.return_value = post_resp

        client = FritzDocsisClient("fritz.box", "user", "pass")
        with pytest.raises(FritzDocsisError, match="HTML"):
            client.fetch_docsis_data()

    @patch("fritzexporter.fritz_docsis.requests.Session")
    def test_fetch_retries_once_on_session_expiry(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        # First login gives a SID
        get_resp = MagicMock()
        get_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.get.return_value = get_resp

        # First data.lua call returns HTML (session expired), second returns JSON
        html_resp = MagicMock()
        html_resp.text = "<html>login</html>"
        json_resp = MagicMock()
        json_resp.text = '{"data":{"readyState":"ready"}}'
        json_resp.json.return_value = {"data": {"readyState": "ready"}}
        session.post.side_effect = [html_resp, json_resp]

        client = FritzDocsisClient("fritz.box", "user", "pass")
        result = client.fetch_docsis_data()

        assert result["data"]["readyState"] == "ready"
        assert session.post.call_count == 2


# ---------------------------------------------------------------------------
# WanDocsisCable capability metrics
# ---------------------------------------------------------------------------


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanDocsisCable:
    def _collect_docsis_metrics(
        self, mock_fritzconnection: MagicMock, docsis_raw: dict | None = None
    ) -> list[Metric]:
        fc = mock_fritzconnection.return_value

        def cable_call_action_mock(service, action, **kwargs):
            result = call_action_mock(service, action, **kwargs)
            # Report a cable WAN access type so DOCSIS is auto-detected.
            # WANCommonInterfaceConfig1 reports "X_AVM-DE_Cable" on real boxes.
            if (service, action) == ("WANCommonInterfaceConfig1", "GetCommonLinkProperties"):
                result = dict(result)
                result["NewWANAccessType"] = "X_AVM-DE_Cable"
            return result

        fc.call_action.side_effect = cable_call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
        }
        fc.services = create_fc_services(services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )

        # Stub out the DOCSIS client so no real HTTP happens
        mock_client = MagicMock()
        mock_client.fetch_docsis_data.return_value = docsis_raw or DOCSIS_RAW
        device.docsis_client = mock_client

        collector.register(device)
        return list(collector.collect())

    def test_docsis_power_metrics(self, mock_fritzconnection: MagicMock):
        metrics = self._collect_docsis_metrics(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        assert "fritz_docsis_power_dBmV" in by_name
        power = _sample_map(by_name["fritz_docsis_power_dBmV"])

        # Downstream DOCSIS 3.1 ch33
        assert power[
            (
                ("channel_id", "33"),
                ("direction", "downstream"),
                ("friendly_name", "FritzCable"),
                ("serial", "1234567890"),
                ("standard", "DOCSIS 3.1"),
            )
        ] == pytest.approx(1.2)
        # Downstream DOCSIS 3.0 ch5
        assert power[
            (
                ("channel_id", "5"),
                ("direction", "downstream"),
                ("friendly_name", "FritzCable"),
                ("serial", "1234567890"),
                ("standard", "DOCSIS 3.0"),
            )
        ] == pytest.approx(0.2)
        # Upstream DOCSIS 3.1 ch2
        assert power[
            (
                ("channel_id", "2"),
                ("direction", "upstream"),
                ("friendly_name", "FritzCable"),
                ("serial", "1234567890"),
                ("standard", "DOCSIS 3.1"),
            )
        ] == pytest.approx(4.0)

    def test_docsis_mer_and_mse_metrics(self, mock_fritzconnection: MagicMock):
        metrics = self._collect_docsis_metrics(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        mer = _sample_map(by_name["fritz_docsis_mer_dB"])
        assert mer[
            (
                ("channel_id", "33"),
                ("friendly_name", "FritzCable"),
                ("serial", "1234567890"),
                ("standard", "DOCSIS 3.1"),
            )
        ] == pytest.approx(39.8)

        mse = _sample_map(by_name["fritz_docsis_mse_dB"])
        assert mse[
            (
                ("channel_id", "5"),
                ("friendly_name", "FritzCable"),
                ("serial", "1234567890"),
                ("standard", "DOCSIS 3.0"),
            )
        ] == pytest.approx(-38.6)

    def test_docsis_error_and_latency_metrics(self, mock_fritzconnection: MagicMock):
        metrics = self._collect_docsis_metrics(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        corr = _sample_map(by_name["fritz_docsis_corrected_errors"])
        assert corr[
            (
                ("channel_id", "33"),
                ("friendly_name", "FritzCable"),
                ("serial", "1234567890"),
                ("standard", "DOCSIS 3.1"),
            )
        ] == 12345

        uncorr = _sample_map(by_name["fritz_docsis_uncorrected_errors"])
        assert uncorr[
            (
                ("channel_id", "33"),
                ("friendly_name", "FritzCable"),
                ("serial", "1234567890"),
                ("standard", "DOCSIS 3.1"),
            )
        ] == 3

        latency = _sample_map(by_name["fritz_docsis_latency_ms"])
        assert latency[
            (
                ("channel_id", "33"),
                ("friendly_name", "FritzCable"),
                ("serial", "1234567890"),
                ("standard", "DOCSIS 3.1"),
            )
        ] == pytest.approx(0.32)

    def test_docsis_channel_info_labels(self, mock_fritzconnection: MagicMock):
        metrics = self._collect_docsis_metrics(mock_fritzconnection)
        by_name = {m.name: m for m in metrics}

        info = by_name["fritz_docsis_channel_info"]
        samples = {s.labels["channel_id"]: s for s in info.samples}

        ds31 = samples["33"]
        assert ds31.value == 1
        assert ds31.labels["direction"] == "downstream"
        assert ds31.labels["standard"] == "DOCSIS 3.1"
        assert ds31.labels["modulation"] == "4096QAM"
        assert ds31.labels["frequency"] == "618.000"

        us31 = samples["2"]
        assert us31.labels["direction"] == "upstream"
        assert us31.labels["modulation"] == "64QAM"
        assert us31.labels["frequency"] == "41.800"

    def test_docsis_disabled_on_non_cable_box(self, mock_fritzconnection: MagicMock):
        """On a non-cable box (WAN access type != Cable), no DOCSIS samples."""
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
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
        # Metric families are always yielded (matching other capabilities),
        # but with no samples when the collector is disabled.
        assert "fritz_docsis_power_dBmV" in by_name
        assert by_name["fritz_docsis_power_dBmV"].samples == []
        assert by_name["fritz_docsis_channel_info"].samples == []

    def test_docsis_check_capability_disabled_on_non_cable(
        self, mock_fritzconnection: MagicMock
    ):
        """check_capability must not enable DOCSIS on a non-cable box."""
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
        }
        fc.services = create_fc_services(services)

        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )

        # The mock returns NewWANAccessType "PPPoE", so DOCSIS must stay disabled.
        assert device.capabilities["WanDocsisCable"].present is False

    def test_docsis_check_capability_enabled_on_cable(self, mock_fritzconnection: MagicMock):
        """check_capability auto-enables DOCSIS when WAN access type is Cable."""
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
        }
        fc.services = create_fc_services(services)

        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )

        assert device.capabilities["WanDocsisCable"].present is True
        # The DOCSIS client should be created for the web-interface login.
        assert device.docsis_client is not None

    def test_docsis_fetch_error_does_not_break_collection(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        """If the DOCSIS fetch fails, other metrics still collect."""
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
        }
        fc.services = create_fc_services(services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        mock_client = MagicMock()
        mock_client.fetch_docsis_data.side_effect = FritzDocsisError("boom")
        device.docsis_client = mock_client

        collector.register(device)
        with caplog.at_level(logging.ERROR):
            metrics = list(collector.collect())

        # DOCSIS metric families present but empty (fetch failed), and device
        # reachability still collected
        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_docsis_power_dBmV"].samples == []
        assert "fritz_device_reachable" in by_name


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
# WanSegmentUtilizationCable capability metrics
# ---------------------------------------------------------------------------


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanSegmentUtilizationCable:
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
        }
        fc.services = create_fc_services(services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        mock_client = MagicMock()
        mock_client.fetch_docsis_data.return_value = DOCSIS_RAW
        mock_client.fetch_monitor_segment.return_value = segment_raw or SEGMENT_RAW
        device.docsis_client = mock_client

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

    def test_disabled_on_non_cable_box(self, mock_fritzconnection: MagicMock):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
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
        }
        fc.services = create_fc_services(services)

        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        assert device.capabilities["WanSegmentUtilizationCable"].present is True

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
        }
        fc.services = create_fc_services(services)

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        mock_client = MagicMock()
        mock_client.fetch_docsis_data.return_value = DOCSIS_RAW
        mock_client.fetch_monitor_segment.side_effect = FritzDocsisError("boom")
        device.docsis_client = mock_client

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
# WanConnectionStatusCable capability metrics
# ---------------------------------------------------------------------------


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanConnectionStatusCable:
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
        mock_client.fetch_docsis_data.return_value = DOCSIS_RAW
        mock_client.fetch_monitor_segment.return_value = SEGMENT_RAW
        mock_client.fetch_connections.return_value = (
            connections_raw if connections_raw is not None else CONNECTIONS_RAW["connection"]
        )
        device.docsis_client = mock_client

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
        """Disabled capability (no docsis client) yields empty metric families."""
        self._cable_fc(mock_fritzconnection)
        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        device.docsis_client = None
        collector.register(device)
        metrics = list(collector.collect())

        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_wan_connection_uptime_seconds"].samples == []
        assert by_name["fritz_wan_connection_status"].samples == []

    def test_disabled_on_non_cable_box(self, mock_fritzconnection: MagicMock):
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        services = {
            **fc_services_capabilities["DeviceInfo"],
            **fc_services_capabilities["WanCommonInterfaceByteRate"],
        }
        fc.services = create_fc_services(services)

        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzCable",
            host_info=False,
        )
        assert device.capabilities["WanConnectionStatusCable"].present is False

        collector = FritzCollector()
        collector.register(device)
        metrics = list(collector.collect())
        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_wan_connection_uptime_seconds"].samples == []
        assert by_name["fritz_wan_connection_status"].samples == []

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
        mock_client.fetch_docsis_data.return_value = DOCSIS_RAW
        mock_client.fetch_monitor_segment.return_value = SEGMENT_RAW
        mock_client.fetch_connections.side_effect = FritzDocsisError("boom")
        device.docsis_client = mock_client

        collector.register(device)
        with caplog.at_level(logging.ERROR):
            metrics = list(collector.collect())

        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_wan_connection_uptime_seconds"].samples == []
        assert by_name["fritz_wan_connection_status"].samples == []
        assert "fritz_device_reachable" in by_name
