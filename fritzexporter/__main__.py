# Copyright 2019-2021 Patrick Dreker <patrick@dreker.de>
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

import asyncio
import logging
import sys
import argparse

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY

from fritzexporter.fritzdevice import FritzCollector, FritzDevice
from fritzexporter.exceptions import ConfigError, ConfigFileUnreadableError, DeviceNamesNotUniqueWarning
from .config import get_config, check_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARN)


def main():
    fritzcollector = FritzCollector()

    parser = argparse.ArgumentParser(description='Fritz Exporter for Prometheus using the TR-064 API')
    parser.add_argument('--config', type=str, help='Path to config file')

    args = parser.parse_args()

    try:
        config = get_config(args.config)
        check_config(config)
    except (ConfigError, ConfigFileUnreadableError) as e:
        print(e)
        sys.exit(1)
    except DeviceNamesNotUniqueWarning:
        pass

    for dev in config['devices']:
        fritzcollector.register(FritzDevice(dev['hostname'], dev['username'], dev['password'], dev['name']))

    REGISTRY.register(fritzcollector)

    logger.info(f'Starting listener at {config["exporter_port"]}')
    start_http_server(int(config["exporter_port"]))

    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == '__main__':
    main()
