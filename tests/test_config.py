import logging

import pytest

from fritzexporter.config import (
    ConfigError,
    ConfigFileUnreadableError,
    DeviceConfig,
    EmptyConfigError,
    ExporterConfig,
    NoDevicesFoundError,
    get_config,
)


class TestReadConfig:
    def test_file_not_found(self):
        testfile = "this/does/not/exist"

        with pytest.raises(ConfigFileUnreadableError):
            get_config(testfile)

    def test_no_config(self, monkeypatch):
        testfile = None

        monkeypatch.delenv("FRITZ_HOSTNAME", raising=False)
        monkeypatch.delenv("FRITZ_USERNAME", raising=False)
        monkeypatch.delenv("FRITZ_PASSWORD", raising=False)
        monkeypatch.delenv("FRITZ_NAME", raising=False)
        with pytest.raises(ConfigError):
            get_config(testfile)


class TestFileConfigs:
    def test_empty_file(self):
        testfile = "tests/conffiles/empty.yaml"

        with pytest.raises(EmptyConfigError):
            _ = get_config(testfile)

    def test_empty_devices(self):
        testfile = "tests/conffiles/emptydevices.yaml"

        with pytest.raises(NoDevicesFoundError):
            _ = get_config(testfile)

    def test_malformed_device(self):
        testfile = "tests/conffiles/malformeddevice.yaml"

        with pytest.raises(ValueError):
            _ = get_config(testfile)

    def test_nodevices(self):
        testfile = "tests/conffiles/nodevices.yaml"

        with pytest.raises(NoDevicesFoundError):
            _ = get_config(testfile)

    def test_invalidport(self):
        testfile = "tests/conffiles/invalidport.yaml"

        with pytest.raises(ValueError):
            _ = get_config(testfile)

    def test_valid_file(self):
        testfile = "tests/conffiles/validconfig.yaml"

        expected = ExporterConfig(
            listen_address="127.0.0.1",
            devices=[
                DeviceConfig(
                    "fritz.box",
                    "prometheus1",
                    "prometheus2",
                    None,
                    "Fritz!Box 7590 Router",
                    False,
                ),
                DeviceConfig(
                    "repeater-Wohnzimmer",
                    "prometheus3",
                    "prometheus4",
                    None,
                    "Repeater Wohnzimmer",
                    False,
                ),
            ]
        )

        config = get_config(testfile)
        assert config == expected

    def test_password_file(self):
        testfile = "tests/conffiles/password_file.yaml"

        expected = ExporterConfig(
            devices=[
                DeviceConfig(
                    "fritz.box",
                    "prometheus1",
                    None,
                    "tests/conffiles/password.txt",
                    "Fritz!Box 7590 Router",
                    False,
                ),
            ]
        )
        config = get_config(testfile)
        assert config == expected

class TestEnvConfig:
    def test_env_config(self, monkeypatch):
        monkeypatch.setenv("FRITZ_HOSTNAME", "hostname.local")
        monkeypatch.setenv("FRITZ_USERNAME", "SomeUserName")
        monkeypatch.setenv("FRITZ_PASSWORD", "AnInterestingPassword")
        monkeypatch.setenv("FRITZ_NAME", "My Fritz Device")
        monkeypatch.setenv("FRITZ_LISTEN_ADDRESS", "127.0.0.2")
        monkeypatch.setenv("FRITZ_PORT", "12345")
        monkeypatch.setenv("FRITZ_LOG_LEVEL", "INFO")

        config = get_config(None)
        devices: list[DeviceConfig] = [
            DeviceConfig(
                "hostname.local",
                "SomeUserName",
                "AnInterestingPassword",
                None,
                "My Fritz Device",
            )
        ]
        expected: ExporterConfig = ExporterConfig(12345, "INFO", devices, "127.0.0.2")

        assert config == expected

    def test_minimal_env_config(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "SomeUserName")
        monkeypatch.setenv("FRITZ_PASSWORD", "AnInterestingPassword")

        config = get_config(None)
        devices: list[DeviceConfig] = [
            DeviceConfig(
                "fritz.box", "SomeUserName", "AnInterestingPassword", None, "Fritz!Box"
            )
        ]
        expected: ExporterConfig = ExporterConfig(9787, "INFO", devices)

        assert config == expected

    def test_password_file_env_config(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "SomeUserName")
        monkeypatch.setenv("FRITZ_PASSWORD_FILE", "tests/conffiles/password.txt")

        config = get_config(None)
        devices: list[DeviceConfig] = [
            DeviceConfig(
                "fritz.box", "SomeUserName", None, "tests/conffiles/password.txt", "Fritz!Box"
            )
        ]
        expected: ExporterConfig = ExporterConfig(9787, "INFO", devices)

        assert config == expected

    def test_connection_timeout_env_config(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "SomeUserName")
        monkeypatch.setenv("FRITZ_PASSWORD", "AnInterestingPassword")
        monkeypatch.setenv("FRITZ_CONNECTION_TIMEOUT", "15")

        config = get_config(None)

        assert config.devices[0].connection_timeout == 15

    def test_connection_timeout_env_zero_disables_timeout(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "SomeUserName")
        monkeypatch.setenv("FRITZ_PASSWORD", "AnInterestingPassword")
        monkeypatch.setenv("FRITZ_CONNECTION_TIMEOUT", "0")

        config = get_config(None)

        assert config.devices[0].connection_timeout is None


class TestConfigEdgeCases:
    def test_bind_all_interfaces_logs_warning_for_ipv6(self, caplog):
        caplog.set_level(logging.WARNING)

        ExporterConfig.from_config(
            {
                "listen_address": "::",
                "devices": [
                    {
                        "hostname": "fritz.box",
                        "username": "user",
                        "password": "password",
                    }
                ],
            }
        )

        assert "Binding to all interfaces" in caplog.text

    def test_duplicate_device_names_logs_warning(self, caplog):
        testfile = "tests/conffiles/namesnotunique.yaml"
        caplog.set_level(logging.WARNING)

        # Should succeed but log a warning
        config = get_config(testfile)

        assert config is not None
        assert "Device names are not unique" in caplog.text

    def test_password_too_long_raises_error(self):
        from fritzexporter.config.exceptions import FritzPasswordTooLongError

        with pytest.raises(FritzPasswordTooLongError):
            DeviceConfig(
                hostname="fritz.box",
                username="user",
                password="a" * 33,  # 33 chars exceeds 32
            )

    def test_password_too_long_error_message(self):
        from fritzexporter.config.exceptions import FritzPasswordTooLongError

        try:
            DeviceConfig(
                hostname="fritz.box",
                username="user",
                password="a" * 33,
            )
        except FritzPasswordTooLongError as e:
            assert "Password is longer than 32 characters" in str(e)

    def test_password_file_not_found_raises_error(self):
        from fritzexporter.config.exceptions import FritzPasswordFileDoesNotExistError

        with pytest.raises(FritzPasswordFileDoesNotExistError):
            DeviceConfig(
                hostname="fritz.box",
                username="user",
                password_file="/this/does/not/exist/password.txt",
            )

    def test_password_file_not_found_error_message(self):
        from fritzexporter.config.exceptions import FritzPasswordFileDoesNotExistError

        try:
            DeviceConfig(
                hostname="fritz.box",
                username="user",
                password_file="/this/does/not/exist/password.txt",
            )
        except FritzPasswordFileDoesNotExistError as e:
            assert "Password file does not exist" in str(e)

    def test_connection_timeout_parsed_from_config(self):
        testfile = "tests/conffiles/config_with_timeout.yaml"

        config = get_config(testfile)

        assert config.devices[0].connection_timeout == 10
        assert config.devices[1].connection_timeout is None

    def test_connection_timeout_defaults_to_none(self):
        testfile = "tests/conffiles/validconfig.yaml"

        config = get_config(testfile)

        for dev in config.devices:
            assert dev.connection_timeout is None

    def test_connection_timeout_zero_disables_timeout(self):
        config = DeviceConfig(
            hostname="fritz.box",
            username="user",
            password="password",
            connection_timeout=0,
        )
        assert config.connection_timeout is None

    def test_connection_timeout_must_not_be_negative(self):
        with pytest.raises(ValueError):
            DeviceConfig(
                hostname="fritz.box",
                username="user",
                password="password",
                connection_timeout=-1,
            )

    def test_connection_timeout_must_be_numeric(self):
        with pytest.raises(ValueError):
            DeviceConfig(
                hostname="fritz.box",
                username="user",
                password="password",
                connection_timeout="invalid",
            )
