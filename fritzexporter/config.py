import logging
import yaml
import os

from .exceptions import ConfigFileUnreadableError, ConfigError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARN)

def get_config(config_file_path):
    config = None
    if config_file_path:
        try:
            with open(config_file_path, 'r') as config_file:
                config = yaml.safe_load(config_file)
        except IOError as e:
            logger.critical('Config file specified but could not be read.')
            raise ConfigFileUnreadableError
    else:
        if all(required in os.environ for required in ['FRITZ_USERNAME', 'FRITZ_PASSWORD']):
            hostname = os.getenv('FRITZ_HOSTNAME') if 'FRITZ_HOSTNAME' in os.environ else 'fritz.box'
            name = os.getenv('FRITZ_NAME') if 'FRITZ_NAME' in os.environ else 'Fritz!Box'
            exporter_port = int(os.getenv('FRITZ_PORT')) if 'FRITZ_PORT' in os.environ else 9787
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
    if config:
        if 'exporter_port' not in config:
            config['exporter_port'] = '9787'
        elif int(config['exporter_port']) < 1 or int(config['exporter_port']) > 65535:
            logger.critical('Invalid listening port specified: must be 1-65535')
            raise ConfigError('Invalid listening port specified: must be 1-65535')
        if 'devices' not in config or len(config['devices']) == 0:
            logger.critical('No devices found in config. Exiting.')
            raise ConfigError('No devices found in config. Exiting.')
        else:
            if any(required not in config['devices'][0] for required in ['hostname', 'username', 'password']):
                logger.critical('Device specified but either hostname, username or password are missing')
                raise ConfigError('Device specified but either hostname, username or password are missing')
    else:
        logger.critical('No config found.')
        raise ConfigError('No config found.')
