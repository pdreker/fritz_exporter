import argparse
import asyncio
import logging
import sys

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY

from fritzexporter.config import ExporterException, get_config
from fritzexporter.data_donation import donate_data
from fritzexporter.fritzdevice import FritzCollector, FritzDevice

ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s %(levelname)8s %(name)s | %(message)s")
ch.setFormatter(formatter)

logger = logging.getLogger("fritzexporter")
logger.addHandler(ch)


def main():
    fritzcollector = FritzCollector()

    parser = argparse.ArgumentParser(
        description="Fritz Exporter for Prometheus using the TR-064 API"
    )
    parser.add_argument("--config", type=str, help="Path to config file")
    levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    parser.add_argument(
        "--log-level",
        choices=levels,
        help="Set log-level (default: INFO)",
    )

    parser.add_argument(
        "--donate-data",
        action="store_const",
        const="donate",
        help="Do not start exporter, collect and print data to assist the project",
    )

    parser.add_argument(
        "--upload-data",
        action="store_const",
        const="upload",
        help="Instead of displaying the collected data donation, upload it.",
    )

    parser.add_argument(
        "-s",
        "--sanitize",
        action="append",
        nargs="+",
        metavar="FIELD_SPEC",
        help="Sanitize 'service, action, field' from the data donation output",
    )

    args = parser.parse_args()

    try:
        config = get_config(args.config)
    except ExporterException as e:
        logger.exception(e)
        sys.exit(1)

    log_level = (
        getattr(logging, args.log_level) if args.log_level else getattr(logging, config.log_level)
    )
    if not log_level:
        log_level = "INFO"

    loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict]
    for log in loggers:
        log.setLevel(log_level)

    for dev in config.devices:
        fritz_device = FritzDevice(
            dev.hostname,
            dev.username,
            dev.password,
            dev.name,
            dev.host_info,
        )

        if args.donate_data == "donate":
            donate_data(
                fritz_device,
                upload=True if args.upload_data == "upload" else False,
                sanitation=args.sanitize,
            )
            sys.exit(0)
        else:
            logger.info(f"registering {dev.hostname} to collector")
            fritzcollector.register(fritz_device)

    REGISTRY.register(fritzcollector)

    logger.info(f"Starting listener at {config.exporter_port}")
    start_http_server(int(config.exporter_port))

    logger.info("Entering async main loop - exporter is ready")
    loop = asyncio.new_event_loop()
    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == "__main__":
    main()

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
