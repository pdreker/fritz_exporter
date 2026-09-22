"""Tests for the DOCSIS cable collector (fritz_docsis.py + WanDocsisCable)."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client.core import Metric

from fritzexporter.fritz_docsis import parse_docsis_response
from fritzexporter.fritz_webui import FritzWebUiError
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

# ---------------------------------------------------------------------------
# WanDocsisCable capability metrics
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
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

        # Stub out the web UI client so no real HTTP happens
        mock_client = MagicMock()
        mock_client.fetch_page.return_value = docsis_raw or DOCSIS_RAW
        device.webui_client = mock_client

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
        # The web UI client should be created for the web-interface login.
        assert device.webui_client is not None

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
        mock_client.fetch_page.side_effect = FritzWebUiError("boom")
        device.webui_client = mock_client

        collector.register(device)
        with caplog.at_level(logging.ERROR):
            metrics = list(collector.collect())

        # DOCSIS metric families present but empty (fetch failed), and device
        # reachability still collected
        by_name = {m.name: m for m in metrics}
        assert by_name["fritz_docsis_power_dBmV"].samples == []
        assert "fritz_device_reachable" in by_name

