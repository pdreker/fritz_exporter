import logging
from abc import ABC, abstractclassmethod, abstractmethod

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class FritzCapability(ABC):
    capabilities = []

    def __init__(self) -> None:
        self.present = False
        self.requirements = []
        self.metrics = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        FritzCapability.capabilities.append(cls)

    def checkCapability(self, device):
        self.present = all([ (service in device.fc.services) and (action in device.fc.services[service].actions) for (service, action) in self.requirements ])
        logger.debug(f'Capability {type(self).__name__} set to {self.present} on device {device.host}')

    def getMetrics(self, devices, name):
        for device in devices:
            if device.capabilities[name].present:
                yield from self._getMetricValues(device)

    @abstractmethod
    def createMetrics():
        pass

    @abstractmethod
    def _getMetricValues(self, device):
        pass

class FritzCapabilities():
    def __init__(self, device=None) -> None:
        self.capabilities = { capability.__name__: capability() for capability in FritzCapability.capabilities }
        if device:
           self.checkPresent(device)

    def __iter__(self):
        return iter(self.capabilities)

    def __len__(self):
        return len(self.capabilities)

    def __getitem__(self, index):
        return self.capabilities[index]

    def items(self):
        return self.capabilities.items()

    def merge(self, other_caps):
        for cap in self.capabilities:
            self.capabilities[cap].present = self.capabilities[cap].present or other_caps.capabilities[cap].present
        
    def empty(self):
        return not any([ cap.present for cap in list(self.capabilities.values()) ])

    def checkPresent(self, device):
        for c in self.capabilities:
            self.capabilities[c].checkCapability(device)

class DeviceInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('DeviceInfo1', 'GetInfo'))

    def createMetrics(self):
        self.metrics['uptime'] = CounterMetricFamily('fritz_uptime', 'FritzBox uptime, system info in labels', labels=['modelname', 'softwareversion', 'serial'], unit='seconds_total')

    def _getMetricValues(self, device):
        info_result = device.fc.call_action('DeviceInfo:1', 'GetInfo')
        self.metrics['uptime'].add_metric([info_result['NewModelName'], info_result['NewSoftwareVersion'], info_result['NewSerialNumber']], info_result['NewUpTime'])
        yield self.metrics['uptime']

class HostNumberOfEntries(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('Hosts1', 'GetHostNumberOfEntries'))

    def createMetrics(self):
        self.metrics['numhosts'] = GaugeMetricFamily('fritz_known_devices', 'Number of devices in hosts table', labels=['serial'], unit='count_total')

    def _getMetricValues(self, device):
        num_hosts_result = device.fc.call_action('Hosts1', 'GetHostNumberOfEntries')
        self.metrics['numhosts'].add_metric([device.serial], num_hosts_result['NewHostNumberOfEntries'])
        yield self.metrics['numhosts']
        
#class HostInfo(FritzCapability):
#    def __init__(self) -> None:
#        super().__init__()
#        self.requirements.append(('Hosts1', 'GetHostNumberOfEntries'))
#        self.requirements.append(('Hosts1', 'GetGenericHostEntry'))
#        self.requirements.append(('Hosts1', 'X_AVM-DE_GetSpecificHostEntryByIP'))
#
#    def createMetrics(self):
#        self.metrics['hostactive'] = GaugeMetricFamily('fritz_host_active', 'Indicates that the device is curently active', labels=['serial', 'ip_address', 'mac_address', 'hostname', 'interface', 'port', 'model'])
#        self.metrics['hostspeed']  = GaugeMetricFamily('fritz_host_speed', 'Connection speed of the device', labels=['serial', 'ip_address', 'mac_address', 'hostname', 'interface', 'port', 'model'])
#
#    def _getMetricValues(self, device):
#        num_hosts_result = device.fc.call_action('Hosts1', 'GetHostNumberOfEntries')
#        logger.debug(f'Fetching host information for device serial {device.serial} (hosts found: {num_hosts_result["NewHostNumberOfEntries"]}')
#        for host_index in range(num_hosts_result['NewHostNumberOfEntries']):
#            logger.debug(f'Fetching generic host information for host number {host_index}')
#            host_result = device.fc.call_action('Hosts1', 'GetGenericHostEntry', NewIndex=host_index)
#            
#            host_ip = host_result['NewIPAddress']
#            host_mac = host_result['NewMACAddress']
#            host_name = host_result['NewHostName']
#            
#            if host_ip != "":
#                logger.debug(f'Fetching extended AVM host information for host number {host_index} by IP {host_ip}')
#                avm_host_result = device.fc.call_action('Hosts1', 'X_AVM-DE_GetSpecificHostEntryByIP', NewIPAddress=host_ip)
#                host_interface = avm_host_result['NewInterfaceType']
#                host_port = str(avm_host_result['NewX_AVM-DE_Port'])
#                host_model = avm_host_result['NewX_AVM-DE_Model']
#                host_speed = avm_host_result['NewX_AVM-DE_Speed']
#            else:
#                logger.debug(f'Unable to fetch extended AVM host information for host number {host_index}: no IP found')
#                host_interface = "n/a"
#                host_port = "n/a"
#                host_model = "n/a"
#                host_speed = 0
#
#            host_active = 1.0 if host_result['NewActive'] else 0.0
#            self.metrics['hostactive'].add_metric([device.serial, host_ip, host_mac, host_name, host_interface, host_port, host_model], host_active)
#            self.metrics['hostspeed'].add_metric([device.serial, host_ip, host_mac, host_name, host_interface, host_port, host_model], host_speed)
#
#            yield self.metrics['hostactive']
#            yield self.metrics['hostspeed']

class UserInterface(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('UserInterface1', 'GetInfo'))

    def createMetrics(self):
        self.metrics['update'] = GaugeMetricFamily('fritz_update_available', 'FritzBox update available', labels=['serial', 'newsoftwareversion'])

    def _getMetricValues(self, device):
        update_result = device.fc.call_action('UserInterface:1', 'GetInfo')
        upd_available = 1 if update_result['NewUpgradeAvailable'] == '1' else 0
        new_software_version = "n/a" if update_result['NewX_AVM-DE_Version'] is None else update_result['NewX_AVM-DE_Version']
        self.metrics['update'].add_metric([device.serial, new_software_version], upd_available)
        yield self.metrics['update']


class LanInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('LANEthernetInterfaceConfig1', 'GetInfo'))

    def createMetrics(self):
        self.metrics['lanenable'] = GaugeMetricFamily('fritz_lan_status_enabled', 'LAN Interface enabled', labels=['serial'])
        self.metrics['lanstatus'] = GaugeMetricFamily('fritz_lan_status', 'LAN Interface status', labels=['serial'])

    def _getMetricValues(self, device):
        lanstatus_result = device.fc.call_action('LANEthernetInterfaceConfig:1', 'GetInfo')
        self.metrics['lanenable'].add_metric([device.serial], lanstatus_result['NewEnable'])

        lanstatus = 1 if lanstatus_result['NewStatus'] == 'Up' else 0
        self.metrics['lanstatus'].add_metric([device.serial], lanstatus)
        yield self.metrics['lanenable']
        yield self.metrics['lanstatus']


class LanInterfaceConfigStatistics(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('LANEthernetInterfaceConfig1', 'GetStatistics'))

    def createMetrics(self):
        self.metrics['lanbytes'] =  CounterMetricFamily('fritz_lan_data', 'LAN bytes received', labels=['serial', 'direction'], unit='bytes')
        self.metrics['lanpackets'] = CounterMetricFamily('fritz_lan_packet', 'LAN packets transmitted', labels=['serial', 'direction'], unit='count_total')

    def _getMetricValues(self, device):
        lanstats_result = device.fc.call_action('LANEthernetInterfaceConfig:1', 'GetStatistics')
        self.metrics['lanbytes'].add_metric([device.serial, 'rx'], lanstats_result['NewBytesReceived'])
        self.metrics['lanbytes'].add_metric([device.serial, 'tx'], lanstats_result['NewBytesSent'])
        self.metrics['lanpackets'].add_metric([device.serial, 'rx'], lanstats_result['NewPacketsReceived'])
        self.metrics['lanpackets'].add_metric([device.serial, 'tx'], lanstats_result['NewPacketsSent'])
        yield self.metrics['lanbytes']
        yield self.metrics['lanpackets']

class WanDSLInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANDSLInterfaceConfig1', 'GetInfo'))

    def createMetrics(self):
        self.metrics['enable']  = GaugeMetricFamily('fritz_dsl_status_enabled', 'DSL enabled', labels=['serial'])
        self.metrics['datarate']  = GaugeMetricFamily('fritz_dsl_datarate', 'DSL datarate in kbps', labels= ['serial', 'direction', 'type'], unit='kbps')
        self.metrics['noisemargin']  = GaugeMetricFamily('fritz_dsl_noise_margin', 'Noise Margin in dB', labels=['serial', 'direction'], unit='dB')
        self.metrics['attenuation']  = GaugeMetricFamily('fritz_dsl_attenuation', 'Line attenuation in dB', labels=['serial', 'direction'], unit='dB')
        self.metrics['status']  = GaugeMetricFamily('fritz_dsl_status', 'DSL status', labels=['serial'])

    def _getMetricValues(self, device):
        fritz_dslinfo_result = device.fc.call_action('WANDSLInterfaceConfig:1', 'GetInfo')
        self.metrics['enable'] .add_metric([device.serial], fritz_dslinfo_result['NewEnable'])
        
        dslstatus = 1 if fritz_dslinfo_result['NewStatus'] == 'Up' else 0
        self.metrics['status'].add_metric([device.serial], dslstatus)
        self.metrics['datarate'].add_metric([device.serial, 'tx', 'curr'], fritz_dslinfo_result['NewUpstreamCurrRate'])
        self.metrics['datarate'].add_metric([device.serial, 'rx','curr'], fritz_dslinfo_result['NewDownstreamCurrRate'])
        self.metrics['datarate'].add_metric([device.serial, 'tx', 'max'], fritz_dslinfo_result['NewUpstreamMaxRate'])
        self.metrics['datarate'].add_metric([device.serial, 'rx','max'], fritz_dslinfo_result['NewDownstreamMaxRate'])
        self.metrics['noisemargin'].add_metric([device.serial, 'tx'], fritz_dslinfo_result['NewUpstreamNoiseMargin']/10)
        self.metrics['noisemargin'].add_metric([device.serial, 'rx'], fritz_dslinfo_result['NewDownstreamNoiseMargin']/10)
        self.metrics['attenuation'].add_metric([device.serial, 'tx'], fritz_dslinfo_result['NewUpstreamAttenuation']/10)
        self.metrics['attenuation'].add_metric([device.serial, 'rx'], fritz_dslinfo_result['NewDownstreamAttenuation']/10)

        yield self.metrics['enable'] 
        yield self.metrics['status'] 
        yield self.metrics['datarate'] 
        yield self.metrics['noisemargin'] 
        yield self.metrics['attenuation'] 

class WanPPPConnectionStatus(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANPPPConnection1', 'GetStatusInfo'))

    def createMetrics(self):
        self.metrics['uptime'] = GaugeMetricFamily('fritz_ppp_connection_uptime', 'PPP connection uptime', labels=['serial'], unit='seconds_total')
        self.metrics['connected'] = GaugeMetricFamily('fritz_ppp_connection_state', 'PPP connection state', labels=['serial', 'last_error'])

    def _getMetricValues(self, device):
        fritz_pppstatus_result = device.fc.call_action('WANPPPConnection:1', 'GetStatusInfo')
        pppconnected = 1 if fritz_pppstatus_result['NewConnectionStatus'] == 'Connected' else 0
        self.metrics['uptime'].add_metric([device.serial], fritz_pppstatus_result['NewUptime'])
        self.metrics['connected'].add_metric([device.serial, fritz_pppstatus_result['NewLastConnectionError']], pppconnected)
        yield self.metrics['uptime']
        yield self.metrics['connected']

class WanCommonInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetCommonLinkProperties'))

    def createMetrics(self):
        self.metrics['wanconfig'] = GaugeMetricFamily('fritz_wan_max_bitrate', 'max bitrate at the physical layer', labels=['serial', 'wantype', 'direction'], unit='bps')
        self.metrics['wanlinkstatus'] = GaugeMetricFamily('fritz_wan_phys_link_status', 'link status at the physical layer', labels=['serial', 'wantype'])

    def _getMetricValues(self, device):
        wanstatus_result = device.fc.call_action('WANCommonInterfaceConfig1', 'GetCommonLinkProperties')
        self.metrics['wanconfig'].add_metric([device.serial, wanstatus_result['NewWANAccessType'], 'tx'], wanstatus_result['NewLayer1UpstreamMaxBitRate'])
        self.metrics['wanconfig'].add_metric([device.serial, wanstatus_result['NewWANAccessType'], 'rx'], wanstatus_result['NewLayer1DownstreamMaxBitRate'])
        l1_status = wanstatus_result['NewPhysicalLinkStatus']
        wanstatus = 1 if l1_status == "Up" else 0
        self.metrics['wanlinkstatus'].add_metric([device.serial, wanstatus_result['NewWANAccessType']], wanstatus)

        yield self.metrics['wanconfig']
        yield self.metrics['wanlinkstatus']
class WanCommonInterfaceDataBytes(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalBytesReceived'))
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalBytesSent'))

    def createMetrics(self):
        self.metrics['wanbytes'] = CounterMetricFamily('fritz_wan_data', 'WAN data in bytes', labels=['serial', 'direction'], unit='bytes_total')

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalBytesReceived')
        wan_bytes_rx = fritz_wan_result['NewTotalBytesReceived']
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalBytesSent')
        wan_bytes_tx = fritz_wan_result['NewTotalBytesSent']
        self.metrics['wanbytes'].add_metric([device.serial, 'up'], wan_bytes_tx)
        self.metrics['wanbytes'].add_metric([device.serial, 'down'], wan_bytes_rx)
        yield self.metrics['wanbytes']

class WanCommonInterfaceDataPackets(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalPacketsReceived'))
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalPacketsSent'))

    def createMetrics(self):
        self.metrics['wanpackets'] = CounterMetricFamily('fritz_wan_data_packets', 'WAN data in packets', labels=['serial', 'direction'], unit='count_total')

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsReceived')
        wan_packets_rx = fritz_wan_result['NewTotalPacketsReceived']
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsSent')
        wan_packets_tx = fritz_wan_result['NewTotalPacketsSent']
        self.metrics['wanpackets'].add_metric([device.serial, 'up'], wan_packets_tx)
        self.metrics['wanpackets'].add_metric([device.serial, 'down'], wan_packets_rx)
        yield self.metrics['wanpackets']


def wlanConsructorFactory(obj_ref, index):
    obj_ref.requirements.append((f'WLANConfiguration{index}', 'GetInfo'))
    obj_ref.requirements.append((f'WLANConfiguration{index}', 'GetTotalAssociations'))
    obj_ref.requirements.append((f'WLANConfiguration{index}', 'GetPacketStatistics'))

def wlanCreateMetricsFactory(obj_ref, name):
    m_name = name.replace('.', '_')
    obj_ref.metrics['wlanstatus']  = GaugeMetricFamily(f'fritz_wifi_{m_name}_status', f'Status of the {name} WiFi', labels=['serial', 'enabled', 'standard', 'ssid'])
    obj_ref.metrics['wlanchannel'] = GaugeMetricFamily(f'fritz_wifi_{m_name}_channel', f'Channel of the {name} WiFi', labels=['serial', 'enabled', 'standard', 'ssid'])
    obj_ref.metrics['wlanassocs']  = GaugeMetricFamily(f'fritz_wifi_{m_name}_associations', f'Number of associations (devices) of the {name} WiFi', labels=['serial', 'enabled', 'standard', 'ssid'], unit='count_total')
    obj_ref.metrics['wlanpackets'] = GaugeMetricFamily(f'fritz_wifi_{m_name}_packets', f'Amount of packets of the {name} WiFi', labels=['serial', 'enabled', 'standard', 'ssid', 'direction'], unit='count_total')

def wlanGetMetricsFactory(obj_ref, index, device):
        wlan_result = device.fc.call_action(f'WLANConfiguration{index}', 'GetInfo')
        wlan_status = 1 if wlan_result['NewStatus'] == "Up" else 0
        wlan_enabled = '1' if wlan_result['NewEnable'] else '0'
        obj_ref.metrics['wlanstatus'].add_metric([device.serial, wlan_enabled, wlan_result['NewStandard'], wlan_result['NewSSID']], wlan_status)
        obj_ref.metrics['wlanchannel'].add_metric([device.serial, wlan_enabled, wlan_result['NewStandard'], wlan_result['NewSSID']], wlan_result['NewChannel'])
        assoc_results = device.fc.call_action(f'WLANConfiguration{index}', 'GetTotalAssociations')
        obj_ref.metrics['wlanassocs'].add_metric([device.serial, wlan_enabled, wlan_result['NewStandard'], wlan_result['NewSSID']], assoc_results['NewTotalAssociations'])

        packet_stats_result = device.fc.call_action(f'WLANConfiguration{index}', 'GetPacketStatistics')
        obj_ref.metrics['wlanpackets'].add_metric([device.serial, wlan_enabled, wlan_result['NewStandard'], wlan_result['NewSSID'], 'rx'], packet_stats_result['NewTotalPacketsReceived'])
        obj_ref.metrics['wlanpackets'].add_metric([device.serial, wlan_enabled, wlan_result['NewStandard'], wlan_result['NewSSID'], 'tx'], packet_stats_result['NewTotalPacketsSent'])

        yield obj_ref.metrics['wlanstatus']
        yield obj_ref.metrics['wlanchannel']
        yield obj_ref.metrics['wlanassocs']
        yield obj_ref.metrics['wlanpackets']


class WlanConfigurationInfo2_4GHz(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        wlanConsructorFactory(self, 1)

    def createMetrics(self):
        wlanCreateMetricsFactory(self, '2.4GHz')

    def _getMetricValues(self, device):
        yield from wlanGetMetricsFactory(self, 1, device)

class WlanConfigurationInfo5GHz(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        wlanConsructorFactory(self, 2)

    def createMetrics(self):
        wlanCreateMetricsFactory(self, '5GHz')

    def _getMetricValues(self, device):
        yield from wlanGetMetricsFactory(self, 2, device)

class WlanConfigurationInfoGuest(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        wlanConsructorFactory(self, 3)

    def createMetrics(self):
        wlanCreateMetricsFactory(self, 'guest')

    def _getMetricValues(self, device):
        yield from wlanGetMetricsFactory(self, 3, device)
