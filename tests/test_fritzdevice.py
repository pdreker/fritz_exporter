import logging
from unittest.mock import MagicMock, call, patch

from fritzexporter.fritzdevice import FritzDevice

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_devices


@patch("fritzexporter.fritzdevice.FritzConnection")
class TestFritzDevice:
    def test_should_create_fritz_device_with_correct_capabilities(
        self, mock_fritzconnection: MagicMock, caplog
    ):

        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        fd = FritzDevice("somehost", "someuser", "password", "Fritz!Mock", False)

        # Check
        assert fd.model == "Fritz!MockBox 9790"
        assert fd.serial == "1234567890"

        assert mock_fritzconnection.call_count == 1
        assert mock_fritzconnection.call_args == call(
            address="somehost", user="someuser", password="password"
        )

    def test_should_complain_about_password(
        self, mock_fritzconnection: MagicMock, caplog
    ):

        # Prepare
        caplog.set_level(logging.DEBUG)
        password: str = "123456789012345678901234567890123"

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        # Act
        _ = FritzDevice("somehost", "someuser", password, "Fritz!Mock", False)

        # Check
        assert (
            "fritzexporter.fritzdevice",
            logging.WARN,
            "Password is longer than 32 characters! Login may not succeed, please see README!",
        ) in caplog.record_tuples
