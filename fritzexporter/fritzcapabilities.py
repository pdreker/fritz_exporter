import logging
from abc import ABC, abstractmethod

from fritzconnection.core.exceptions import ActionError, ServiceError, FritzInternalError
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARN)


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
        self.present = all([(service in device.fc.services) and (action in device.fc.services[service].actions) for (service, action) in self.requirements])
        logger.debug(f'Capability {type(self).__name__} set to {self.present} on device {device.host}')

        # It seems some boxes report service/actions they don't actually support. So try calling the requirements,
        # and if it throws "InvalidService", "InvalidAction" or "FritzInternalError" disable this again.
        if self.present:
            for (svc, action) in self.requirements:
                try:
                    device.fc.call_action(svc, action)
                except (ServiceError, ActionError, FritzInternalError) as e:
                    logger.warn(f'disabling metrics at service {svc}, action {action} - fritzconnection.call_action returned {e}')
                    self.present = False

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
        self.capabilities = {capability.__name__: capability() for capability in FritzCapability.capabilities}
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
        return not any([cap.present for cap in list(self.capabilities.values())])

    def checkPresent(self, device):
        for c in self.capabilities:
            self.capabilities[c].checkCapability(device)


class DeviceInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('DeviceInfo1', 'GetInfo'))

    def createMetrics(self):
        self.metrics['uptime'] = CounterMetricFamily('fritz_uptime', 'FritzBox uptime, system info in labels',
                                                     labels=['modelname', 'softwareversion', 'serial', 'friendly_name'], unit='seconds')

    def _getMetricValues(self, device):
        info_result = device.fc.call_action('DeviceInfo:1', 'GetInfo')
        self.metrics['uptime'].add_metric([info_result['NewModelName'], info_result['NewSoftwareVersion'], info_result['NewSerialNumber'],
                                          device.friendly_name], info_result['NewUpTime'])
        yield self.metrics['uptime']


class HostNumberOfEntries(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('Hosts1', 'GetHostNumberOfEntries'))

    def createMetrics(self):
        self.metrics['numhosts'] = GaugeMetricFamily('fritz_known_devices', 'Number of devices in hosts table',
                                                     labels=['serial', 'friendly_name'], unit='count')

    def _getMetricValues(self, device):
        num_hosts_result = device.fc.call_action('Hosts1', 'GetHostNumberOfEntries')
        self.metrics['numhosts'].add_metric([device.serial, device.friendly_name], num_hosts_result['NewHostNumberOfEntries'])
        yield self.metrics['numhosts']


# class HostInfo(FritzCapability):
#    def __init__(self) -> None:
#        super().__init__()
#        self.requirements.append(('Hosts1', 'GetHostNumberOfEntries'))
#        self.requirements.append(('Hosts1', 'GetGenericHostEntry'))
#        self.requirements.append(('Hosts1', 'X_AVM-DE_GetSpecificHostEntryByIP'))
#
#    def createMetrics(self):
#        self.metrics['hostactive'] = GaugeMetricFamily('fritz_host_active', 'Indicates that the device is curently active',
#                                                       labels=['serial', 'ip_address', 'mac_address', 'hostname', 'interface', 'port', 'model'])
#        self.metrics['hostspeed']  = GaugeMetricFamily('fritz_host_speed', 'Connection speed of the device',
#                                                       labels=['serial', 'ip_address', 'mac_address', 'hostname', 'interface', 'port', 'model'])
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
        self.metrics['update'] = GaugeMetricFamily('fritz_update_available', 'FritzBox update available',
                                                   labels=['serial', 'friendly_name', 'newsoftwareversion'])

    def _getMetricValues(self, device):
        update_result = device.fc.call_action('UserInterface:1', 'GetInfo')
        upd_available = 1 if update_result['NewUpgradeAvailable'] else 0
        new_software_version = update_result['NewX_AVM-DE_Version'] if update_result['NewUpgradeAvailable'] else 'n/a'
        self.metrics['update'].add_metric([device.serial, device.friendly_name, new_software_version], upd_available)
        yield self.metrics['update']


class LanInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('LANEthernetInterfaceConfig1', 'GetInfo'))

    def createMetrics(self):
        self.metrics['lanenable'] = GaugeMetricFamily('fritz_lan_status_enabled', 'LAN Interface enabled', labels=['serial', 'friendly_name'])
        self.metrics['lanstatus'] = GaugeMetricFamily('fritz_lan_status', 'LAN Interface status', labels=['serial', 'friendly_name'])

    def _getMetricValues(self, device):
        lanstatus_result = device.fc.call_action('LANEthernetInterfaceConfig:1', 'GetInfo')
        self.metrics['lanenable'].add_metric([device.serial, device.friendly_name], lanstatus_result['NewEnable'])

        lanstatus = 1 if lanstatus_result['NewStatus'] == 'Up' else 0
        self.metrics['lanstatus'].add_metric([device.serial, device.friendly_name], lanstatus)
        yield self.metrics['lanenable']
        yield self.metrics['lanstatus']


class LanInterfaceConfigStatistics(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('LANEthernetInterfaceConfig1', 'GetStatistics'))

    def createMetrics(self):
        self.metrics['lanbytes'] = CounterMetricFamily('fritz_lan_data', 'LAN bytes received', labels=['serial', 'friendly_name', 'direction'], unit='bytes')
        self.metrics['lanpackets'] = CounterMetricFamily('fritz_lan_packet', 'LAN packets transmitted',
                                                         labels=['serial', 'friendly_name', 'direction'], unit='count')

    def _getMetricValues(self, device):
        lanstats_result = device.fc.call_action('LANEthernetInterfaceConfig:1', 'GetStatistics')
        self.metrics['lanbytes'].add_metric([device.serial, device.friendly_name, 'rx'], lanstats_result['NewBytesReceived'])
        self.metrics['lanbytes'].add_metric([device.serial, device.friendly_name, 'tx'], lanstats_result['NewBytesSent'])
        self.metrics['lanpackets'].add_metric([device.serial, device.friendly_name, 'rx'], lanstats_result['NewPacketsReceived'])
        self.metrics['lanpackets'].add_metric([device.serial, device.friendly_name, 'tx'], lanstats_result['NewPacketsSent'])
        yield self.metrics['lanbytes']
        yield self.metrics['lanpackets']


class WanDSLInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANDSLInterfaceConfig1', 'GetInfo'))

    def createMetrics(self):
        self.metrics['enable'] = GaugeMetricFamily('fritz_dsl_status_enabled', 'DSL enabled', labels=['serial', 'friendly_name'])
        self.metrics['datarate'] = GaugeMetricFamily('fritz_dsl_datarate', 'DSL datarate in kbps',
                                                     labels=['serial', 'friendly_name', 'direction', 'type'], unit='kbps')
        self.metrics['noisemargin'] = GaugeMetricFamily('fritz_dsl_noise_margin', 'Noise Margin in dB',
                                                        labels=['serial', 'friendly_name', 'direction'], unit='dB')
        self.metrics['attenuation'] = GaugeMetricFamily('fritz_dsl_attenuation', 'Line attenuation in dB',
                                                        labels=['serial', 'friendly_name', 'direction'], unit='dB')
        self.metrics['status'] = GaugeMetricFamily('fritz_dsl_status', 'DSL status', labels=['serial', 'friendly_name'])

    def _getMetricValues(self, device):
        fritz_dslinfo_result = device.fc.call_action('WANDSLInterfaceConfig:1', 'GetInfo')
        self.metrics['enable'].add_metric([device.serial, device.friendly_name], fritz_dslinfo_result['NewEnable'])

        dslstatus = 1 if fritz_dslinfo_result['NewStatus'] == 'Up' else 0
        self.metrics['status'].add_metric([device.serial, device.friendly_name], dslstatus)
        self.metrics['datarate'].add_metric([device.serial, device.friendly_name, 'tx', 'curr'], fritz_dslinfo_result['NewUpstreamCurrRate'])
        self.metrics['datarate'].add_metric([device.serial, device.friendly_name, 'rx', 'curr'], fritz_dslinfo_result['NewDownstreamCurrRate'])
        self.metrics['datarate'].add_metric([device.serial, device.friendly_name, 'tx', 'max'], fritz_dslinfo_result['NewUpstreamMaxRate'])
        self.metrics['datarate'].add_metric([device.serial, device.friendly_name, 'rx', 'max'], fritz_dslinfo_result['NewDownstreamMaxRate'])
        self.metrics['noisemargin'].add_metric([device.serial, device.friendly_name, 'tx'], fritz_dslinfo_result['NewUpstreamNoiseMargin']/10)
        self.metrics['noisemargin'].add_metric([device.serial, device.friendly_name, 'rx'], fritz_dslinfo_result['NewDownstreamNoiseMargin']/10)
        self.metrics['attenuation'].add_metric([device.serial, device.friendly_name, 'tx'], fritz_dslinfo_result['NewUpstreamAttenuation']/10)
        self.metrics['attenuation'].add_metric([device.serial, device.friendly_name, 'rx'], fritz_dslinfo_result['NewDownstreamAttenuation']/10)

        yield self.metrics['enable']
        yield self.metrics['status']
        yield self.metrics['datarate']
        yield self.metrics['noisemargin']
        yield self.metrics['attenuation']


class WanDSLInterfaceConfigAVM(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANDSLInterfaceConfig1', 'X_AVM-DE_GetDSLInfo'))

    def createMetrics(self):
        self.metrics['fec'] = CounterMetricFamily('fritz_dsl_fec_errors_count', 'Number of Forward Error Correction Errors', labels=['serial', 'friendly_name'])
        self.metrics['crc'] = CounterMetricFamily('fritz_dsl_crc_errors_count', 'Number of CRC Errors', labels=['serial', 'friendly_name'])

    def _getMetricValues(self, device):
        fritz_avm_dsl_result = device.fc.call_action('WANDSLInterfaceConfig1', 'X_AVM-DE_GetDSLInfo')
        self.metrics['fec'].add_metric([device.serial, device.friendly_name], fritz_avm_dsl_result['NewFECErrors'])
        self.metrics['crc'].add_metric([device.serial, device.friendly_name], fritz_avm_dsl_result['NewCRCErrors'])

        yield self.metrics['fec']
        yield self.metrics['crc']


class WanPPPConnectionStatus(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANPPPConnection1', 'GetStatusInfo'))

    def createMetrics(self):
        self.metrics['uptime'] = CounterMetricFamily('fritz_ppp_connection_uptime', 'PPP connection uptime', labels=['serial', 'friendly_name'], unit='seconds')
        self.metrics['connected'] = GaugeMetricFamily('fritz_ppp_connection_state', 'PPP connection state', labels=['serial', 'friendly_name', 'last_error'])

    def _getMetricValues(self, device):
        fritz_pppstatus_result = device.fc.call_action('WANPPPConnection:1', 'GetStatusInfo')
        pppconnected = 1 if fritz_pppstatus_result['NewConnectionStatus'] == 'Connected' else 0
        self.metrics['uptime'].add_metric([device.serial, device.friendly_name], fritz_pppstatus_result['NewUptime'])
        self.metrics['connected'].add_metric([device.serial, device.friendly_name, fritz_pppstatus_result['NewLastConnectionError']], pppconnected)
        yield self.metrics['uptime']
        yield self.metrics['connected']


class WanCommonInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetCommonLinkProperties'))

    def createMetrics(self):
        self.metrics['wanconfig'] = GaugeMetricFamily('fritz_wan_max_bitrate', 'max bitrate at the physical layer',
                                                      labels=['serial', 'friendly_name', 'wantype', 'direction'], unit='bps')
        self.metrics['wanlinkstatus'] = GaugeMetricFamily('fritz_wan_phys_link_status', 'link status at the physical layer',
                                                          labels=['serial', 'friendly_name', 'wantype'])

    def _getMetricValues(self, device):
        wanstatus_result = device.fc.call_action('WANCommonInterfaceConfig1', 'GetCommonLinkProperties')
        self.metrics['wanconfig'].add_metric([device.serial, device.friendly_name, wanstatus_result['NewWANAccessType'], 'tx'],
                                             wanstatus_result['NewLayer1UpstreamMaxBitRate'])
        self.metrics['wanconfig'].add_metric([device.serial, device.friendly_name, wanstatus_result['NewWANAccessType'], 'rx'],
                                             wanstatus_result['NewLayer1DownstreamMaxBitRate'])
        l1_status = wanstatus_result['NewPhysicalLinkStatus']
        wanstatus = 1 if l1_status == "Up" else 0
        self.metrics['wanlinkstatus'].add_metric([device.serial, device.friendly_name, wanstatus_result['NewWANAccessType']], wanstatus)

        yield self.metrics['wanconfig']
        yield self.metrics['wanlinkstatus']


class WanCommonInterfaceDataBytes(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalBytesReceived'))
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalBytesSent'))

    def createMetrics(self):
        self.metrics['wanbytes'] = CounterMetricFamily('fritz_wan_data', 'WAN data in bytes', labels=['serial', 'friendly_name', 'direction'], unit='bytes')

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalBytesReceived')
        wan_bytes_rx = fritz_wan_result['NewTotalBytesReceived']
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalBytesSent')
        wan_bytes_tx = fritz_wan_result['NewTotalBytesSent']
        self.metrics['wanbytes'].add_metric([device.serial, device.friendly_name, 'tx'], wan_bytes_tx)
        self.metrics['wanbytes'].add_metric([device.serial, device.friendly_name, 'rx'], wan_bytes_rx)
        yield self.metrics['wanbytes']


class WanCommonInterfaceByteRate(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonIFC1', 'GetAddonInfos'))

    def createMetrics(self):
        self.metrics['wanbyterate'] = GaugeMetricFamily('fritz_wan_datarate', 'Current WAN data rate in bytes/s',
                                                        labels=['serial', 'friendly_name', 'direction'], unit='bytes')

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action('WANCommonIFC1', 'GetAddonInfos')
        wan_byterate_rx = fritz_wan_result['NewByteReceiveRate']
        wan_byterate_tx = fritz_wan_result['NewByteSendRate']
        self.metrics['wanbyterate'].add_metric([device.serial, device.friendly_name, 'rx'], wan_byterate_rx)
        self.metrics['wanbyterate'].add_metric([device.serial, device.friendly_name, 'tx'], wan_byterate_tx)
        yield self.metrics['wanbyterate']


class WanCommonInterfaceDataPackets(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalPacketsReceived'))
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalPacketsSent'))

    def createMetrics(self):
        self.metrics['wanpackets'] = CounterMetricFamily('fritz_wan_data_packets', 'WAN data in packets',
                                                         labels=['serial', 'friendly_name', 'direction'], unit='count')

    def _getMetricValues(self, device):
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsReceived')
        wan_packets_rx = fritz_wan_result['NewTotalPacketsReceived']
        fritz_wan_result = device.fc.call_action('WANCommonInterfaceConfig:1', 'GetTotalPacketsSent')
        wan_packets_tx = fritz_wan_result['NewTotalPacketsSent']
        self.metrics['wanpackets'].add_metric([device.serial, device.friendly_name, 'tx'], wan_packets_tx)
        self.metrics['wanpackets'].add_metric([device.serial, device.friendly_name, 'rx'], wan_packets_rx)
        yield self.metrics['wanpackets']


def wlanConsructorFactory(obj_ref, index):
    obj_ref.requirements.append((f'WLANConfiguration{index}', 'GetInfo'))
    obj_ref.requirements.append((f'WLANConfiguration{index}', 'GetTotalAssociations'))
    obj_ref.requirements.append((f'WLANConfiguration{index}', 'GetPacketStatistics'))


def wlanCreateMetricsFactory(obj_ref):
    m_name = obj_ref.wifi_type.replace('.', '_')
    name = obj_ref.wifi_type
    obj_ref.metrics['wlanstatus'] = GaugeMetricFamily(f'fritz_wifi_{m_name}_status', f'Status of the {name} WiFi',
                                                      labels=['serial', 'friendly_name', 'enabled', 'standard', 'ssid'])
    obj_ref.metrics['wlanchannel'] = GaugeMetricFamily(f'fritz_wifi_{m_name}_channel', f'Channel of the {name} WiFi',
                                                       labels=['serial', 'friendly_name', 'enabled', 'standard', 'ssid'])
    obj_ref.metrics['wlanassocs'] = GaugeMetricFamily(f'fritz_wifi_{m_name}_associations', f'Number of associations (devices) of the {name} WiFi',
                                                      labels=['serial', 'friendly_name', 'enabled', 'standard', 'ssid'], unit='count')
    obj_ref.metrics['wlanpackets'] = CounterMetricFamily(f'fritz_wifi_{m_name}_packets', f'Amount of packets of the {name} WiFi',
                                                         labels=['serial', 'friendly_name', 'enabled', 'standard', 'ssid', 'direction'], unit='count')


def wlanGetMetricsFactory(obj_ref, index, device):
    wlan_result = device.fc.call_action(f'WLANConfiguration{index}', 'GetInfo')
    wlan_status = 1 if wlan_result['NewStatus'] == "Up" else 0
    wlan_enabled = '1' if wlan_result['NewEnable'] else '0'
    obj_ref.metrics['wlanstatus'].add_metric([device.serial, device.friendly_name, wlan_enabled,
                                             wlan_result['NewStandard'], wlan_result['NewSSID']], wlan_status)
    obj_ref.metrics['wlanchannel'].add_metric([device.serial, device.friendly_name, wlan_enabled, wlan_result['NewStandard'],
                                              wlan_result['NewSSID']], wlan_result['NewChannel'])
    assoc_results = device.fc.call_action(f'WLANConfiguration{index}', 'GetTotalAssociations')
    obj_ref.metrics['wlanassocs'].add_metric([device.serial, device.friendly_name, wlan_enabled, wlan_result['NewStandard'],
                                             wlan_result['NewSSID']], assoc_results['NewTotalAssociations'])

    packet_stats_result = device.fc.call_action(f'WLANConfiguration{index}', 'GetPacketStatistics')
    obj_ref.metrics['wlanpackets'].add_metric([device.serial, device.friendly_name, wlan_enabled, wlan_result['NewStandard'], wlan_result['NewSSID'], 'rx'],
                                              packet_stats_result['NewTotalPacketsReceived'])
    obj_ref.metrics['wlanpackets'].add_metric([device.serial, device.friendly_name, wlan_enabled, wlan_result['NewStandard'], wlan_result['NewSSID'], 'tx'],
                                              packet_stats_result['NewTotalPacketsSent'])

    yield obj_ref.metrics['wlanstatus']
    yield obj_ref.metrics['wlanchannel']
    yield obj_ref.metrics['wlanassocs']
    yield obj_ref.metrics['wlanpackets']


# The standard specifies, that there may be 5 WiFi networks here.
# This still feels "wrong" to hardcode 5 WiFi objects, but fixing this may
# require a more in depth approach to how capabilities are organized/checked.
# An object callback in check capabilities may be the way out.

class WlanConfigurationInfo1(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        wlanConsructorFactory(self, 1)
        self.wifi_type = '2.4GHz'

    def createMetrics(self):
        wlanCreateMetricsFactory(self)

    def _getMetricValues(self, device):
        yield from wlanGetMetricsFactory(self, 1, device)


class WlanConfigurationInfo2(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        wlanConsructorFactory(self, 2)
        self.wifi_type = '5GHz'

    def createMetrics(self):
        wlanCreateMetricsFactory(self)

    def _getMetricValues(self, device):
        yield from wlanGetMetricsFactory(self, 2, device)


class WlanConfigurationInfo3(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        wlanConsructorFactory(self, 3)
        self.wifi_type = 'guest'

    def createMetrics(self):
        wlanCreateMetricsFactory(self)

    def _getMetricValues(self, device):
        yield from wlanGetMetricsFactory(self, 3, device)


class WlanConfigurationInfo4(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        wlanConsructorFactory(self, 4)
        self.wifi_type = 'unkown1'

    def createMetrics(self):
        wlanCreateMetricsFactory(self)

    def _getMetricValues(self, device):
        yield from wlanGetMetricsFactory(self, 4, device)


class WlanConfigurationInfo5(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        wlanConsructorFactory(self, 5)
        self.wifi_type = 'unkown2'

    def createMetrics(self):
        wlanCreateMetricsFactory(self)

    def _getMetricValues(self, device):
        yield from wlanGetMetricsFactory(self, 5, device)
