# Copyright 2019 Patrick Dreker <patrick@dreker.de>
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

import fritzconnection as fc
import time, os
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily, REGISTRY
from prometheus_client import start_http_server

#for key in info_result:
#    print(f'{key}: {info_result[key]}')

class FritzBoxCollector(object):
    def __init__(self, host, user, passwd):
        self.host = host
        self.user = user
        self.passwd = passwd
        self.conn = fc.FritzConnection(address=self.host, user=self.user, password=self.passwd)

    def collect(self):
        info_result = self.conn.call_action('DeviceInfo:1', 'GetInfo')
        fritzbox_uptime = CounterMetricFamily('fritzbox_uptime', 'FritzBox uptime, system info in labels', labels=['ModelName', 'SoftwareVersion', 'Serial'])
        fritzbox_uptime.add_metric([info_result['NewModelName'], info_result['NewSoftwareVersion'], info_result['NewSerialNumber']], info_result['NewUpTime'])
        fb_serial = info_result['NewSerialNumber']
        yield fritzbox_uptime

        update_result = self.conn.call_action('UserInterface:1', 'GetInfo')
        fritzbox_update = GaugeMetricFamily('fritzbox_update_available', 'FritzBox update available', labels=['Serial', 'NewSoftwareVersion'])
        upd_available = 1 if update_result['NewUpgradeAvailable'] == '1' else 0
        fritzbox_update.add_metric([fb_serial, update_result['NewX_AVM-DE_Version']], upd_available)
        yield fritzbox_update

        lanstatus_result = self.conn.call_action('LANEthernetInterfaceConfig:1', 'GetInfo')
        fritzbox_lanenable = GaugeMetricFamily('fritzbox_lan_status_enabled', 'LAN Interface enabled', labels=['Serial'])
        fritzbox_lanenable.add_metric([fb_serial], lanstatus_result['NewEnable'])
        yield fritzbox_lanenable

        fritzbox_lanstatus = GaugeMetricFamily('fritzbox_lan_status', 'LAN Interface status', labels=['Serial'])
        lanstatus = 1 if lanstatus_result['NewStatus'] == 'Up' else 0
        fritzbox_lanstatus.add_metric([fb_serial], lanstatus)
        yield fritzbox_lanstatus

        lanstats_result = self.conn.call_action('LANEthernetInterfaceConfig:1', 'GetStatistics')
        fritzbox_lan_brx = CounterMetricFamily('fritzbox_lan_received_bytes', 'LAN bytes received', labels=['Serial'])
        fritzbox_lan_btx = CounterMetricFamily('fritzbox_lan_transmitted_bytes', 'LAN bytes transmitted', labels=['Serial'])
        fritzbox_lan_prx = CounterMetricFamily('fritzbox_lan_received_packets_total', 'LAN packets received', labels=['Serial'])
        fritzbox_lan_ptx = CounterMetricFamily('fritzbox_lan_transmitted_packets_total', 'LAN packets transmitted', labels=['Serial'])
        fritzbox_lan_brx.add_metric([fb_serial], lanstats_result['NewBytesReceived'])
        fritzbox_lan_btx.add_metric([fb_serial], lanstats_result['NewBytesSent'])
        fritzbox_lan_prx.add_metric([fb_serial], lanstats_result['NewPacketsReceived'])
        fritzbox_lan_ptx.add_metric([fb_serial], lanstats_result['NewPacketsSent'])
        yield fritzbox_lan_brx
        yield fritzbox_lan_btx
        yield fritzbox_lan_prx
        yield fritzbox_lan_ptx

        fritzbox_dslinfo_result = self.conn.call_action('WANDSLInterfaceConfig:1', 'GetInfo')
        fritzbox_dsl_enable = GaugeMetricFamily('fritzbox_dsl_status_enabled', 'DSL enabled', labels=['Serial'])
        fritzbox_dsl_enable.add_metric([fb_serial], fritzbox_dslinfo_result['NewEnable'])
        fritzbox_dsl_status = GaugeMetricFamily('fritzbox_dsl_status', 'DSL status', labels=['Serial'])
        dslstatus = 1 if fritzbox_dslinfo_result['NewStatus'] == 'Up' else 0
        fritzbox_dsl_status.add_metric([fb_serial], dslstatus)
        yield fritzbox_dsl_enable
        yield fritzbox_dsl_status

        fritzbox_dsl_datarate = GaugeMetricFamily('fritzbox_dsl_datarate_kbps', 'DSL datarate in kbps', labels= ['Serial', 'Direction', 'Type'])
        fritzbox_dsl_datarate.add_metric([fb_serial, 'up', 'curr'], fritzbox_dslinfo_result['NewUpstreamCurrRate'])
        fritzbox_dsl_datarate.add_metric([fb_serial, 'down','curr'], fritzbox_dslinfo_result['NewDownstreamCurrRate'])
        fritzbox_dsl_datarate.add_metric([fb_serial, 'up', 'max'], fritzbox_dslinfo_result['NewUpstreamMaxRate'])
        fritzbox_dsl_datarate.add_metric([fb_serial, 'down','max'], fritzbox_dslinfo_result['NewDownstreamMaxRate'])
        yield fritzbox_dsl_datarate

        fritzbox_dsl_noisemargin = GaugeMetricFamily('fritzbox_dsl_noise_margin_dB', 'Noise Margin in dB', labels=['Serial', 'Direction'])
        fritzbox_dsl_noisemargin.add_metric([fb_serial, 'up'], fritzbox_dslinfo_result['NewUpstreamNoiseMargin']/10)
        fritzbox_dsl_noisemargin.add_metric([fb_serial, 'down'], fritzbox_dslinfo_result['NewDownstreamNoiseMargin']/10)
        yield fritzbox_dsl_noisemargin

        fritzbox_dsl_attenuation = GaugeMetricFamily('fritzbox_dsl_attenuation_dB', 'Line attenuation in dB', labels=['Serial', 'Direction'])
        fritzbox_dsl_attenuation.add_metric([fb_serial, 'up'], fritzbox_dslinfo_result['NewUpstreamAttenuation']/10)
        fritzbox_dsl_attenuation.add_metric([fb_serial, 'down'], fritzbox_dslinfo_result['NewDownstreamAttenuation']/10)
        yield fritzbox_dsl_attenuation

        fritzbox_pppstatus_result = self.conn.call_action('WANPPPConnection:1', 'GetStatusInfo')
        pppconnected = 1 if fritzbox_pppstatus_result['NewConnectionStatus'] == 'Connected' else 0
        fritzbox_ppp_uptime = GaugeMetricFamily('fritzbox_ppp_connection_uptime', 'PPP connection uptime', labels=['Serial'])
        fritzbox_ppp_uptime.add_metric([fb_serial], fritzbox_pppstatus_result['NewUptime'])
        fritzbox_ppp_connected = GaugeMetricFamily('fritzbox_ppp_conection_state', 'PPP connection state', labels=['Serial', 'last_error'])
        fritzbox_ppp_connected.add_metric([fb_serial, fritzbox_pppstatus_result['NewLastConnectionError']], pppconnected)
        yield fritzbox_ppp_uptime
        yield fritzbox_ppp_connected

        fritzbox_wan_result = self.conn.call_action('WANCommonInterfaceConfig:1', 'GetTotalBytesReceived')
        wan_bytes_rx = fritzbox_wan_result['NewTotalBytesReceived']
        fritzbox_wan_result = self.conn.call_action('WANCommonInterfaceConfig:1', 'GetTotalBytesSent')
        wan_bytes_tx = fritzbox_wan_result['NewTotalBytesSent']
        fritzbox_wan_result = self.conn.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsReceived')
        wan_packets_rx = fritzbox_wan_result['NewTotalPacketsReceived']
        fritzbox_wan_result = self.conn.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsSent')
        wan_packets_tx = fritzbox_wan_result['NewTotalPacketsSent']
        
        fritzbox_wan_data = CounterMetricFamily('fritzbox_wan_data_bytes', 'WAN data in bytes', labels=['Serial', 'Direction'])
        fritzbox_wan_data.add_metric([fb_serial, 'up'], wan_bytes_tx)
        fritzbox_wan_data.add_metric([fb_serial, 'down'], wan_bytes_rx)
        fritzbox_wan_packets = CounterMetricFamily('fritzbox_wan_data_packets', 'WAN data in packets', labels=['Serial', 'Direction'])
        fritzbox_wan_packets.add_metric([fb_serial, 'up'], wan_packets_tx)
        fritzbox_wan_packets.add_metric([fb_serial, 'down'], wan_packets_rx)
        yield fritzbox_wan_data
        yield fritzbox_wan_packets

if __name__ == '__main__':

    REGISTRY.register(FritzBoxCollector(os.getenv('FRITZ_HOST', 'fritz.box'), os.getenv('FRITZ_USER'), os.getenv('FRITZ_PASS')))
    # Start up the server to expose the metrics.
    start_http_server(os.getenv('FRITZ_EXPORTER_PORT', 8765))
    while(True):
        time.sleep(10000)