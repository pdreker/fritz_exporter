import argparse
import logging
import os
import sys
import threading

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY

from fritzexporter.config import DeviceConfig, ExporterError, get_config
from fritzexporter.fritzdevice import (
    DeviceSpec,
    FritzCollector,
    FritzCredentials,
)
from fritzexporter.tr064_remote import ConnectionOptions

from . import __version__

ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(levelname)8s %(name)s | %(message)s")
ch.setFormatter(formatter)

logger = logging.getLogger("fritzexporter")
logger.addHandler(ch)


def parse_cmdline() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Fritz Exporter for Prometheus using the TR-064 API (v{__version__})"
    )
    parser.add_argument("--config", type=str, help="Path to config file")
    levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    parser.add_argument(
        "--log-level",
        choices=levels,
        help="Set log-level (default: INFO)",
    )

    parser.add_argument(
        "--version", action="store_const", const="version", help="Print version number and exit."
    )

    return parser.parse_args()


def _register_device(dev: DeviceConfig, fritzcollector: FritzCollector) -> None:
    spec = DeviceSpec(
        FritzCredentials(dev.hostname, dev.username, dev.read_password()),
        dev.name,
        host_info=dev.host_info,
        wifi_client_info=dev.wifi_client_info,
        connection=ConnectionOptions(
            connection_timeout=dev.connection_timeout,
            use_tls=dev.use_tls,
            port=dev.port,
            remote_access=dev.remote_access,
            allow_unsafe_md5_password=dev.allow_unsafe_md5_password,
        ),
    )
    fritzcollector.add_device(spec)


def main() -> None:
    fritzcollector = FritzCollector()

    args = parse_cmdline()

    if args.version:
        print(__version__)  # noqa: T201
        sys.exit(0)

    try:
        config = get_config(args.config)
    except ExporterError as e:
        # The message says what and where; a traceback would only hide it.
        logger.critical("Invalid configuration: %s", e)
        sys.exit(1)

    # Only the exporter's own loggers have a handler (see above); others log
    # warnings and worse through Python's fallback.
    logger.setLevel(args.log_level or config.log_level)
    logger.info("Configuration read from %s", args.config or "the environment")

    for dev in config.devices:
        _register_device(dev, fritzcollector)

    REGISTRY.register(fritzcollector)

    logger.info("Starting listener at %s:%d", config.listen_address, config.exporter_port)
    start_http_server(int(config.exporter_port), str(config.listen_address))

    logger.info("Exporter is ready")

    # Avoid blocking forever when running tests
    if not os.getenv("FRITZ_EXPORTER_UNDER_TEST"):
        threading.Event().wait()


if __name__ == "__main__":
    main()

# Copyright 2019-2026 Patrick Dreker <patrick@dreker.de>
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
