import logging
from unittest.mock import MagicMock, call, patch

from fritzexporter.fritzdevice import FritzDevice

from .fc_services_mock import fc_services_fb7590, create_fc_services


def call_action_mock(service, action, **kwargs):
    MODEL_NAME = "Fritz!MockBox 9790"
    HOSTSVC = "Hosts1"
    WLANSVC = "WLANConfiguration"
    WANCICSVC = "WANCommonInterfaceConfig1"

    call_action_responses = {
        ("DeviceInfo1", "GetInfo"): {
            "NewSerialNumber": "1234567890",
            "NewModelName": MODEL_NAME,
            "NewSoftwareVersion": "1.2",
            "NewUptime": 1234,
        },
        (HOSTSVC, "GetHostNumberOfEntries"): {"NewHostNumberOfEntries": 3},
        ("UserInterface1", "GetInfo"): {
            "NewUpgradeAvailable": 1,
            "NewX_AVM-DE_Version": "1.3",
        },
        ("LANEthernetInterfaceConfig1", "GetInfo"): {
            "NewEnable": 1,
            "NewStatus": "Up",
        },
        ("LANEthernetInterfaceConfig1", "GetStatistics"): {
            "NewBytesReceived": 1234,
            "NewBytesSent": 9876,
            "NewPacketsReceived": 123,
            "NewPacketsSent": 987,
        },
        ("WANDSLInterfaceConfig1", "GetInfo"): {
            "NewEnable": 1,
            "NewStatus": "Up",
            "NewUpstreamCurrRate": 500,
            "NewDownstreamCurrRate": 100,
            "NewUpstreamMaxRate": 567,
            "NewDownstreamMaxRate": 123,
            "NewUpstreamNoiseMargin": 56,
            "NewDownstreamNoiseMargin": 67,
            "NewUpstreamAttenuation": 12,
            "NewDownstreamAttenuation": 23,
        },
        ("WANDSLInterfaceConfig1", "X_AVM-DE_GetDSLInfo"): {
            "NewFECErrors": 12,
            "NewCRCErrors": 23,
        },
        ("WANPPPConnection1", "GetStatusInfo"): {
            "NewConnectionStatus": "Connected",
            "NewUptime": 12345,
            "NewLastConnectionError": "Timeout",
        },
        (WANCICSVC, "GetCommonLinkProperties"): {
            "NewWANAccessType": "PPPoE",
            "NewLayer1UpstreamMaxBitRate": 10000,
            "NewLayer1DownstreamMaxBitRate": 10001,
            "NewPhysicalLinkStatus": "Up",
        },
        (WANCICSVC, "GetTotalBytesReceived"): {"NewTotalBytesReceived": 1234567},
        (WANCICSVC, "GetTotalBytesSent"): {"NewTotalBytesSent": 234567},
        ("WANCommonIFC1", "GetAddonInfos"): {
            "NewByteReceiveRate": 12345,
            "NewByteSendRate": 23456,
        },
        (WANCICSVC, "GetTotalPacketsReceived"): {"NewTotalPacketsReceived": 12345},
        (WANCICSVC, "GetTotalPacketsSent"): {"NewTotalPacketsSent": 2345},
        (f"{WLANSVC}1", "GetInfo"): {
            "NewStatus": "Up",
            "NewEnable": 1,
            "NewStandard": "802.11xe",
            "NewSSID": "SomeSSID-1",
            "NewChannel": "42",
        },
        (f"{WLANSVC}1", "GetTotalAssociations"): {"NewTotalAssociations": 56},
        (f"{WLANSVC}1", "GetPacketStatistics"): {
            "NewTotalPacketsReceived": 123456,
            "NewTotalPacketsSent": 2345,
        },
        (f"{WLANSVC}2", "GetInfo"): {
            "NewStatus": "Up",
            "NewEnable": 1,
            "NewStandard": "802.11xe2",
            "NewSSID": "SomeSSID-2",
            "NewChannel": "23",
        },
        (f"{WLANSVC}2", "GetTotalAssociations"): {"NewTotalAssociations": 43},
        (f"{WLANSVC}2", "GetPacketStatistics"): {
            "NewTotalPacketsReceived": 1234560,
            "NewTotalPacketsSent": 23450,
        },
        (f"{WLANSVC}3", "GetInfo"): {
            "NewStatus": "Up",
            "NewEnable": 1,
            "NewStandard": "802.11xe3",
            "NewSSID": "SomeSSID-3",
            "NewChannel": "69",
        },
        (f"{WLANSVC}3", "GetTotalAssociations"): {"NewTotalAssociations": 82},
        (f"{WLANSVC}3", "GetPacketStatistics"): {
            "NewTotalPacketsReceived": 1234561,
            "NewTotalPacketsSent": 23451,
        },
        (HOSTSVC, "GetGenericHostEntry"): {
            "NewIPAddress": "192.168.178.42",
            "NewMACAddress": "01:02:03:04:05:06",
            "NewHostName": "generichost",
            "NewActive": 1,
        },
        (HOSTSVC, "X_AVM-DE_GetSpecificHostEntryByIP"): {
            "NewInterfaceType": "eth",
            "NewX_AVM-DE_Port": "LAN1",
            "NewX_AVM-DE_Model": "Mockgear",
            "NewX_AVM-DE_Speed": 1000,
        },
        ("WANCommonIFC1", "GetAddonInfos"): {},
    }

    return call_action_responses[(service, action)]


@patch("fritzexporter.fritzdevice.FritzConnection")
class TestFritzDevice:
    def test_should_create_fritzconnection_and_complain_about_password(
        self, mock_fritzconnection: MagicMock, caplog
    ):

        caplog.set_level(logging.DEBUG)
        password: str = "123456789012345678901234567890123"

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_action_mock
        fc.services = create_fc_services(fc_services_fb7590)

        fd = FritzDevice("somehost", "someuser", password, "Fritz!Mock", False)

        assert (
            "fritzexporter.fritzdevice",
            logging.WARN,
            "Password is longer than 32 characters! Login may not succeed, please see README!",
        ) in caplog.record_tuples

        assert fd.model == "Fritz!MockBox 9790"
        assert fd.serial == "1234567890"

        assert mock_fritzconnection.call_count == 1
        assert mock_fritzconnection.call_args == call(
            address="somehost", user="someuser", password=password
        )
