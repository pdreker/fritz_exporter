import logging
import yaml
import os

from .exceptions import ConfigFileUnreadableError, ConfigError, DeviceNamesNotUniqueWarning

logger = logging.getLogger("fritzexporter.config")


def get_config(config_file_path: str):
    config = None
    if config_file_path:
        try:
            with open(config_file_path, "r") as config_file:
                config = yaml.safe_load(config_file)
        except IOError as e:
            logger.exception("Config file specified but could not be read." + str(e))
            raise ConfigFileUnreadableError(e)
        logger.info(f"Read configuration from {config_file_path}")
    else:
        if all(required in os.environ for required in ["FRITZ_USERNAME", "FRITZ_PASSWORD"]):
            hostname = (
                os.getenv("FRITZ_HOSTNAME") if ("FRITZ_HOSTNAME" in os.environ) else "fritz.box"
            )
            name = os.getenv("FRITZ_NAME") if "FRITZ_NAME" in os.environ else "Fritz!Box"
            exporter_port = int(os.getenv("FRITZ_PORT", "")) if "FRITZ_PORT" in os.environ else 9787
            username = os.getenv("FRITZ_USERNAME")
            password = os.getenv("FRITZ_PASSWORD")
            log_level = os.getenv("FRITZ_LOG_LEVEL", "INFO")
            check_log_level(log_level)
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
            logger.info("No configuration file specified: configuration read from environment")
        else:
            logger.critical("No config file specified and required env variables missing!")
            raise ConfigError("No config file specified and required env variables missing!")

    for dev in config["devices"]:
        if "host_info" not in dev:
            dev["host_info"] = False

    return config


def check_log_level(log_level):
    if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        logger.critical("log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        raise ConfigError("log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")


def check_config(config: dict):  # noqa: C901
    # Sanity check config object: exporter_port must be 1 <= exporter_port <= 65535 and
    # there must be at least one device with hostname, username and password.
    if config:
        if "log_level" in config:
            check_log_level(config["log_level"])
        if "exporter_port" not in config:
            config["exporter_port"] = "9787"
        elif int(config["exporter_port"]) < 1 or int(config["exporter_port"]) > 65535:
            logger.critical("Invalid listening port specified: must be 1-65535")
            raise ConfigError("Invalid listening port specified: must be 1-65535")
        if "devices" not in config or len(config["devices"]) == 0:
            logger.critical("No devices found in config. Exiting.")
            raise ConfigError("No devices found in config. Exiting.")
        else:
            if any(
                required not in config["devices"][0]
                for required in ["hostname", "username", "password"]
            ):
                logger.critical(
                    "Device specified but either hostname, " "username or password are missing"
                )
                raise ConfigError(
                    "Device specified but either hostname, " "username or password are missing"
                )

            for index, dev in enumerate(config["devices"]):
                if "name" not in dev or dev["name"] == "":
                    logger.info(
                        f'No name specified for {dev["hostname"]} '
                        "- setting name to fritz-{index}"
                    )
                    dev["name"] = f"fritz-{index}"

            devicenames = [dev["name"] for dev in config["devices"]]
            if len(devicenames) != len(set(devicenames)):
                logger.warning("Device names are not unique")
                raise DeviceNamesNotUniqueWarning("Device names are not unique")

    else:
        logger.critical("No config found.")
        raise ConfigError("No config found.")


# Copyright 2019-2022 Patrick Dreker <patrick@dreker.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
