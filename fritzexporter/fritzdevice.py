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
        self.capabilities = {
            "HAS_DEVICE_INFO": False,
            "HAS_HOST_NUMBER_OF_ENTRIES": False,
            "HAS_HOST_GET_GENERIC_ENTRY": False,
            "HAS_USER_INTERFACE": False,
            "HAS_LAN_CONFIG_INFO": False,
            "HAS_LAN_CONFIG_STATS": False,
            "HAS_WAN_DSL_INTERFACE_CONFIG": False,
            "HAS_WAN_PPP_STATUS_INFO": False,
            "HAS_WAN_COMMON_INTERFACE_LINK_PROPERTIES": False,
            "HAS_WAN_COMMON_INTERFACE_BYTES": False,
            "HAS_WAN_COMMON_INTERFACE_PACKETS": False,
        }

        try:
            self.fc = FritzConnection(address=host, user=user, password=password)
        except ConnectionError as e:
            logger.critical(f'unable to connect to {host}: {str(e)}', exc_info=True)
            sys.exit(1)
        
        self.getDeviceInfo()
        self.getCapabilities()

        if not any(self.capabilities):
            logger.critical(f'Device {host} has no detected capabilities. Exiting. ')
            sys.exit(1)


    def getDeviceInfo(self):            
        try:
            device_info = self.fc.call_action('DeviceInfo', 'GetInfo')
            self.serial = device_info['NewserialNumber']
            self.model = device_info['NewModelName']

        except (FritzServiceError, FritzActionError):
            logger.error(f'Fritz Device {self.host} does not provide basic device info (Service: DeviceInfo1, Action: GetInfo). serial number and model name will be unavailable.', exc_info=True)

    def getCapabilities(self):
        if self.checkServiceAction('DeviceInfo1'):
            self.capabilities["HAS_DEVICE_INFO"] = True
            logger.debug(f'Device {self.host} detected HAS_DEVICE_INFO')
        if self.checkServiceAction('Hosts1', 'GetHostNumberOfEntries'):
            self.capabilities["HAS_HOST_NUMBER_OF_ENTRIES"] = True
            logger.debug(f'Device {self.host} detected HAS_HOST_NUMBER_OF_ENTRIES')
        if self.checkServiceAction('Hosts1', 'GetGenericHostEntry'):
            self.capabilities["HAS_HOST_GET_GENERIC_ENTRY"] = True
            logger.debug(f'Device {self.host} detected HAS_HOST_GET_GENERIC_ENTRY')
        if self.checkServiceAction('UserInterface1'):
            self.capabilities["HAS_USER_INTERFACE"] = True
            logger.debug(f'Device {self.host} detected HAS_USER_INTERFACE')
        if self.checkServiceAction('LANEthernetInterfaceConfig1'):
            self.capabilities["HAS_LAN_CONFIG_INFO"] = True
            logger.debug(f'Device {self.host} detected HAS_LAN_CONFIG_INFO')
        if self.checkServiceAction('LANEthernetInterfaceConfig1', 'GetStatistics'):
            self.capabilities["HAS_LAN_CONFIG_STATS"] = True
            logger.debug(f'Device {self.host} detected HAS_LAN_CONFIG_STATS')
        if self.checkServiceAction('WANDSLInterfaceConfig1'):
            self.capabilities["HAS_WAN_DSL_INTERFACE_CONFIG"] = True
            logger.debug(f'Device {self.host} detected HAS_WAN_DSL_INTERFACE_CONFIG')
        if self.checkServiceAction('WANPPPConnection1', 'GetStatusInfo'):
            self.capabilities["HAS_WAN_PPP_STATUS_INFO"] = True
            logger.debug(f'Device {self.host} detected HAS_WAN_PPP_STATUS_INFO')
        if self.checkServiceAction('WANCommonInterfaceConfig1', 'GetCommonLinkProperties'):
            self.capabilities["HAS_WAN_COMMON_INTERFACE_LINK_PROPERTIES"] = True
            logger.debug(f'Device {self.host} detected HAS_WAN_COMMON_INTERFACE_LINK_PROPERTIES')

        if self.checkServiceAction('WANCommonInterfaceConfig1', 'GetTotalBytesReceived')and self.checkServiceAction('WANCommonInterfaceConfig1', 'GetTotalBytesSent'):
            self.capabilities["HAS_WAN_COMMON_INTERFACE_BYTES"] = True
            logger.debug(f'Device {self.host} detected HAS_WAN_COMMON_INTERFACE_BYTES')
        if self.checkServiceAction('WANCommonInterfaceConfig1', 'GETTotalPacketsReceived') and self.checkServiceAction('WANCommonInterfaceConfig1', 'GetTotalPacketsSent'):
            self.capabilities["HAS_WAN_COMMON_INTERFACE_PACKETS"] = True
            logger.debug(f'Device {self.host} detected HAS_WAN_COMMON_INTERFACE_PACKETS')

    def checkServiceAction(self, service, action='GetInfo'):
        return (service in self.fc.services) and (action in self.fc.services[service].actions)

class FritzCollector(object):
    def __init__(self):
        self.devices = []
        self.capabilities = {
            "HAS_DEVICE_INFO": False,
            "HAS_HOST_NUMBER_OF_ENTRIES": False,
            "HAS_HOST_GET_GENERIC_ENTRY": False,
            "HAS_USER_INTERFACE": False,
            "HAS_LAN_CONFIG_INFO": False,
            "HAS_LAN_CONFIG_STATS": False,
            "HAS_WAN_DSL_INTERFACE_CONFIG": False,
            "HAS_WAN_PPP_STATUS_INFO": False,
            "HAS_WAN_COMMON_INTERFACE_LINK_PROPERTIES": False,
            "HAS_WAN_COMMON_INTERFACE_BYTES": False,
            "HAS_WAN_COMMON_INTERFACE_PACKETS": False,
        }

    def register(self, fritzdev):
        self.devices.append(fritzdev)
        logger.debug(f'registered device {fritzdev.host} ({fritzdev.model}) to collector')
        for capa, value in self.capabilities.items():
            self.capabilities[capa] = self.capabilities[capa] or fritzdev.capabilities[capa]

    def collect(self):        
        if not self.devices:
            logger.critical(f'No devices registered in collector! Exiting.')
            sys.exit(1)
        
        if self.capabilities["HAS_DEVICE_INFO"]:
            fritz_uptime = CounterMetricFamily('fritz_uptime', 'FritzBox uptime, system info in labels', labels=['modelname', 'softwaresersion', 'serial'])
            for dev in self.devices:
                if dev.capabilities["HAS_DEVICE_INFO"]:
                    info_result = dev.fc.call_action('DeviceInfo:1', 'GetInfo')
                    fritz_uptime.add_metric([info_result['NewModelName'], info_result['NewSoftwareVersion'], info_result['NewserialNumber']], info_result['NewUpTime'])
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
            fritz_lan_brx = CounterMetricFamily('fritz_lan_received_bytes', 'LAN bytes received', labels=['serial'])
            fritz_lan_btx = CounterMetricFamily('fritz_lan_transmitted_bytes', 'LAN bytes transmitted', labels=['serial'])
            fritz_lan_prx = CounterMetricFamily('fritz_lan_received_packets_total', 'LAN packets received', labels=['serial'])
            fritz_lan_ptx = CounterMetricFamily('fritz_lan_transmitted_packets_total', 'LAN packets transmitted', labels=['serial'])
            for dev in self.devices:
                if dev.capabilities["HAS_LAN_CONFIG_STATS"]:
                    lanstats_result = dev.fc.call_action('LANEthernetInterfaceConfig:1', 'GetStatistics')
                    fritz_lan_brx.add_metric([dev.serial], lanstats_result['NewBytesReceived'])
                    fritz_lan_btx.add_metric([dev.serial], lanstats_result['NewBytesSent'])
                    fritz_lan_prx.add_metric([dev.serial], lanstats_result['NewPacketsReceived'])
                    fritz_lan_ptx.add_metric([dev.serial], lanstats_result['NewPacketsSent'])
            yield fritz_lan_brx
            yield fritz_lan_btx
            yield fritz_lan_prx
            yield fritz_lan_ptx

        # WAN DSL Config Info
        if self.capabilities["HAS_WAN_DSL_INTERFACE_CONFIG"]:
            fritz_dsl_enable = GaugeMetricFamily('fritz_dsl_status_enabled', 'DSL enabled', labels=['serial'])
            fritz_dsl_datarate = GaugeMetricFamily('fritz_dsl_datarate_kbps', 'DSL datarate in kbps', labels= ['serial', 'Direction', 'Type'])
            fritz_dsl_noisemargin = GaugeMetricFamily('fritz_dsl_noise_margin_dB', 'Noise Margin in dB', labels=['serial', 'Direction'])
            fritz_dsl_attenuation = GaugeMetricFamily('fritz_dsl_attenuation_dB', 'Line attenuation in dB', labels=['serial', 'Direction'])
            fritz_dsl_status = GaugeMetricFamily('fritz_dsl_status', 'DSL status', labels=['serial'])
            for dev in self.devices:
                if dev.capabilities["HAS_WAN_DSL_INTERFACE_CONFIG"]:
                    fritz_dslinfo_result = dev.fc.call_action('WANDSLInterfaceConfig:1', 'GetInfo')
                    fritz_dsl_enable.add_metric([dev.serial], fritz_dslinfo_result['NewEnable'])
                    
                    dslstatus = 1 if fritz_dslinfo_result['NewStatus'] == 'Up' else 0
                    fritz_dsl_status.add_metric([dev.serial], dslstatus)

                    fritz_dsl_datarate.add_metric([dev.serial, 'up', 'curr'], fritz_dslinfo_result['NewUpstreamCurrRate'])
                    fritz_dsl_datarate.add_metric([dev.serial, 'down','curr'], fritz_dslinfo_result['NewDownstreamCurrRate'])
                    fritz_dsl_datarate.add_metric([dev.serial, 'up', 'max'], fritz_dslinfo_result['NewUpstreamMaxRate'])
                    fritz_dsl_datarate.add_metric([dev.serial, 'down','max'], fritz_dslinfo_result['NewDownstreamMaxRate'])
                    fritz_dsl_noisemargin.add_metric([dev.serial, 'up'], fritz_dslinfo_result['NewUpstreamNoiseMargin']/10)
                    fritz_dsl_noisemargin.add_metric([dev.serial, 'down'], fritz_dslinfo_result['NewDownstreamNoiseMargin']/10)
                    fritz_dsl_attenuation.add_metric([dev.serial, 'up'], fritz_dslinfo_result['NewUpstreamAttenuation']/10)
                    fritz_dsl_attenuation.add_metric([dev.serial, 'down'], fritz_dslinfo_result['NewDownstreamAttenuation']/10)

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
