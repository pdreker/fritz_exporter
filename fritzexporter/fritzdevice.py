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

from fritzexporter.fritzcapabilities import FritzCapabilities, FritzCapability
import logging
import sys

from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import FritzActionError, FritzServiceError
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from requests.exceptions import ConnectionError

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class FritzDevice():

    def __init__(self, host, user, password) -> None:
        self.host = host
        self.serial = "n/a"
        self.model = "n/a"

        try:
            self.fc = FritzConnection(address=host, user=user, password=password)
        except ConnectionError as e:
            logger.critical(f'unable to connect to {host}: {str(e)}', exc_info=True)
            sys.exit(1)

        self.capabilities = FritzCapabilities(self)

        self.getDeviceInfo()

        if self.capabilities.empty():
            logger.critical(f'Device {host} has no detected capabilities. Exiting. ')
            sys.exit(1)

    def getDeviceInfo(self):            
        try:
            device_info = self.fc.call_action('DeviceInfo', 'GetInfo')
            self.serial = device_info['NewSerialNumber']
            self.model = device_info['NewModelName']

        except (FritzServiceError, FritzActionError):
            logger.error(f'Fritz Device {self.host} does not provide basic device info (Service: DeviceInfo1, Action: GetInfo). serial number and model name will be unavailable.', exc_info=True)

class FritzCollector(object):
    def __init__(self):
        self.devices = []
        self.capabilities = FritzCapabilities()

    def register(self, fritzdev):
        self.devices.append(fritzdev)
        logger.debug(f'registered device {fritzdev.host} ({fritzdev.model}) to collector')
        self.capabilities.merge(fritzdev.capabilities)

    def collect(self):        
        if not self.devices:
            logger.critical(f'No devices registered in collector! Exiting.')
            sys.exit(1)
        
        if self.capabilities["HAS_DEVICE_INFO"]:
            fritz_uptime = CounterMetricFamily('fritz_uptime', 'FritzBox uptime, system info in labels', labels=['modelname', 'softwaresersion', 'serial'])
            for dev in self.devices:
                if dev.capabilities["HAS_DEVICE_INFO"]:
                    info_result = dev.fc.call_action('DeviceInfo:1', 'GetInfo')
                    fritz_uptime.add_metric([info_result['NewModelName'], info_result['NewSoftwareVersion'], info_result['NewSerialNumber']], info_result['NewUpTime'])
            yield fritz_uptime

        if self.capabilities["HAS_USER_INTERFACE"]:
            fritz_update = GaugeMetricFamily('fritz_update_available', 'FritzBox update available', labels=['serial', 'newsoftwareversion'])
            for dev in self.devices:
                if dev.capabilities["HAS_USER_INTERFACE"]:
                    update_result = dev.fc.call_action('UserInterface:1', 'GetInfo')
                    upd_available = 1 if update_result['NewUpgradeAvailable'] == '1' else 0
                    new_software_version = "n/a" if update_result['NewX_AVM-DE_Version'] is None else update_result['NewX_AVM-DE_Version']
                    fritz_update.add_metric([dev.serial, new_software_version], upd_available)
            yield fritz_update

        # LAN Config Info
        if self.capabilities["HAS_LAN_CONFIG_INFO"]:
            fritz_lanenable = GaugeMetricFamily('fritz_lan_status_enabled', 'LAN Interface enabled', labels=['serial'])
            fritz_lanstatus = GaugeMetricFamily('fritz_lan_status', 'LAN Interface status', labels=['serial'])
            for dev in self.devices:
                if dev.capabilities["HAS_LAN_CONFIG_INFO"]:
                    lanstatus_result = dev.fc.call_action('LANEthernetInterfaceConfig:1', 'GetInfo')
                    fritz_lanenable.add_metric([dev.serial], lanstatus_result['NewEnable'])

                    lanstatus = 1 if lanstatus_result['NewStatus'] == 'Up' else 0
                    fritz_lanstatus.add_metric([dev.serial], lanstatus)
            yield fritz_lanenable
            yield fritz_lanstatus

        # LAN Config Statistics
        if self.capabilities["HAS_LAN_CONFIG_STATS"]:
            fritz_lan_bytes = CounterMetricFamily('fritz_lan_data_bytes', 'LAN bytes received', labels=['serial', 'direction'])
            fritz_lan_packets = CounterMetricFamily('fritz_lan_packets_total', 'LAN packets transmitted', labels=['serial', 'direction'])
            for dev in self.devices:
                if dev.capabilities["HAS_LAN_CONFIG_STATS"]:
                    lanstats_result = dev.fc.call_action('LANEthernetInterfaceConfig:1', 'GetStatistics')
                    fritz_lan_bytes.add_metric([dev.serial, 'rx'], lanstats_result['NewBytesReceived'])
                    fritz_lan_bytes.add_metric([dev.serial, 'tx'], lanstats_result['NewBytesSent'])
                    fritz_lan_packets.add_metric([dev.serial, 'rx'], lanstats_result['NewPacketsReceived'])
                    fritz_lan_packets.add_metric([dev.serial], 'tx', lanstats_result['NewPacketsSent'])
            yield fritz_lan_bytes
            yield fritz_lan_packets

        # WAN DSL Config Info
        if self.capabilities["HAS_WAN_DSL_INTERFACE_CONFIG"]:
            fritz_dsl_enable = GaugeMetricFamily('fritz_dsl_status_enabled', 'DSL enabled', labels=['serial'])
            fritz_dsl_datarate = GaugeMetricFamily('fritz_dsl_datarate_kbps', 'DSL datarate in kbps', labels= ['serial', 'direction', 'type'])
            fritz_dsl_noisemargin = GaugeMetricFamily('fritz_dsl_noise_margin_dB', 'Noise Margin in dB', labels=['serial', 'direction'])
            fritz_dsl_attenuation = GaugeMetricFamily('fritz_dsl_attenuation_dB', 'Line attenuation in dB', labels=['serial', 'direction'])
            fritz_dsl_status = GaugeMetricFamily('fritz_dsl_status', 'DSL status', labels=['serial'])
            for dev in self.devices:
                if dev.capabilities["HAS_WAN_DSL_INTERFACE_CONFIG"]:
                    fritz_dslinfo_result = dev.fc.call_action('WANDSLInterfaceConfig:1', 'GetInfo')
                    fritz_dsl_enable.add_metric([dev.serial], fritz_dslinfo_result['NewEnable'])
                    
                    dslstatus = 1 if fritz_dslinfo_result['NewStatus'] == 'Up' else 0
                    fritz_dsl_status.add_metric([dev.serial], dslstatus)

                    fritz_dsl_datarate.add_metric([dev.serial, 'tx', 'curr'], fritz_dslinfo_result['NewUpstreamCurrRate'])
                    fritz_dsl_datarate.add_metric([dev.serial, 'rx','curr'], fritz_dslinfo_result['NewDownstreamCurrRate'])
                    fritz_dsl_datarate.add_metric([dev.serial, 'tx', 'max'], fritz_dslinfo_result['NewUpstreamMaxRate'])
                    fritz_dsl_datarate.add_metric([dev.serial, 'rx','max'], fritz_dslinfo_result['NewDownstreamMaxRate'])
                    fritz_dsl_noisemargin.add_metric([dev.serial, 'tx'], fritz_dslinfo_result['NewUpstreamNoiseMargin']/10)
                    fritz_dsl_noisemargin.add_metric([dev.serial, 'rx'], fritz_dslinfo_result['NewDownstreamNoiseMargin']/10)
                    fritz_dsl_attenuation.add_metric([dev.serial, 'tx'], fritz_dslinfo_result['NewUpstreamAttenuation']/10)
                    fritz_dsl_attenuation.add_metric([dev.serial, 'rx'], fritz_dslinfo_result['NewDownstreamAttenuation']/10)

            yield fritz_dsl_enable
            yield fritz_dsl_status
            yield fritz_dsl_datarate
            yield fritz_dsl_noisemargin
            yield fritz_dsl_attenuation


        # WAN PPP Connection StatusInfo
        if self.capabilities["HAS_WAN_PPP_STATUS_INFO"]:
            fritz_ppp_uptime = GaugeMetricFamily('fritz_ppp_connection_uptime', 'PPP connection uptime', labels=['serial'])
            fritz_ppp_connected = GaugeMetricFamily('fritz_ppp_conection_state', 'PPP connection state', labels=['serial', 'last_error'])
            for dev in self.devices:
                if dev.capabilities["HAS_WAN_PPP_STATUS_INFO"]:
                    fritz_pppstatus_result = dev.fc.call_action('WANPPPConnection:1', 'GetStatusInfo')
                    pppconnected = 1 if fritz_pppstatus_result['NewConnectionStatus'] == 'Connected' else 0
                    fritz_ppp_uptime.add_metric([dev.serial], fritz_pppstatus_result['NewUptime'])
                    fritz_ppp_connected.add_metric([dev.serial, fritz_pppstatus_result['NewLastConnectionError']], pppconnected)
            yield fritz_ppp_uptime
            yield fritz_ppp_connected

        if self.capabilities["HAS_WAN_COMMON_INTERFACE_BYTES"]:
            fritz_wan_data = CounterMetricFamily('fritz_wan_data_bytes', 'WAN data in bytes', labels=['serial', 'direction'])
            for dev in self.devices:
                if dev.capabilities["HAS_WAN_COMMON_INTERFACE_BYTES"]:
                    fritz_wan_result = dev.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalBytesReceived')
                    wan_bytes_rx = fritz_wan_result['NewTotalBytesReceived']
                    fritz_wan_result = dev.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalBytesSent')
                    wan_bytes_tx = fritz_wan_result['NewTotalBytesSent']
                    fritz_wan_data.add_metric([dev.serial, 'up'], wan_bytes_tx)
                    fritz_wan_data.add_metric([dev.serial, 'down'], wan_bytes_rx)
            yield fritz_wan_data

        if self.capabilities["HAS_WAN_COMMON_INTERFACE_PACKETS"]:
            fritz_wan_packets = CounterMetricFamily('fritz_wan_data_packets', 'WAN data in packets', labels=['serial', 'direction'])
            for dev in self.devices:
                if dev.capabilities["HAS_WAN_COMMON_INTERFACE_PACKETS"]:
                    fritz_wan_result = dev.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsReceived')
                    wan_packets_rx = fritz_wan_result['NewTotalPacketsReceived']
                    fritz_wan_result = dev.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsSent')
                    wan_packets_tx = fritz_wan_result['NewTotalPacketsSent']
                    fritz_wan_packets.add_metric([dev.serial, 'up'], wan_packets_tx)
                    fritz_wan_packets.add_metric([dev.serial, 'down'], wan_packets_rx)
            yield fritz_wan_packets
