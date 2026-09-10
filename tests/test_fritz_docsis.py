"""Tests for the DOCSIS cable collector (fritz_docsis.py + WanDocsisCable)."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client.core import Metric

from fritzexporter.fritz_docsis import (
    FritzDocsisClient,
    FritzDocsisError,
    parse_docsis_response,
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
            docsis=True,
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

    def test_docsis_disabled_by_default(self, mock_fritzconnection: MagicMock):
        """Without the docsis flag, no DOCSIS samples are produced."""
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
            docsis=False,
        )
        collector.register(device)
        metrics = list(collector.collect())

        by_name = {m.name: m for m in metrics}
        # Metric families are always yielded (matching other capabilities),
        # but with no samples when the collector is disabled.
        assert "fritz_docsis_power_dBmV" in by_name
        assert by_name["fritz_docsis_power_dBmV"].samples == []
        assert by_name["fritz_docsis_channel_info"].samples == []

    def test_docsis_check_capability_keeps_disabled(self, mock_fritzconnection: MagicMock):
        """check_capability must not auto-enable DOCSIS (empty requirements)."""
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
            docsis=False,
        )

        # check_present runs during FritzCapabilities.__init__; the DOCSIS
        # capability must stay disabled even though it has no TR-064
        # requirements (all([]) would otherwise be True).
        assert device.capabilities["WanDocsisCable"].present is False

    def test_docsis_fetch_error_does_not_break_collection(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        """If the DOCSIS fetch fails, other metrics still collect."""
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
            docsis=True,
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
