from __future__ import annotations

import logging
import os
from typing import Any, Optional

import yaml
from attrs import converters, define, field, validators

from .exceptions import (
    ConfigError,
    ConfigFileUnreadableError,
    EmptyConfigError,
    FritzPasswordTooLongError,
    NoDevicesFoundError,
)

logger = logging.getLogger("fritzexporter.config")


def _read_config_file(config_file_path: str) -> dict:
    try:
        with open(config_file_path, "r") as config_file:
            config = yaml.safe_load(config_file)
    except IOError as e:
        logger.exception("Config file specified but could not be read." + str(e))
        raise ConfigFileUnreadableError(e)
    logger.info(f"Read configuration from {config_file_path}")

    return config


def _read_config_from_env() -> dict:
    if not all(
        required in os.environ for required in ["FRITZ_USERNAME", "FRITZ_PASSWORD"]
    ):
        logger.critical(
            "Required env variables missing (FRITZ_USERNAME, FRITZ_PASSWORD)!"
        )
        raise ConfigError(
            "Required env variables missing (FRITZ_USERNAME, FRITZ_PASSWORD)!"
        )

    exporter_port = os.getenv("FRITZ_PORT")
    log_level = os.getenv("FRITZ_LOG_LEVEL")

    hostname = os.getenv("FRITZ_HOSTNAME")
    name: str = os.getenv("FRITZ_NAME", "Fritz!Box")
    username = os.getenv("FRITZ_USERNAME")
    password = os.getenv("FRITZ_PASSWORD")
    host_info: str = os.getenv("FRITZ_HOST_INFO", "False")

    config: dict[Any, Any] = {}
    if exporter_port:
        config["exporter_port"] = exporter_port
    if log_level:
        config["log_level"] = log_level

    config["devices"] = []
    device = {
        "username": username,
        "password": password,
        "host_info": host_info,
        "name": name,
    }
    if hostname:
        device["hostname"] = hostname
    config["devices"].append(device)

    logger.info("No configuration file specified: configuration read from environment")

    return config


def get_config(config_file_path: Optional[str]) -> ExporterConfig:
    config = {}
    if config_file_path:
        config = _read_config_file(config_file_path)
    else:
        config = _read_config_from_env()
    return ExporterConfig.from_config(config)


@define
class ExporterConfig:
    exporter_port: int = field(
        default=9787,
        validator=[
            validators.instance_of(int),
            validators.ge(1024),
            validators.le(65535),
        ],
        converter=int,
    )
    log_level: str = field(default="INFO")
    devices: list[DeviceConfig] = field(factory=list)

    @log_level.validator
    def check_log_level(self, attribute, value):
        if value not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            logger.critical(
                "log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL"
            )
            raise ConfigError(
                "log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL"
            )

    @devices.validator
    def check_devices(self, attribute, value):
        if value is None or (isinstance(value, list) and len(value) == 0):
            logger.exception("No devices found in config.")
            raise NoDevicesFoundError("No devices found in config.")
        devicenames = [dev.name for dev in value]
        if len(devicenames) != len(set(devicenames)):
            logger.warning("Device names are not unique")

    @classmethod
    def from_config(cls, config) -> ExporterConfig:
        if config is None:
            logger.exception("No config found (check Env vars or config file).")
            raise EmptyConfigError(
                "Reading config file returned empty config. Check file content."
            )

        exporter_port = config.get("exporter_port", 9787)
        log_level = config.get("log_level", "INFO")
        devices: list[DeviceConfig] = [
            DeviceConfig.from_config(dev) for dev in config.get("devices", [])
        ]

        return cls(exporter_port=exporter_port, log_level=log_level, devices=devices)


@define
class DeviceConfig:
    hostname: str = field(
        validator=validators.min_len(1), converter=lambda x: str.lower(x)
    )
    username: str = field(validator=validators.min_len(1))
    password: str = field(validator=validators.min_len(1))
    name: str = ""
    host_info: bool = field(default=False, converter=converters.to_bool)

    @password.validator
    def check_password(self, attr, val):
        if len(val) > 32:
            logger.exception(
                "Password is longer than 32 characters! "
                "Login may not succeed, please see documentation!"
            )
            raise FritzPasswordTooLongError(
                "Password is longer than 32 characters! "
                "Login may not succeed, please see documentation!"
            )

    @classmethod
    def from_config(cls, device) -> DeviceConfig:
        hostname = device.get("hostname", "fritz.box")
        username = device.get("username", "")
        password = device.get("password", "")
        name = device.get("name", "")
        host_info = device.get("host_info", False)

        return cls(
            hostname=hostname,
            username=username,
            password=password,
            name=name,
            host_info=host_info,
        )
