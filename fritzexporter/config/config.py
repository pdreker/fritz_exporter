from __future__ import annotations

import logging
import os
from typing import Optional

import yaml
from attrs import define, field, validators
from exceptions import (
    ConfigError,
    ConfigFileUnreadableError,
    EmptyConfigError,
    FritzPasswordTooLongError,
    NoDevicesFoundError,
)

logger = logging.getLogger("fritzexporter.config")


def get_config(config_file_path: Optional[str]) -> dict:
    config = {}
    if config_file_path:
        try:
            with open(config_file_path, "r") as config_file:
                config = yaml.safe_load(config_file)
        except IOError as e:
            logger.exception("Config file specified but could not be read." + str(e))
            raise ConfigFileUnreadableError(e)
        logger.info(f"Read configuration from {config_file_path}")
    else:
        if all(
            required in os.environ for required in ["FRITZ_USERNAME", "FRITZ_PASSWORD"]
        ):
            hostname = (
                os.getenv("FRITZ_HOSTNAME")
                if ("FRITZ_HOSTNAME" in os.environ)
                else "fritz.box"
            )
            name = (
                os.getenv("FRITZ_NAME") if "FRITZ_NAME" in os.environ else "Fritz!Box"
            )
            exporter_port = (
                int(os.getenv("FRITZ_PORT", "")) if "FRITZ_PORT" in os.environ else 9787
            )
            username = os.getenv("FRITZ_USERNAME")
            password = os.getenv("FRITZ_PASSWORD")
            log_level = os.getenv("FRITZ_LOG_LEVEL", "INFO")
            host_info_str: str = os.getenv("FRITZ_HOST_INFO", "False")

            if host_info_str.lower() in ["true", "1"]:
                host_info = True
            else:
                host_info = False

            config = {
                "exporter_port": exporter_port,
                "log_level": log_level,
                "devices": [
                    {
                        "name": name,
                        "hostname": hostname,
                        "username": username,
                        "password": password,
                        "host_info": host_info,
                    }
                ],
            }
            logger.info(
                "No configuration file specified: configuration read from environment"
            )

    return config


@define
class ExporterConfig:
    exporter_port: int = field(
        default=9787,
        validator=[
            validators.instance_of(int),
            validators.ge(1024),
            validators.le(65535),
        ],
    )
    log_level: str = field(default="INFO")
    devices: list[DeviceConfig] = []

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

        return ExporterConfig(
            exporter_port=exporter_port, log_level=log_level, devices=devices
        )


@define
class DeviceConfig:
    hostname: str = field(
        validator=validators.min_len(1), converter=lambda x: str.lower(x)
    )
    username: str = field(validator=validators.min_len(1))
    password: str = field(validator=validators.min_len(1))
    name: str = ""
    host_info: bool = False

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
        hostname = device.get("hostname", "")
        username = device.get("username", "")
        password = device.get("password", "")
        name = device.get("name", "")
        host_info = device.get("host_info", False)

        return DeviceConfig(
            hostname=hostname,
            username=username,
            password=password,
            name=name,
            host_info=host_info,
        )
