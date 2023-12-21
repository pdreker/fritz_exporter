from typing import NamedTuple


class BlacklistItem(NamedTuple):
    service: str
    action: str
    return_value: str | None = None


call_blacklist = [
    BlacklistItem("DeviceConfig1", "GetPersistentData"),
    BlacklistItem("DeviceConfig1", "X_AVM-DE_GetConfigFile"),
    BlacklistItem("Hosts1", "X_AVM-DE_GetAutoWakeOnLANByMACAddress"),
    BlacklistItem("WANCommonInterfaceConfig1", "X_AVM-DE_GetOnlineMonitor"),
    BlacklistItem("WLANConfiguration1", "GetDefaultWEPKeyIndex"),
    BlacklistItem("WLANConfiguration2", "GetDefaultWEPKeyIndex"),
    BlacklistItem("WLANConfiguration3", "GetDefaultWEPKeyIndex"),
    BlacklistItem("WLANConfiguration4", "GetDefaultWEPKeyIndex"),
    BlacklistItem("X_AVM-DE_AppSetup1", "GetAppMessageFilter"),
    BlacklistItem("X_AVM-DE_Filelinks1", "GetNumberOfFilelinkEntries"),
    BlacklistItem("X_AVM-DE_HostFilter1", "GetTicketIDStatus"),
    BlacklistItem("X_AVM-DE_OnTel1", "GetCallBarringEntry"),
    BlacklistItem("X_AVM-DE_OnTel1", "GetCallBarringEntryByNum"),
    BlacklistItem("X_AVM-DE_OnTel1", "GetDeflection"),
    BlacklistItem("X_AVM-DE_OnTel1", "GetPhonebook"),
    BlacklistItem("X_AVM-DE_OnTel1", "GetPhonebookEntry"),
    BlacklistItem("X_AVM-DE_OnTel1", "GetPhonebookEntryUID"),
    BlacklistItem("X_AVM-DE_TAM1", "GetInfo"),
    BlacklistItem("X_VoIP1", "GetVoIPEnableAreaCode"),
    BlacklistItem("X_VoIP1", "GetVoIPEnableCountryCode"),
    BlacklistItem("X_VoIP1", "X_AVM-DE_GetClient"),
    BlacklistItem("X_VoIP1", "X_AVM-DE_GetClient2"),
    BlacklistItem("X_VoIP1", "X_AVM-DE_GetClient3"),
    BlacklistItem("X_VoIP1", "X_AVM-DE_GetClientByClientId"),
    BlacklistItem("X_VoIP1", "X_AVM-DE_GetPhonePort"),
    BlacklistItem("X_VoIP1", "X_AVM-DE_GetVoIPAccount"),
]
