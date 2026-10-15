"""Tests for the fibre level fallback (fritz_fiber.py)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from fritzexporter.fritz_fiber import parse_fiber_stats_response
from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.reply import DataError
from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_capabilities


class TestParseFiberStats:
    def test_newest_entry_of_each_series_in_dbm(self):
        raw = {"data": {"opt_ds": [["sa", 9, -65], ["sa", 8, -66]], "opt_us": [["sa", 9, -54]]}}
        assert parse_fiber_stats_response(raw) == {"receive_dbm": -6.5, "transmit_dbm": -5.4}

    def test_missing_series_give_none(self):
        assert parse_fiber_stats_response({}) == {"receive_dbm": None, "transmit_dbm": None}
        raw = {"data": {"opt_ds": [], "opt_us": [["sa", 9, ""]]}}
        assert parse_fiber_stats_response(raw) == {"receive_dbm": None, "transmit_dbm": None}

    @pytest.mark.parametrize(
        "raw",
        [
            {"data": {"opt_us": [["sa", 9, "n/a"]]}},
            {"data": {"opt_us": [["sa", 9]]}},
            {"data": {"opt_us": "not a list"}},
            {"data": "not an object"},
            [],
        ],
    )
    def test_malformed_data_raises(self, raw):
        with pytest.raises(DataError):
            parse_fiber_stats_response(raw)


FIBER_RAW = {"data": {"opt_ds": [["sa", 9, -65], ["sa", 8, -66]], "opt_us": [["sa", 9, -54]]}}


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestWanFiberWebUiFallback:
    """WanFiberInterfaceConfig falls back to fiberStats when TR-064 answers zero."""

    def _collect(
        self,
        mock_fritzconnection: MagicMock,
        *,
        tr064_levels: tuple[int, int],
        webui_client: MagicMock | None,
    ) -> dict[str, float]:
        fc = mock_fritzconnection.return_value

        def fiber_call_action_mock(service, action, **kwargs):
            result = call_action_mock(service, action, **kwargs)
            if (service, action) == ("X_AVM-DE_WANFiber1", "GetInfo"):
                result = dict(result)
                result["NewOpticalSignalLevel"], result["NewTransmitOpticalLevel"] = tr064_levels
            return result

        fc.call_action.side_effect = fiber_call_action_mock
        fc.services = create_fc_services(
            {
                **fc_services_capabilities["DeviceInfo"],
                **fc_services_capabilities["WanCommonInterfaceByteRate"],
                "X_AVM-DE_WANFiber1": ["GetInfo", "GetInfoGPON", "GetStatistics"],
            }
        )

        collector = FritzCollector()
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzFiber",
            host_info=False,
        )
        device.webui_client = webui_client
        collector.register(device)
        by_name = {m.name: m for m in collector.collect()}
        return {
            "receive": by_name["fritz_fiber_optical_signal_level_dBm"].samples[0].value,
            "transmit": by_name["fritz_fiber_transmit_optical_level_dBm"].samples[0].value,
        }

    def test_zero_from_tr064_uses_web_ui_levels(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = FIBER_RAW
        levels = self._collect(mock_fritzconnection, tr064_levels=(0, 0), webui_client=client)
        assert levels == {"receive": pytest.approx(-6.5), "transmit": pytest.approx(-5.4)}
        client.fetch_page.assert_called_once_with("fiberStats")

    def test_non_zero_tr064_does_not_call_web_ui(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = FIBER_RAW
        levels = self._collect(
            mock_fritzconnection, tr064_levels=(-15600, -2500), webui_client=client
        )
        assert levels == {"receive": pytest.approx(-15.6), "transmit": pytest.approx(-2.5)}
        client.fetch_page.assert_not_called()

    def test_zero_without_web_ui_client_stays_zero(self, mock_fritzconnection: MagicMock):
        levels = self._collect(mock_fritzconnection, tr064_levels=(0, 0), webui_client=None)
        assert levels == {"receive": 0, "transmit": 0}

    def test_web_ui_error_keeps_zero_and_logs_warning(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        client = MagicMock()
        client.fetch_page.side_effect = FritzWebUiError("login failed")
        with caplog.at_level(logging.WARNING):
            levels = self._collect(mock_fritzconnection, tr064_levels=(0, 0), webui_client=client)
        assert levels == {"receive": 0, "transmit": 0}
        assert "Could not read fibre levels from the web UI of somehost: login failed" in caplog.text

    def test_malformed_web_ui_data_keeps_tr064_values(self, mock_fritzconnection: MagicMock, caplog):
        client = MagicMock()
        client.fetch_page.return_value = {"data": {"opt_ds": [["sa", 9, "n/a"]]}}
        with caplog.at_level(logging.WARNING):
            levels = self._collect(mock_fritzconnection, tr064_levels=(0, 0), webui_client=client)
        assert levels == {"receive": 0, "transmit": 0}
        assert "fiberStats opt_ds[0] value: expected a number, got str" in caplog.text

    @patch("fritzexporter.fritzdevice.FritzWebUiClient")
    def test_fiber_alone_creates_web_ui_client(
        self, mock_webui_client: MagicMock, mock_fritzconnection: MagicMock
    ):
        # No other web-UI capability is present here, so the device must
        # create the client for the fibre fallback during normal initialisation.
        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(
            {
                **fc_services_capabilities["DeviceInfo"],
                "X_AVM-DE_WANFiber1": ["GetInfo"],
            }
        )

        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"),
            "FritzFiber",
            host_info=False,
        )

        assert device.capabilities["WanFiberInterfaceConfig"].present
        assert not any(
            device.capabilities[name].present
            for name in (
                "HomeAutomation",
                "WanDocsisCable",
                "WanSegmentUtilization",
                "WanConnectionStatus",
            )
        )
        assert device.webui_client is mock_webui_client.return_value
