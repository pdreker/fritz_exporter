import logging
from unittest.mock import MagicMock, call, patch

import pytest
from fritzconnection.core.exceptions import FritzConnectionException

from fritzexporter.__main__ import main, parse_cmdline

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_devices


class Test_Main:
    def test_cli_args_log_level(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--log-level", "DEBUG"])

        args = parse_cmdline()

        assert "log_level" in args
        assert args.log_level == "DEBUG"

    def test_cli_args_config(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--config", "/some/file.yaml"])

        args = parse_cmdline()

        assert "config" in args
        assert args.config == "/some/file.yaml"

    def test_cli_args_donate_data(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--donate-data"])

        args = parse_cmdline()

        assert "donate_data" in args

    def test_cli_args_upload_data(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--upload-data"])

        args = parse_cmdline()

        assert "donate_data" in args

    def test_cli_args_sanitize(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["fritzexporter", "--sanitize", "FOO", "BAR", "-s", "FOOBAR", "BLABLA", "SOMETHING"],
        )

        args = parse_cmdline()

        assert "sanitize" in args
        assert args.sanitize == [["FOO", "BAR"], ["FOOBAR", "BLABLA", "SOMETHING"]]

    def test_cli_args_version(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--version"])

        args = parse_cmdline()

        assert "version" in args

    def test_if_version_prints_version_and_stops(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--version"])

        with pytest.raises(SystemExit) as pytest_wrapped_exit:
            main()

        captured = capsys.readouterr()
        assert captured.out == "develop\n"
        assert captured.err == ""
        assert pytest_wrapped_exit.type == SystemExit
        assert pytest_wrapped_exit.value.code == 0

    def test_invalid_config_exits_with_code(self, monkeypatch, caplog):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--config", "/does/not/exist"])

        with pytest.raises(SystemExit) as pytest_wrapped_exit:
            main()

        assert pytest_wrapped_exit.type == SystemExit
        assert pytest_wrapped_exit.value.code == 1
        assert "fritzexporter.config.exceptions.ConfigFileUnreadableError" in caplog.text

    @patch("fritzexporter.fritzdevice.FritzConnection")
    def test_valid_args_run_clean(self, mock_fc: MagicMock, monkeypatch, caplog):
        monkeypatch.setattr(
            "sys.argv",
            [
                "fritzexporter",
                "--config",
                "tests/conffiles/validconfig.yaml",
                "--log-level",
                "DEBUG",
            ],
        )
        monkeypatch.setenv("FRITZ_EXPORTER_UNDER_TEST", "true")  # do not enter infinite loop

        # Prepare
        caplog.set_level(logging.DEBUG)

        fc = mock_fc.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        main()

        loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict]
        for log in loggers:
            if log.name.startswith("fritzexporter"):
                assert log.level == logging.DEBUG

    @patch("prometheus_client.core.REGISTRY.register")
    @patch("fritzexporter.fritzdevice.FritzConnection")
    def test_donate_data_path(
        self, mock_fc: MagicMock, mock_registry: MagicMock, monkeypatch, capsys, caplog
    ):
        monkeypatch.setattr(
            "sys.argv",
            [
                "fritzexporter",
                "--config",
                "tests/conffiles/validconfig.yaml",
                "--donate-data",
            ],
        )
        monkeypatch.setenv("FRITZ_EXPORTER_UNDER_TEST", "true")

        caplog.set_level(logging.DEBUG)

        fc = mock_fc.return_value
        fc.call_action.side_effect = call_action_mock
        # Use minimal services so donate_data doesn't call unknown actions
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        # donate-data path calls sys.exit(0) after donating data for the first device
        assert exc_info.value.code == 0
        # Check that donation output was printed
        captured = capsys.readouterr()
        assert "Donation data for device" in captured.out

    @patch("prometheus_client.core.REGISTRY.register")
    @patch("fritzexporter.__main__.start_http_server")
    @patch("fritzexporter.__main__.FritzCollector")
    def test_startup_connection_failure_registers_device_as_offline(
        self, mock_collector_cls: MagicMock, mock_http: MagicMock, mock_registry: MagicMock,
        monkeypatch, caplog
    ):
        monkeypatch.setattr(
            "sys.argv",
            [
                "fritzexporter",
                "--config",
                "tests/conffiles/validconfig.yaml",
            ],
        )
        monkeypatch.setenv("FRITZ_EXPORTER_UNDER_TEST", "true")

        caplog.set_level(logging.DEBUG)

        mock_collector = MagicMock()
        mock_collector_cls.return_value = mock_collector

        # Simulate device being unreachable at startup
        with patch("fritzexporter.__main__.FritzDevice") as mock_device_cls:
            mock_device_cls.side_effect = FritzConnectionException("connection refused")
            main()

        # Check that the error was logged
        assert any(
            "Failed to initialize device" in record.message
            for record in caplog.records
            if record.levelno == logging.ERROR
        )

        # Check that register_offline was called for both devices from validconfig.yaml
        assert mock_collector.register_offline.call_count == 2
        hostnames = [c.args[0] for c in mock_collector.register_offline.call_args_list]
        assert "fritz.box" in hostnames
        assert "repeater-wohnzimmer" in hostnames  # hostnames are lowercased by DeviceConfig

    @patch("prometheus_client.core.REGISTRY.register")
    @patch("fritzexporter.__main__.start_http_server")
    @patch("fritzexporter.fritzdevice.FritzConnection")
    def test_password_file_reading(
        self, mock_fc: MagicMock, mock_http: MagicMock, mock_registry: MagicMock,
        monkeypatch, caplog
    ):
        monkeypatch.setattr(
            "sys.argv",
            [
                "fritzexporter",
                "--config",
                "tests/conffiles/password_file.yaml",
            ],
        )
        monkeypatch.setenv("FRITZ_EXPORTER_UNDER_TEST", "true")

        caplog.set_level(logging.DEBUG)

        fc = mock_fc.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        main()

        assert "Using password from password file" in caplog.text
