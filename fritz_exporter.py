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
from fritzexporter.exceptions import ConfigError, ConfigFileUnreadableError
import logging
import os
import sys
import argparse
import yaml

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY

from fritzexporter.fritzdevice import FritzCollector, FritzDevice

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARN)

def get_config():
    parser = argparse.ArgumentParser(description='Fritz Exporter for Prometheus using the TR-064 API')
    parser.add_argument('--config', type=str, help='Path to config file (./fritz-exporter.yaml)')

    args = parser.parse_args()

    # Check if config file options is set. If yes and file is not there/not readable -> error out
    # Of option is not set, check for required env vars. If required not set -> error out

    config = None
    if args.config:
        try:
            with open(args.config, 'r') as config_file:
                config = yaml.safe_load(config_file.readlines())
        except IOError as e:
            logger.critical('Config file specified but could not be read.')
            raise ConfigFileUnreadableError
    else:
        if 'FRITZ_USERNAME' and 'FRITZ_PASSWORD' in os.environ:
            hostname = os.getenv('FRITZ_HOSTNAME') if 'FRITZ_HOSTNAME' in os.environ else 'fritz.box'
            name = os.getenv('FRITZ_NAME') if 'FRITZ_NAME' in os.environ else 'Fritz!Box'
            exporter_port = os.getenv('FRITZ_PORT') if 'FRITZ_PORT' in os.environ else '9787'
            username = os.getenv('FRITZ_USERNAME')
            password = os.getenv('FRITZ_PASSWORD')
            config = {
                'exporter_port': exporter_port,
                'devices': [
                    {
                        'name': name,
                        'hostname': hostname,
                        'username': username,
                        'password': password
                    }
                ]
            }
        else:
            logger.critical('No config file specified and required env variables missing!')
            raise ConfigError('No config file specified and required env variables missing!')
    
    return config

def check_config(config):
    # Sanity check config object: exporter_port must be 1 <= exporter_port <= 65535 and there must be at least one device with hostname, username and password.
    if 'exporter_port' not in config:
        config['exporter_port'] = '9787'
    elif int(config['exporter_port']) < 1 or int(config['exporter_port']) > 65535:
        logger.critical('Invalid listening port specified: must be 1-65535')
        raise ConfigError('Invalid listening port specified: must be 1-65535')
    if 'devices' not in config or len(config['devices']) == 0:
        logger.critical('No devices found in config. Exiting.')
        raise ConfigError('No devices found in config. Exiting.')
    else:
        if 'hostname' and 'username' and 'password' not in config['devices'][0]:
            logger.critical('Device specified but either hostname, username or password are missing')
            raise ConfigError('Device specified but either hostname, username or password are missing')

def main():
    fritzcollector = FritzCollector()

    try:
        config = get_config()
        check_config(config)
    except (ConfigError, ConfigFileUnreadableError) as e:
        print(e)
        sys.exit(1)

    for dev in config['devices']:
        fritzcollector.register(FritzDevice(dev['hostname'], dev['username'], dev['password']))

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
