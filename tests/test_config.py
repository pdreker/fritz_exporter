from _pytest.monkeypatch import monkeypatch
import pytest

from pytest import MonkeyPatch

from fritzexporter.config import get_config, check_config
from fritzexporter.exceptions import ConfigError, ConfigFileUnreadableError

class TestReadConfig:
    def test_file_not_found(self):
        testfile = 'this/does/not/exist'

        with pytest.raises(ConfigFileUnreadableError):
            get_config(testfile)

    def test_no_config(self, monkeypatch):
        testfile = None

        monkeypatch.delenv('FRITZ_HOSTNAME', raising=False)
        monkeypatch.delenv('FRITZ_USERNAME', raising=False)
        monkeypatch.delenv('FRITZ_PASSWORD', raising=False)
        monkeypatch.delenv('FRITZ_NAME', raising=False)
        with pytest.raises(ConfigError):
            get_config(testfile)

class TestFileConfigs:
    def test_empty_file(self):
        testfile = 'tests/conffiles/empty.yaml'
        config = get_config(testfile)

        with pytest.raises(ConfigError):
            check_config(config)

    def test_empty_devices(self):
        testfile = 'tests/conffiles/emptydevices.yaml'
        config = get_config(testfile)
        
        with pytest.raises(ConfigError):
            check_config(config)

    def test_malformed_device(self):
        testfile = 'tests/conffiles/malformeddevice.yaml'
        config = get_config(testfile)

        with pytest.raises(ConfigError):
            check_config(config)

    def test_nodevices(self):
        testfile = 'tests/conffiles/nodevices.yaml'
        config = get_config(testfile)

        with pytest.raises(ConfigError):
            check_config(config)

    def test_invalidport(self):
        testfile = 'tests/conffiles/invalidport.yaml'
        config = get_config(testfile)

        with pytest.raises(ConfigError):
            check_config(config)

    def test_valid_file(self):
        testfile = 'tests/conffiles/validconfig.yaml'

        expected = {
            'exporter_port': 9787,
            'devices': [
                {
                    'name': 'Fritz!Box 7590 Router',
                    'hostname': 'fritz.box',
                    'username': 'prometheus1',
                    'password': 'prometheus2'
                },
                {
                    'name': 'Repeater Wohnzimmer',
                    'hostname': 'repeater-Wohnzimmer',
                    'username': 'prometheus3',
                    'password': 'prometheus4'
                }
            ]
        }

        config = get_config(testfile)
        assert config == expected

        check_config(config)

class TestEnvConfig:
    def test_env_config(self, monkeypatch):
        monkeypatch.setenv('FRITZ_HOSTNAME', 'hostname.local')
        monkeypatch.setenv('FRITZ_USERNAME', 'SomeUserName')
        monkeypatch.setenv('FRITZ_PASSWORD', 'AnInterestingPassword')
        monkeypatch.setenv('FRITZ_NAME', 'My Fritz Device')
        monkeypatch.setenv('FRITZ_PORT', '12345')

        config = get_config(None)
        expected = {
            'exporter_port': 12345,
            'devices': [
                {
                    'name': 'My Fritz Device',
                    'hostname': 'hostname.local',
                    'username': 'SomeUserName',
                    'password': 'AnInterestingPassword'
                },
            ]
        }

        assert config == expected

    def test_minimal_env_config(self, monkeypatch):
        monkeypatch.setenv('FRITZ_USERNAME', 'SomeUserName')
        monkeypatch.setenv('FRITZ_PASSWORD', 'AnInterestingPassword')

        config = get_config(None)
        expected = {
            'exporter_port': 9787,
            'devices': [
                {
                    'name': 'Fritz!Box',
                    'hostname': 'fritz.box',
                    'username': 'SomeUserName',
                    'password': 'AnInterestingPassword'
                },
            ]
        }

        assert config == expected
    
