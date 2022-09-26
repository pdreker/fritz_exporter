import pytest

from fritzexporter.config import get_config
from fritzexporter.config import (
    ConfigError,
    EmptyConfigError,
    ConfigFileUnreadableError,
    NoDevicesFoundError,
)

from fritzexporter.config import DeviceConfig, ExporterConfig


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
            devices=[
                DeviceConfig(
                    "fritz.box", "prometheus1", "prometheus2", "Fritz!Box 7590 Router", False
                ),
                DeviceConfig(
                    "repeater-Wohnzimmer",
                    "prometheus3",
                    "prometheus4",
                    "Repeater Wohnzimmer",
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
        monkeypatch.setenv("FRITZ_PORT", "12345")
        monkeypatch.setenv("FRITZ_LOG_LEVEL", "INFO")

        config = get_config(None)
        devices: list[DeviceConfig] = [
            DeviceConfig(
                "hostname.local", "SomeUserName", "AnInterestingPassword", "My Fritz Device"
            )
        ]
        expected: ExporterConfig = ExporterConfig(12345, "INFO", devices)

        assert config == expected

    def test_minimal_env_config(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "SomeUserName")
        monkeypatch.setenv("FRITZ_PASSWORD", "AnInterestingPassword")

        config = get_config(None)
        devices: list[DeviceConfig] = [
            DeviceConfig("fritz.box", "SomeUserName", "AnInterestingPassword", "Fritz!Box")
        ]
        expected: ExporterConfig = ExporterConfig(9787, "INFO", devices)

        assert config == expected
