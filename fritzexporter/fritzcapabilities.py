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
        logger.debug(f'Creating metrics objects for {type(self).__name__}')
        self.metrics['uptime'] = CounterMetricFamily('fritz_uptime', 'FritzBox uptime, system info in labels', labels=['modelname', 'softwareversion', 'serial'])

    def _getMetricValues(self, device):
        logger.debug(f'Populating metrics objects for {type(self).__name__}')
        info_result = device.fc.call_action('DeviceInfo:1', 'GetInfo')
        logger.debug(f'Collected metrics objects for {type(self).__name__}')
        self.metrics['uptime'].add_metric([info_result['NewModelName'], info_result['NewSoftwareVersion'], info_result['NewSerialNumber']], info_result['NewUpTime'])
        logger.debug(f'Yielding metrics objects for {type(self).__name__}')
        yield self.metrics['uptime']

#class HostNumberOfEntries(FritzCapability):
#    def __init__(self) -> None:
#        super().__init__()
#        self.requirements.append(('Hosts1', 'GetHostNumberOfEntries'))
#
#    def createMetrics(self):
#        pass
#
#    def _getMetricValues(self, device):
#        pass
        
#class HostGetGenericEntry(FritzCapability):
#    def __init__(self) -> None:
#        super().__init__()
#        self.requirements.append(('Hosts1', 'GetGenericHostEntry'))
#
#    def createMetrics(self):
#        pass
#
#    def _getMetricValues(self, device):
#        pass

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


class LanInterfaceConfigStatistocs(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('LANEthernetInterfaceConfig1', 'GetStatistics'))

    def createMetrics(self):
        self.metrics['lanbytes'] =  CounterMetricFamily('fritz_lan_data_bytes', 'LAN bytes received', labels=['serial', 'direction'])
        self.metrics['lanpackets'] = CounterMetricFamily('fritz_lan_packets_total', 'LAN packets transmitted', labels=['serial', 'direction'])

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
        self.metrics['datarate']  = GaugeMetricFamily('fritz_dsl_datarate_kbps', 'DSL datarate in kbps', labels= ['serial', 'direction', 'type'])
        self.metrics['noisemargin']  = GaugeMetricFamily('fritz_dsl_noise_margin_dB', 'Noise Margin in dB', labels=['serial', 'direction'])
        self.metrics['attenuation']  = GaugeMetricFamily('fritz_dsl_attenuation_dB', 'Line attenuation in dB', labels=['serial', 'direction'])
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
        self.metrics['uptime'] = GaugeMetricFamily('fritz_ppp_connection_uptime', 'PPP connection uptime', labels=['serial'])
        self.metrics['connected'] = GaugeMetricFamily('fritz_ppp_conection_state', 'PPP connection state', labels=['serial', 'last_error'])

    def _getMetricValues(self, device):
        fritz_pppstatus_result = device.fc.call_action('WANPPPConnection:1', 'GetStatusInfo')
        pppconnected = 1 if fritz_pppstatus_result['NewConnectionStatus'] == 'Connected' else 0
        self.metrics['uptime'].add_metric([device.serial], fritz_pppstatus_result['NewUptime'])
        self.metrics['connected'].add_metric([device.serial, fritz_pppstatus_result['NewLastConnectionError']], pppconnected)
        yield self.metrics['uptime']
        yield self.metrics['connected']

#class WanCommonInterfaceConfig(FritzCapability):
#    def __init__(self) -> None:
#        super().__init__()
#        self.requirements.append(('WANCommonInterfaceConfig1', 'GetCommonLinkProperties'))
#
#    def createMetrics(self):
#        pass
#
#    def _getMetricValues(self, device):
#        pass

class WanCommonInterfaceDataBytes(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalBytesReceived'))
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalBytesSent'))

    def createMetrics(self):
        self.metrics['wanbytes'] = CounterMetricFamily('fritz_wan_data_bytes', 'WAN data in bytes', labels=['serial', 'direction'])

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
        self.metrics['wanpackets'] = CounterMetricFamily('fritz_wan_data_packets', 'WAN data in packets', labels=['serial', 'direction'])

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsReceived')
        wan_packets_rx = fritz_wan_result['NewTotalPacketsReceived']
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsSent')
        wan_packets_tx = fritz_wan_result['NewTotalPacketsSent']
        self.metrics['wanpackets'].add_metric([device.serial, 'up'], wan_packets_tx)
        self.metrics['wanpackets'].add_metric([device.serial, 'down'], wan_packets_rx)
        yield self.metrics['wanpackets']


#class WlanConfigurationInfo(FritzCapability):
#    def __init__(self) -> None:
#        super().__init__()
#        self.requirements.append(('WLANConfiguration1', 'GetInfo'))
#
#    def createMetrics(self):
#        pass
#
#    def _getMetricValues(self, device):
#        pass
