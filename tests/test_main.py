import logging
from unittest.mock import MagicMock, call, patch

import pytest

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
