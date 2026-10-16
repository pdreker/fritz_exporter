import logging
from pathlib import Path
from importlib.metadata import version
from http.client import RemoteDisconnected
from unittest.mock import MagicMock, call, patch

import pytest
from fritzconnection.core.exceptions import FritzConnectionException
from requests.exceptions import ConnectionError as RequestsConnectionError

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

    def test_cli_args_version(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--version"])

        args = parse_cmdline()

        assert "version" in args

    def test_if_version_prints_version_and_stops(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--version"])

        with pytest.raises(SystemExit) as pytest_wrapped_exit:
            main()

        captured = capsys.readouterr()
        # The installed version, not the "develop" fallback (regression: the
        # lookup used the import name instead of the distribution name).
        assert captured.out == f"{version('fritz-exporter')}\n"
        assert captured.out != "develop\n"
        assert captured.err == ""
        assert pytest_wrapped_exit.type == SystemExit
        assert pytest_wrapped_exit.value.code == 0

    def test_invalid_config_exits_with_code(self, monkeypatch, caplog):
        monkeypatch.setattr("sys.argv", ["fritzexporter", "--config", "/does/not/exist"])

        with pytest.raises(SystemExit) as pytest_wrapped_exit:
            main()

        assert pytest_wrapped_exit.type == SystemExit
        assert pytest_wrapped_exit.value.code == 1
        assert "Invalid configuration: config file" in caplog.text
        assert "could not be read" in caplog.text
        assert "Traceback" not in caplog.text

    @patch("fritzexporter.__main__.start_http_server")
    @patch("fritzexporter.tr064_remote.FritzConnection")
    def test_valid_args_run_clean(self, mock_fc: MagicMock, mock_http: MagicMock, monkeypatch, caplog):
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
                assert log.getEffectiveLevel() == logging.DEBUG
        assert "Configuration read from tests/conffiles/validconfig.yaml" in caplog.text

    @patch("prometheus_client.core.REGISTRY.register")
    @patch("fritzexporter.__main__.start_http_server")
    @patch("fritzexporter.__main__.FritzCollector")
    def test_every_configured_device_is_added_to_the_collector(
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

        main()

        # Every configured device goes to the collector, which sets it up or
        # reports it as down.
        specs = [c.args[0] for c in mock_collector.add_device.call_args_list]
        assert [spec.creds.host for spec in specs] == ["fritz.box", "repeater-wohnzimmer"]

    @patch("prometheus_client.core.REGISTRY.register")
    @patch("fritzexporter.__main__.start_http_server")
    @patch("fritzexporter.tr064_remote.FritzConnection")
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
                "--log-level",
                "DEBUG",
            ],
        )
        monkeypatch.setenv("FRITZ_EXPORTER_UNDER_TEST", "true")

        caplog.set_level(logging.DEBUG)

        fc = mock_fc.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])

        main()

        # The device is set up with the password from the file.
        password = Path("tests/conffiles/password.txt").read_text().strip()
        assert mock_fc.call_args.kwargs["password"] == password
