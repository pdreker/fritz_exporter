from __future__ import annotations

import difflib
import ipaddress
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import attrs
import yaml
from attrs import Converter, converters, define, field, validators

from .exceptions import (
    ConfigError,
    ConfigFileUnreadableError,
    DeviceNamesNotUniqueError,
    EmptyConfigError,
    FritzPasswordFileDoesNotExistError,
    FritzPasswordMissingError,
    FritzPasswordTooLongError,
    FritzRemoteAccessRequiresTlsError,
    InvalidConfigValueError,
    NoDevicesFoundError,
)

logger = logging.getLogger("fritzexporter.config")

# Default per-call TR-064 connect/read timeout in seconds. A finite default keeps
# a wedged device from blocking a scrape (and the collector lock) indefinitely;
# set 0 explicitly to disable the timeout.
DEFAULT_CONNECTION_TIMEOUT: int = 10

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Fritz!OS cuts longer passwords when they are set, so a longer one never matches.
FRITZ_MAX_PASSWORD_LENGTH = 32


def _text(value: object) -> str:
    """A text value; YAML numbers are taken as written, other types refused."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        msg = f"expected text, got {type(value).__name__} (put the value in quotes)"
        raise TypeError(msg)
    return str(value)


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _zero_is_none(value: int | str | None) -> int | None:
    """An int, where ``0`` (or ``None``) means "not set"."""
    if value is None:
        return None
    number = int(value)
    return None if number == 0 else number


def _named(convert: Callable[[Any], Any]) -> Any:  # noqa: ANN401
    """A converter whose errors name the config key.

    Typed ``Any``: config values arrive as text (env) or YAML scalars.
    """

    def wrapped(value: object, _self: object, attribute: attrs.Attribute) -> Any:  # noqa: ANN401
        try:
            return convert(value)
        except (ValueError, TypeError) as e:
            msg = f"{attribute.name}: {e}"
            raise InvalidConfigValueError(msg) from None

    return Converter(wrapped, takes_self=True, takes_field=True)


def _read_password_file(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError as e:
        msg = f"password_file {path} cannot be read: {e.strerror}"
        raise FritzPasswordFileDoesNotExistError(msg) from None


def _ip_address(_: object, attribute: attrs.Attribute, value: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError as e:
        msg = f"{attribute.name}: {e}"
        raise InvalidConfigValueError(msg) from None


def _read_config_file(config_file_path: str) -> dict:
    try:
        with Path(config_file_path).open() as config_file:
            config = yaml.safe_load(config_file)
    except OSError as e:
        msg = f"config file {config_file_path} could not be read: {e.strerror}"
        raise ConfigFileUnreadableError(msg) from None
    except yaml.YAMLError as e:
        # Only the position: the parser's texts quote values (a tag, an alias),
        # which may be a password.
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        msg = f"config file {config_file_path} is not valid YAML{where}"
        raise ConfigFileUnreadableError(msg) from None
    logger.info("Read configuration from %s.", config_file_path)
    return config


# Environment variable for each key; the env path describes one device.
_EXPORTER_ENV = {
    "exporter_port": "FRITZ_PORT",
    "log_level": "FRITZ_LOG_LEVEL",
    "listen_address": "FRITZ_LISTEN_ADDRESS",
}
_DEVICE_ENV = {
    "hostname": "FRITZ_HOSTNAME",
    "name": "FRITZ_NAME",
    "username": "FRITZ_USERNAME",
    "password": "FRITZ_PASSWORD",
    "password_file": "FRITZ_PASSWORD_FILE",
    "host_info": "FRITZ_HOST_INFO",
    "wifi_client_info": "FRITZ_WIFI_CLIENT_INFO",
    "connection_timeout": "FRITZ_CONNECTION_TIMEOUT",
    "use_tls": "FRITZ_USE_TLS",
    "port": "FRITZ_DEVICE_PORT",
    "remote_access": "FRITZ_REMOTE_ACCESS",
    "allow_unsafe_md5_password": "FRITZ_ALLOW_UNSAFE_MD5_PASSWORD",
}


def _read_config_from_env() -> dict:
    if "FRITZ_USERNAME" not in os.environ or all(
        required not in os.environ for required in ["FRITZ_PASSWORD", "FRITZ_PASSWORD_FILE"]
    ):
        msg = (
            "Required env variables missing "
            "(FRITZ_USERNAME, FRITZ_PASSWORD or FRITZ_PASSWORD_FILE)!"
        )
        raise ConfigError(msg)

    def present(names: dict[str, str]) -> dict[str, str]:
        return {key: os.environ[env] for key, env in names.items() if env in os.environ}

    logger.info("No configuration file specified: configuration read from environment")
    return {**present(_EXPORTER_ENV), "devices": [present(_DEVICE_ENV)]}


def get_config(config_file_path: str | None) -> ExporterConfig:
    config = _read_config_file(config_file_path) if config_file_path else _read_config_from_env()
    return ExporterConfig.from_config(config)


def _check_keys(config: dict, cls: type, where: str) -> None:
    """Refuse keys the config class does not have: a typo would go unnoticed."""
    known = [a.name for a in attrs.fields(cls)]
    for key in config:
        if key not in known:
            close = difflib.get_close_matches(str(key), known, n=1)
            hint = f", did you mean '{close[0]}'?" if close else ""
            msg = f"{where}: unknown key '{key}'{hint}"
            raise ConfigError(msg)


@define
class ExporterConfig:
    exporter_port: int = field(
        default=9787,
        validator=[validators.ge(1024), validators.le(65535)],
        converter=_named(int),
    )
    log_level: str = field(default="INFO", validator=validators.in_(LOG_LEVELS))
    devices: list[DeviceConfig] = field(factory=list)
    listen_address: str = field(default="127.0.0.1", validator=_ip_address)

    @devices.validator  # ty: ignore[unresolved-attribute]
    def check_devices(self, _: attrs.Attribute, value: list[DeviceConfig]) -> None:
        if not value:
            msg = "No devices found in config."
            raise NoDevicesFoundError(msg)
        names = [dev.name for dev in value]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            # The name is a label of every series; two devices with one name are
            # told apart by serial only, and not at all while they are offline.
            msg = (
                f"device names must be unique, used more than once: {', '.join(duplicates)} "
                "(the name defaults to the hostname)"
            )
            raise DeviceNamesNotUniqueError(msg)

    @classmethod
    def from_config(cls, config: object) -> ExporterConfig:
        if config is None:
            msg = "No config found (check Env vars or config file)."
            raise EmptyConfigError(msg)
        if not isinstance(config, dict):
            msg = "config: expected keys and values at the top level"
            raise ConfigError(msg)
        _check_keys(config, cls, "config")
        devices = config.get("devices") or []
        if not isinstance(devices, list):
            msg = "config devices: expected a list of devices"
            raise ConfigError(msg)
        settings = {key: value for key, value in config.items() if key != "devices"}

        device_configs = [
            DeviceConfig.from_config(device, f"device {index + 1}")
            for index, device in enumerate(devices)
        ]
        try:
            return cls(devices=device_configs, **settings)
        except (ValueError, TypeError) as e:
            msg = f"config {e}"
            raise InvalidConfigValueError(msg) from None


@define
class DeviceConfig:
    hostname: str = field(
        default="fritz.box",
        validator=validators.min_len(1),
        converter=_named(lambda value: _text(value).lower()),
    )
    username: str = field(default="", validator=validators.min_len(1), converter=_named(_text))
    password: str | None = field(default=None, repr=False, converter=_named(_optional_text))
    password_file: str | None = field(default=None, converter=_named(_optional_text))
    # Defaults to the hostname.
    name: str = field(default="", converter=_named(_text))
    host_info: bool = field(default=False, converter=_named(converters.to_bool))
    wifi_client_info: bool = field(default=False, converter=_named(converters.to_bool))
    connection_timeout: int | None = field(
        default=DEFAULT_CONNECTION_TIMEOUT,
        converter=_named(_zero_is_none),
        validator=validators.optional(validators.ge(1)),
    )
    use_tls: bool = field(default=False, converter=_named(converters.to_bool))
    port: int | None = field(
        default=None,
        converter=_named(_zero_is_none),
        validator=validators.optional(
            validators.and_(validators.ge(1), validators.le(65535)),
        ),
    )
    remote_access: bool = field(default=False, converter=_named(converters.to_bool))
    allow_unsafe_md5_password: bool = field(default=False, converter=_named(converters.to_bool))

    def __attrs_post_init__(self) -> None:
        if not self.name:
            self.name = self.hostname
        if self.password is None and self.password_file is None:
            raise FritzPasswordMissingError

    @password.validator  # ty: ignore[unresolved-attribute]
    def check_password(self, _: attrs.Attribute, value: str | None) -> None:
        if value is not None and len(value) > FRITZ_MAX_PASSWORD_LENGTH:
            raise FritzPasswordTooLongError

    @password_file.validator  # ty: ignore[unresolved-attribute]
    def check_password_file(self, attribute: attrs.Attribute, value: str | None) -> None:
        # Read now, so an unreadable file (e.g. not readable by the container's
        # user) stops the start with a message instead of a traceback later.
        if value is not None:
            self.check_password(attribute, _read_password_file(value))

    def read_password(self) -> str:
        """The password, from ``password`` or from ``password_file``."""
        if self.password_file is not None:
            return _read_password_file(self.password_file)
        return self.password or ""

    @remote_access.validator  # ty: ignore[unresolved-attribute]
    def check_remote_access(self, _: attrs.Attribute, value: bool) -> None:  # noqa: FBT001
        if value and not self.use_tls:
            raise FritzRemoteAccessRequiresTlsError

    @classmethod
    def from_config(cls, device: object, where: str = "device") -> DeviceConfig:
        """A device from its config keys; keys left out take the field defaults."""
        if not isinstance(device, dict):
            msg = f"{where}: expected keys and values"
            raise ConfigError(msg)
        hostname = device.get("hostname")
        if isinstance(hostname, str) and hostname:
            where = f"{where} ({hostname})"
        _check_keys(device, cls, where)
        try:
            return cls(**device)
        except ConfigError as e:
            msg = f"{where}: {e}"
            raise type(e)(msg) from None
        except (ValueError, TypeError) as e:
            msg = f"{where}: {e}"
            raise InvalidConfigValueError(msg) from None
