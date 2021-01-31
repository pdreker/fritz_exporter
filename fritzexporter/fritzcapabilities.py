import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class FritzCapability(ABC):
    capabilities = []

    def __init__(self) -> None:
        self.present = False
        self.requirements = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        FritzCapability.capabilities.append(cls)

    def checkCapability(self, device):
        self.present = all([ (service in device.fc.services) and (action in device.fc.services[service].actions) for (service, action) in self.requirements ])
        logger.debug(f'Capability {type(self).__name__} set to {self.present} on device {device.host}')

    #@abstractmethod
    #def getMetrics(self):
    #    pass

class FritzCapabilities():
    def __init__(self, device=None) -> None:
        self.capabilities = { capability.__name__:capability() for capability in FritzCapability.capabilities }
        if device:
           self.checkPresent(device)

    def __iter__(self):
        return iter(self.capabilities)

    def __len__(self):
        return len(self.capabilities)

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

class HostNumberOfEntries(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('Hosts1', 'GetHostNumberOfEntries'))
        
class HostGetGenericEntry(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('Hosts1', 'GetGenericHostEntry'))
        
class UserInterface(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('UserInterface1', 'GetInfo'))
        
class LanInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('LANEthernetInterfaceConfig1', 'GetInfo'))
        
class LanInterfaceConfigStatistocs(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('LANEthernetInterfaceConfig1', 'GetStatistics'))
        
class WanDSLInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANDSLInterfaceConfig1', 'GetInfo'))
        
class WanPPPConnectionStatus(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANPPPConnection1', 'GetStatusInfo'))
        
class WanCommonInterfaceConfig(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetCommonLinkProperties'))

class WanCommonInterfaceDataBytes(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalBytesReceived'))
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalBytesSent'))

class WanCommonInterfaceDataPackets(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalPacketsReceived'))
        self.requirements.append(('WANCommonInterfaceConfig1', 'GetTotalPacketsSent'))

class WlanConfigurationInfo(FritzCapability):
    def __init__(self) -> None:
        super().__init__()
        self.requirements.append(('WLANConfiguration1', 'GetInfo'))

