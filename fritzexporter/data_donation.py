from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzArgumentError,
    FritzConnectionException,
    FritzServiceError,
)

from . import __version__
from .action_blacklists import BlacklistItem, call_blacklist
from .fritzdevice import FritzDevice

logger = logging.getLogger("fritzexporter.donate_data")

_SANITIZED = "<SANITIZED>"

_SANITIZATION_BLACKLIST: dict[tuple[str, str], list[str]] = {
    ("DeviceConfig1", "GetPersistentData"): ["NewPersistentData"],
    ("DeviceConfig1", "X_AVM-DE_GetConfigFile"): ["NewConfigFile"],
    ("DeviceInfo1", "GetDeviceLog"): ["NewDeviceLog"],
    ("DeviceConfig1", "X_AVM-DE_GetSupportDataInfo"): ["NewX_AVM-DE_SupportDataID"],
    ("DeviceInfo1", "GetInfo"): ["NewDeviceLog", "NewProvisioningCode", "NewSerialNumber"],
    ("DeviceInfo1", "GetSecurityPort"): ["NewSecurityPort"],
    ("Hosts1", "X_AVM-DE_GetHostListPath"): ["NewX_AVM-DE_HostListPath"],
    ("Hosts1", "X_AVM-DE_GetMeshListPath"): ["NewX_AVM-DE_MeshListPath"],
    ("LANConfigSecurity1", "X_AVM-DE_GetCurrentUser"): [
        "NewX_AVM-DE_CurrentUserRights",
        "NewX_AVM-DE_CurrentUsername",
    ],
    ("LANConfigSecurity1", "X_AVM-DE_GetUserList"): ["NewX_AVM-DE_UserList"],
    ("LANEthernetInterfaceConfig1", "GetInfo"): ["NewMACAddress"],
    ("LANHostConfigManagement1", "GetAddressRange"): ["NewMaxAddress", "NewMinAddress"],
    ("LANHostConfigManagement1", "GetDNSServers"): ["NewDNSServers"],
    ("LANHostConfigManagement1", "GetIPRoutersList"): ["NewIPRouters"],
    ("LANHostConfigManagement1", "GetInfo"): [
        "NewDNSServers",
        "NewIPRouters",
        "NewMaxAddress",
        "NewMinAddress",
    ],
    ("ManagementServer1", "GetInfo"): ["NewUsername", "NewConnectionRequestURL"],
    ("Time1", "GetInfo"): ["NewNTPServer1", "NewNTPServer2"],
    ("WANCommonIFC1", "GetAddonInfos"): [
        "NewDNSServer1",
        "NewDNSServer2",
        "NewVoipDNSServer1",
        "NewVoipDNSServer2",
    ],
    ("WANIPConn1", "GetExternalIPAddress"): ["NewExternalIPAddress"],
    ("WANIPConn1", "X_AVM_DE_GetDNSServer"): ["NewIPv4DNSServer1", "NewIPv4DNSServer2"],
    ("WANIPConn1", "X_AVM_DE_GetExternalIPv6Address"): ["NewExternalIPv6Address"],
    ("WANIPConn1", "X_AVM_DE_GetIPv6DNSServer"): ["NewIPv6DNSServer1", "NewIPv6DNSServer2"],
    ("WANIPConn1", "X_AVM_DE_GetIPv6Prefix"): ["NewIPv6Prefix"],
    ("WANIPConnection1", "GetExternalIPAddress"): ["NewExternalIPAddress"],
    ("WANIPConnection1", "GetInfo"): ["NewDNSServers", "NewMACAddress", "NewExternalIPAddress"],
    ("WANIPConnection1", "X_GetDNSServers"): ["NewDNSServers"],
    ("WANPPPConnection1", "GetExternalIPAddress"): ["NewExternalIPAddress"],
    ("WANPPPConnection1", "GetInfo"): [
        "NewDNSServers",
        "NewExternalIPAddress",
        "NewMACAddress",
        "NewUserName",
    ],
    ("WANPPPConnection1", "GetUserName"): ["NewUserName"],
    ("WANPPPConnection1", "X_GetDNSServers"): ["NewDNSServers"],
    **{
        (f"WLANConfiguration{i}", action): fields
        for i in range(1, 5)
        for action, fields in [
            ("GetBSSID", ["NewBSSID"]),
            ("GetInfo", ["NewBSSID", "NewSSID"]),
            ("GetSSID", ["NewSSID"]),
            ("GetSecurityKeys", ["NewKeyPassphrase", "NewPreSharedKey", "NewWEPKey0", "NewWEPKey1", "NewWEPKey2", "NewWEPKey3"]),
            ("X_AVM-DE_GetWLANDeviceListPath", ["NewX_AVM-DE_WLANDeviceListPath"]),
            ("X_AVM-DE_GetWLANHybridMode", ["NewBSSID", "NewSSID"]),
        ]
    },
    ("X_AVM-DE_AppSetup1", "GetAppRemoteInfo"): [
        "NewExternalIPAddress",
        "NewExternalIPv6Address",
        "NewIPAddress",
        "NewMyFritzDynDNSName",
        "NewRemoteAccessDDNSDomain",
    ],
    ("X_AVM-DE_Dect1", "GetDectListPath"): ["NewDectListPath"],
    ("X_AVM-DE_Filelinks1", "GetFilelinkListPath"): ["NewFilelinkListPath"],
    ("X_AVM-DE_MyFritz1", "GetInfo"): ["NewDynDNSName", "NewPort"],
    ("X_AVM-DE_OnTel1", "GetCallBarringList"): ["NewPhonebookURL"],
    ("X_AVM-DE_OnTel1", "GetCallList"): ["NewCallListURL"],
    ("X_AVM-DE_OnTel1", "GetDECTHandsetList"): ["NewDectIDList"],
    ("X_AVM-DE_OnTel1", "GetDeflections"): ["NewDeflectionList"],
    ("X_AVM-DE_RemoteAccess1", "GetDDNSInfo"): ["NewDomain", "NewUpdateURL", "NewUsername"],
    ("X_AVM-DE_RemoteAccess1", "GetInfo"): ["NewPort", "NewUsername"],
    ("X_AVM-DE_Storage1", "GetUserInfo"): ["NewUsername"],
    ("X_AVM-DE_TAM1", "GetList"): ["NewTAMList"],
    ("X_VoIP1", "X_AVM-DE_GetClients"): ["NewX_AVM-DE_ClientList"],
    ("X_VoIP1", "X_AVM-DE_GetNumbers"): ["NewNumberList"],
}


def get_sw_version(device: FritzDevice) -> str:
    try:
        info_result = device.fc.call_action("DeviceInfo1", "GetInfo")
    except FritzServiceError as e:
        return f"ERROR - FritzServiceError: {e}"
    except FritzActionError as e:
        return f"ERROR - FritzActionError: {e}"

    return info_result["NewSoftwareVersion"]


def safe_call_action(device: FritzDevice, service: str, action: str) -> dict[str, str]:
    if BlacklistItem(service, action) in call_blacklist:
        return {"error": "<BLACKLISTED>"}

    try:
        result = device.fc.call_action(service, action)
    except (FritzServiceError, FritzActionError, FritzArgumentError, FritzConnectionException) as e:
        result = {"error": f"{e}"}
    return result


def _apply_builtin_sanitization(res: dict[tuple[str, str], dict]) -> None:
    for svc_action, svc_data in res.items():
        if svc_action in _SANITIZATION_BLACKLIST:
            for field in _SANITIZATION_BLACKLIST[svc_action]:
                if field in svc_data:
                    svc_data[field] = _SANITIZED


def _apply_custom_sanitization(res: dict[tuple[str, str], dict], sanitation: list[list]) -> None:
    for entry in sanitation:
        svc_action = (entry[0], entry[1])
        if svc_action not in res:
            continue
        if len(entry) == 2:  # noqa: PLR2004
            for field in res[svc_action]:
                res[svc_action][field] = _SANITIZED
        elif len(entry) == 3 and entry[2] in res[svc_action]:  # noqa: PLR2004
            res[svc_action][entry[2]] = _SANITIZED


def sanitize_results(
    res: dict[tuple[str, str], dict], sanitation: list[list]
) -> dict[tuple[str, str], dict[str, Any]]:
    _apply_builtin_sanitization(res)
    _apply_custom_sanitization(res, sanitation)
    return res


def jsonify_action_results(ar: dict[tuple[str, str], dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for (service, action), action_data in ar.items():
        if service not in out:
            out[service] = {}
        if action not in out[service]:
            out[service][action] = {k: str(v) for k, v in action_data.items()}
    return out


def upload_data(basedata) -> None:  # noqa: ANN001
    donation_url = os.getenv("FRITZ_DONATION_URL", "https://fritz.dreker.de/data/donate")
    headers = {"Content-Type": "application/json"}
    resp = requests.post(donation_url, data=json.dumps(basedata), headers=headers, timeout=10)

    if resp.status_code == requests.codes.ok:
        donation_id = resp.json().get("donation_id")
        if donation_id:
            logger.info(
                "Data donation for device %s registered under id %s",
                basedata["fritzdevice"]["model"],
                donation_id,
            )
        else:
            logger.warning(
                "Data donation for device  %s did not return a donation id.",
                basedata["fritzdevice"]["model"],
            )
    else:
        resp.raise_for_status()


def donate_data(
    device: FritzDevice, *, upload: bool = False, sanitation: list[list] | None = None
) -> None:
    if not sanitation:
        sanitation = []
    services = {s: list(device.fc.services[s].actions) for s in device.fc.services}
    model = device.model
    sw_version = get_sw_version(device)

    detected_capabilities = list(device.capabilities.capabilities)

    action_results = {}
    for service, actions in services.items():
        for action in actions:
            if action.startswith("Get") and not (
                "ByIP" in action
                or "ByIndex" in action
                or action.startswith("GetSpecific")
                or action.startswith("GetGeneric")
            ):
                res = safe_call_action(device, service, action)
                action_results[(service, action)] = res

    basedata = {
        "exporter_version": __version__,
        "fritzdevice": {
            "model": model,
            "os_version": sw_version,
            "services": services,
            "detected_capabilities": detected_capabilities,
            "action_results": jsonify_action_results(sanitize_results(action_results, sanitation)),
        },
    }

    if upload:
        upload_data(basedata)
    else:
        print(f"---------------- Donation data for device {model} ---------------------")  # noqa: T201
        print(json.dumps(basedata, indent=2))  # noqa: T201
        print("----------------- END ------------------")  # noqa: T201
        print()  # noqa: T201

# Copyright 2019-2026 Patrick Dreker <patrick@dreker.de>
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
