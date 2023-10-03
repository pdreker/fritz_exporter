import logging
from unittest.mock import MagicMock, call, patch

import pytest
import requests
from fritzconnection.core.exceptions import (
    FritzActionError,
    FritzArgumentError,
    FritzConnectionException,
    FritzServiceError,
)

from fritzexporter.data_donation import (
    donate_data,
    get_sw_version,
    safe_call_action,
    sanitize_results,
)
from fritzexporter.fritzdevice import FritzDevice

from .fc_services_mock import (
    call_action_mock,
    call_action_no_basic_action_error_mock,
    call_action_no_basic_mock,
    create_fc_services,
    fc_services_capabilities,
    fc_services_devices,
    fc_services_no_basic_info,
)


class MockResponse:
    def __init__(self, json_data, status_code):
        self.json_data = json_data
        self.status_code = status_code

    def json(self):
        return self.json_data

    def raise_for_status(self):
        raise requests.exceptions.HTTPError


@patch("fritzexporter.fritzdevice.FritzConnection")
class TestDataDonation:
    def test_should_return_sw_version(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_capabilities["DeviceInfo"])

        # Act
        fd = FritzDevice("somehost", "someuser", "password", "FritzMock", False)
        version = get_sw_version(fd)

        # Check
        assert version == "1.2"

    def test_should_return_fritz_service_error(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_no_basic_mock
        fc.services = create_fc_services(fc_services_no_basic_info)

        # Act
        fd = FritzDevice("somehost", "someuser", "password", "FritzMock", False)
        version = get_sw_version(fd)

        # Check
        assert "ERROR - FritzServiceError:" in version

    def test_should_return_fritz_action_error(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_no_basic_action_error_mock
        fc.services = create_fc_services(fc_services_no_basic_info)

        # Act
        fd = FritzDevice("somehost", "someuser", "password", "FritzMock", False)
        version = get_sw_version(fd)

        # Check
        assert "ERROR - FritzActionError:" in version

    @pytest.mark.parametrize(
        "exception",
        [FritzServiceError, FritzActionError, FritzArgumentError, FritzConnectionException],
    )
    def test_should_not_raise_exceptions(self, mock_fritzconnection: MagicMock, exception, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_no_basic_info)

        # Act
        fd = FritzDevice("somehost", "someuser", "password", "FritzMock", False)
        fc.call_action.side_effect = exception
        res = safe_call_action(fd, "foo", "bar")

        # Check
        assert "error" in res

    def test_should_return_blacklisted(self, mock_fritzconnection: MagicMock, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        fd = FritzDevice("somehost", "someuser", "password", "FritzMock", False)
        res = safe_call_action(fd, "DeviceConfig1", "GetPersistentData")

        # Check
        assert res == {"error": "<BLACKLISTED>"}

    def test_should_sanitize_data(self, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        input_data = {
            ("WLANConfiguration4", "X_AVM-DE_GetWLANHybridMode"): {
                "NewBSSID": "foobar",
                "foo": "bar",
            }
        }

        # Act
        output = sanitize_results(input_data, sanitation=[])

        # Check
        assert (
            output[("WLANConfiguration4", "X_AVM-DE_GetWLANHybridMode")]["NewBSSID"]
            == "<SANITIZED>"
        )
        assert output[("WLANConfiguration4", "X_AVM-DE_GetWLANHybridMode")]["foo"] == "bar"

    def test_should_not_sanitize_data(self, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        input_data = {
            ("foo", "bar"): {
                "baz": "foobar",
                "quux": "baz",
            }
        }

        # Act
        output = sanitize_results(input_data, sanitation=[])

        # Check
        assert output == input_data

    def test_should_custom_sanitize_field(self, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        input_data = {
            ("foo", "bar"): {
                "baz": "foobar",
                "quux": "baz",
            }
        }

        expected = {
            ("foo", "bar"): {
                "baz": "<SANITIZED>",
                "quux": "baz",
            }
        }

        # Act
        output = sanitize_results(input_data, sanitation=[["foo", "bar", "baz"]])

        # Check
        assert output == expected

    def test_should_custom_sanitize_action(self, caplog):
        # Prepare
        caplog.set_level(logging.DEBUG)

        input_data = {
            ("foo", "bar"): {
                "baz": "foobar",
                "quux": "baz",
            }
        }

        expected = {
            ("foo", "bar"): {
                "baz": "<SANITIZED>",
                "quux": "<SANITIZED>",
            }
        }

        # Act
        output = sanitize_results(input_data, sanitation=[["foo", "bar"]])

        # Check
        assert output == expected

    @patch("fritzexporter.data_donation.requests.post")
    def test_should_produce_sensible_json_data_and_upload(
        self, mock_requests_post: MagicMock, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_capabilities["HostNumberOfEntries"])

        # Act
        fd = FritzDevice("somehost", "someuser", "password", "FritzMock", False)
        donate_data(fd, upload=True)

        # check
        assert mock_requests_post.call_count == 1
        assert mock_requests_post.call_args == call(
            "https://fritz.dreker.de/data/donate",
            data='{"exporter_version": "develop", "fritzdevice": {"model": "Fritz!MockBox 9790", '
            '"os_version": "1.2", "services": {"Hosts1": ["GetHostNumberOfEntries"]}, '
            '"detected_capabilities": ["DeviceInfo", "HostNumberOfEntries", "UserInterface", '
            '"LanInterfaceConfig", "LanInterfaceConfigStatistics", "WanDSLInterfaceConfig", '
            '"WanDSLInterfaceConfigAVM", "WanPPPConnectionStatus", "WanCommonInterfaceConfig", '
            '"WanCommonInterfaceDataBytes", "WanCommonInterfaceByteRate", '
            '"WanCommonInterfaceDataPackets", "WlanConfigurationInfo", "HostInfo", '
            '"HomeAutomation"], "action_results": {"Hosts1": {"GetHostNumberOfEntries": '
            '{"NewHostNumberOfEntries": "3"}}}}}',
            headers={"Content-Type": "application/json"},
        )

    @patch("fritzexporter.data_donation.requests.post")
    def test_should_produce_sensible_json_data_and_not_upload(
        self, mock_requests_post: MagicMock, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_capabilities["HostNumberOfEntries"])

        # Act
        fd = FritzDevice("somehost", "someuser", "password", "FritzMock", False)
        donate_data(fd)

        # check
        assert mock_requests_post.call_count == 0

    @patch(
        "fritzexporter.data_donation.requests.post",
        side_effect=[
            MockResponse({"donation_id": "1234-12345678-12345678-1234"}, 200),
            MockResponse({"error": "Unprocessable Entity"}, 422),
        ],
    )
    def test_should_log_success_with_id(
        self, mock_requests_post: MagicMock, mock_fritzconnection: MagicMock, caplog
    ):
        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_capabilities["HostNumberOfEntries"])

        # Act 1
        fd = FritzDevice("somehost", "someuser", "password", "FritzMock", False)
        donate_data(fd, upload=True)

        # Check 1
        assert (
            "Data donation for device Fritz!MockBox 9790 registered under id "
            "1234-12345678-12345678-1234" in caplog.text
        )

        # Act 2
        with pytest.raises(requests.exceptions.HTTPError):
            donate_data(fd, upload=True)
