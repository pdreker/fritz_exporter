"""Tests for the fibre level fallback (fritz_fiber.py)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from fritzexporter.fritz_fiber import parse_fiber_stats_response
from fritzexporter.fritz_webui import FritzWebUiError
from fritzexporter.reply import DataError, Issues
from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_capabilities


class TestParseFiberStats:
    def test_newest_entry_of_each_series_in_dbm(self):
        raw = {"data": {"opt_ds": [["sa", 9, -65], ["sa", 8, -66]], "opt_us": [["sa", 9, -54]]}}
        assert parse_fiber_stats_response(raw, Issues()) == {"receive_dbm": -6.5, "transmit_dbm": -5.4}

    def test_missing_series_give_none(self):
        assert parse_fiber_stats_response({"data": []}, Issues()) == {"receive_dbm": None, "transmit_dbm": None}
        raw = {"data": {"opt_ds": [], "opt_us": [["sa", 9, ""]]}}
        assert parse_fiber_stats_response(raw, Issues()) == {"receive_dbm": None, "transmit_dbm": None}

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ({"data": {"opt_us": [["sa", 9, "n/a"]]}}, "fiberStats opt_us[0] value: expected a number"),
            ({"data": {"opt_us": [["sa", 9]]}}, "fiberStats opt_us[0]: expected [weekday, hour, value]"),
            ({"data": {"opt_us": "not a list"}}, "fiberStats data opt_us: expected a list"),
        ],
    )
    def test_defective_series_gives_none_and_is_recorded(self, raw, message):
        raw["data"]["opt_ds"] = [["sa", 9, -65]]
        issues = Issues()

        levels = parse_fiber_stats_response(raw, issues)

        assert levels == {"receive_dbm": -6.5, "transmit_dbm": None}
        assert [m.split(", got")[0] for m in issues.messages] == [message]

    @pytest.mark.parametrize("raw", [{"data": "not an object"}, []])
    def test_reply_of_the_wrong_shape_raises(self, raw):
        with pytest.raises(DataError):
            parse_fiber_stats_response(raw, Issues())


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
        scrapes: int = 1,
    ) -> dict[str, float | None]:
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
        for _ in range(scrapes - 1):
            list(collector.collect())
        by_name = {m.name: m for m in collector.collect()}

        def level(name: str) -> float | None:
            samples = by_name[name].samples
            return samples[0].value if samples else None

        return {
            "receive": level("fritz_fiber_optical_signal_level_dBm"),
            "transmit": level("fritz_fiber_transmit_optical_level_dBm"),
            "threshold samples": len(by_name["fritz_fiber_optical_threshold_dBm"].samples),
        }

    def test_zero_from_tr064_uses_web_ui_levels(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = FIBER_RAW
        levels = self._collect(mock_fritzconnection, tr064_levels=(0, 0), webui_client=client)
        assert levels == {
            "receive": pytest.approx(-6.5),
            "transmit": pytest.approx(-5.4),
            "threshold samples": 2,
        }
        client.fetch_page.assert_called_once_with("fiberStats")

    def test_non_zero_tr064_does_not_call_web_ui(self, mock_fritzconnection: MagicMock):
        client = MagicMock()
        client.fetch_page.return_value = FIBER_RAW
        levels = self._collect(
            mock_fritzconnection, tr064_levels=(-15600, -2500), webui_client=client
        )
        assert levels == {
            "receive": pytest.approx(-15.6),
            "transmit": pytest.approx(-2.5),
            "threshold samples": 2,
        }
        client.fetch_page.assert_not_called()

    def test_web_ui_error_exports_no_level_and_reports_it(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        # 0 from TR-064 means unknown here, not 0 dBm; the other fibre metrics stay.
        client = MagicMock()
        client.fetch_page.side_effect = FritzWebUiError("login failed")
        with caplog.at_level(logging.WARNING):
            levels = self._collect(mock_fritzconnection, tr064_levels=(0, 0), webui_client=client)
        assert levels == {"receive": None, "transmit": None, "threshold samples": 2}
        assert (
            "Skipped part of the WanFiberInterfaceConfig data from FritzFiber (somehost): "
            "optical levels from the web interface skipped: login failed"
        ) in caplog.text

    def test_web_ui_failure_is_not_logged_on_every_scrape(
        self, mock_fritzconnection: MagicMock, caplog
    ):
        client = MagicMock()
        client.fetch_page.side_effect = FritzWebUiError("login failed")
        with caplog.at_level(logging.WARNING):
            self._collect(mock_fritzconnection, tr064_levels=(0, 0), webui_client=client, scrapes=3)
        assert caplog.text.count("optical levels from the web interface skipped") == 1

    def test_malformed_web_ui_data_exports_no_level(self, mock_fritzconnection: MagicMock, caplog):
        client = MagicMock()
        client.fetch_page.return_value = {"data": {"opt_ds": [["sa", 9, "n/a"]]}}
        with caplog.at_level(logging.WARNING):
            levels = self._collect(mock_fritzconnection, tr064_levels=(0, 0), webui_client=client)
        assert levels["receive"] is None
        assert "Skipped part of the WanFiberInterfaceConfig data from FritzFiber (somehost): " in caplog.text
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
