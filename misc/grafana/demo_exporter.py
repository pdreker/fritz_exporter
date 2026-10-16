"""Demo exporter: the real collector against fake devices whose values move over time.

    uv run python misc/grafana/demo_exporter.py [port]   # default 9788, from the repo root

Serves a cable box ("Demo Cable") and a fibre box ("Demo Fiber") with smart home
devices, built from the test fixtures, so the dashboard panels for hardware you do
not have show data. Listens on 127.0.0.1 only.
"""

import copy
import json
import math
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# The repository root, for fritzexporter and the test fixtures.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY

from fritzexporter.fritzdevice import DeviceSpec, FritzCollector, FritzCredentials
from tests.fc_services_mock import (
    call_action_mock,
    create_fc_services,
    fc_services_capabilities,
)
from tests.test_fritz_docsis import DOCSIS_RAW
from tests.test_fritz_rest_api import CONNECTIONS_RAW, SEGMENT_RAW
from tests.web_replies import web_reply

START = time.time()

HOSTS = ["laptop", "phone", "tv", "nas", "printer", "watch"]
# WiFi clients per WLANConfiguration (2.4 GHz, 5 GHz, guest).
WIFI_CLIENTS = {1: 4, 2: 7, 3: 1}

# Counter-like TR-064 fields grow, everything else numeric wobbles.
GROWING = ("Total", "Bytes", "Packets", "Errors", "UpTime", "Uptime", "FEC", "CRC", "Discard")


def _elapsed() -> float:
    return time.time() - START


def _wobble(value: float, seed: str, amount: float = 0.15) -> float:
    phase = (hash(seed) % 1000) / 1000 * 2 * math.pi
    return value * (1 + amount * math.sin(_elapsed() / 120 + phase))


def _move(key: str, value: object, seed: str) -> object:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    if any(word in key for word in GROWING):
        rate = max(abs(value), 1) / 600
        return type(value)(value + rate * _elapsed() * (1 + 0.5 * math.sin(_elapsed() / 90)))
    if value in (0, 1):
        # Flags and states: a change would make a new series (e.g. "enabled" label).
        return value
    return type(value)(_wobble(value, seed + key))


def _argument(kwargs: dict[str, Any], name: str) -> int:
    """An index argument, given as keyword (reads) or in ``arguments`` (probes)."""
    return int(kwargs.get(name, (kwargs.get("arguments") or {}).get(name, 0)))


def _demo_reply(service: str, action: str, kwargs: dict[str, Any]) -> dict | None:
    """Replies the test mock does not have: hosts, WiFi clients, the mesh list path."""
    wlan = int(service.removeprefix("WLANConfiguration")) if "WLANConfiguration" in service else 0
    if (service, action) == ("Hosts1", "X_AVM-DE_GetMeshListPath"):
        return {"NewX_AVM-DE_MeshListPath": "/meshlist.lua"}
    if (service, action) == ("Hosts1", "GetHostNumberOfEntries"):
        return {"NewHostNumberOfEntries": len(HOSTS)}
    if (service, action) == ("Hosts1", "GetGenericHostEntry"):
        index = _argument(kwargs, "NewIndex")
        return {
            "NewIPAddress": f"192.168.178.{20 + index}",
            "NewMACAddress": f"AA:BB:CC:00:00:{index:02X}",
            "NewHostName": HOSTS[index],
            "NewActive": int(HOSTS[index] != "printer"),
        }
    if action == "GetTotalAssociations":
        return {"NewTotalAssociations": WIFI_CLIENTS.get(wlan, 0)}
    if action == "GetGenericAssociatedDeviceInfo":
        client = _argument(kwargs, "NewAssociatedDeviceIndex")
        return {
            "NewAssociatedDeviceMACAddress": f"02:00:0{wlan}:00:00:{client:02X}",
            "NewAssociatedDeviceIPAddress": f"192.168.178.{100 + 10 * wlan + client}",
            "NewX_AVM-DE_SignalStrength": int(_wobble(90 - 9 * client, f"{wlan}{client}", 0.1)),
            "NewX_AVM-DE_Speed": [144, 866, 72][wlan - 1] // (client + 1),
        }
    return None


def _actions(serial: str, model: str, access_type: str) -> Callable[..., object]:
    def call_action(service: str, action: str, **kwargs: Any) -> object:  # noqa: ANN401
        demo = _demo_reply(service, action, kwargs)
        if demo is not None:
            return demo
        result = copy.deepcopy(call_action_mock(service, action, **kwargs))
        if (service, action) == ("DeviceInfo1", "GetInfo"):
            result.update(
                {"NewSerialNumber": serial, "NewModelName": model, "NewSoftwareVersion": "8.25"}
            )
        if action == "GetCommonLinkProperties":
            result["NewWANAccessType"] = access_type
            # A 1000/50 Mbit/s line, with traffic to match below.
            result["NewLayer1DownstreamMaxBitRate"] = 1_000_000_000
            result["NewLayer1UpstreamMaxBitRate"] = 50_000_000
            return result
        if (service, action) == ("WANCommonIFC1", "GetAddonInfos"):
            result["NewByteReceiveRate"] = int(abs(_wobble(30e6, "down", 0.9)))
            result["NewByteSendRate"] = int(abs(_wobble(2e6, "up", 0.8)))
            result["NewX_AVM_DE_Layer1DownstreamMaxBitRate64"] = 1_000_000_000
            result["NewX_AVM_DE_Layer1UpstreamMaxBitRate64"] = 50_000_000
            return result
        if isinstance(result, dict):
            return {k: _move(k, v, f"{serial}{service}{action}") for k, v in result.items()}
        return result

    return call_action


def _docsis() -> dict:
    raw: Any = copy.deepcopy(DOCSIS_RAW)
    for direction in ("channelDs", "channelUs"):
        for standard in raw["data"].get(direction, {}).values():
            for ch in standard:
                for key in ("powerLevel", "mer", "mse"):
                    if key in ch:
                        ch[key] = f"{_wobble(float(ch[key]), str(ch['channelID']) + key, 0.05):.1f}"
                for key in ("corrErrors", "nonCorrErrors"):
                    if key in ch:
                        ch[key] = int(ch[key] + _elapsed() * (5 if key == "corrErrors" else 0.01))
    return raw


def _segment() -> dict:
    raw: Any = copy.deepcopy(SEGMENT_RAW)
    raw["lastSampleTime"] = int(time.time()) // 60 * 60
    for series in raw["data"]:
        for key in ("downstream", "upstream"):
            series[key][-1] = round(_wobble(series[key][-1] + 10, series["type"] + key, 0.6), 2)
    return raw


def _aha() -> str:
    devices = [
        ("Living room", "087610000001", True, True),
        ("Bedroom", "087610000002", True, False),
        ("Washing machine", "116570000003", False, True),
    ]
    parts = []
    for name, ain, heater, plug in devices:
        celsius = int(_wobble(215, name, 0.08))
        product = "FRITZ!DECT 301" if heater else "FRITZ!DECT 200"
        xml = [
            (
                f'<device identifier="{ain}" id="{ain[-2:]}" functionbitmask="1" '
                f'fwversion="5.0" manufacturer="AVM" productname="{product}">'
            ),
            "<present>1</present>",
            f"<name>{name}</name>",
            f"<temperature><celsius>{celsius}</celsius><offset>0</offset></temperature>",
        ]
        if heater:
            xml.append("<battery>80</battery><batterylow>0</batterylow>")
            xml.append(
                f"<hkr><tist>{celsius // 5}</tist><tsoll>42</tsoll>"
                "<absenk>34</absenk><komfort>42</komfort></hkr>"
            )
        if plug:
            power = int(_wobble(45000, name, 0.9))
            energy = int(12345 + _elapsed() / 10)
            xml.append(f"<powermeter><power>{power}</power><energy>{energy}</energy></powermeter>")
            xml.append("<switch><state>1</state><mode>manuell</mode><lock>0</lock></switch>")
        xml.append("</device>")
        parts.append("".join(xml))
    return f'<devicelist version="1">{"".join(parts)}</devicelist>'


def _mesh_topology() -> dict:
    """The box and one repeater, linked on 2.4 and 5 GHz."""

    def link(uid: str, rate: int, top: int) -> dict:
        return {
            "uid": uid,
            "node_1_uid": "box",
            "node_2_uid": "repeater",
            "state": "CONNECTED",
            "cur_data_rate_rx": int(_wobble(rate, uid + "rx", 0.2)),
            "cur_data_rate_tx": int(_wobble(rate * 0.8, uid + "tx", 0.2)),
            "max_data_rate_rx": top,
            "max_data_rate_tx": top,
        }

    return {
        "nodes": [
            {
                "uid": "box",
                "device_name": "fritz.box",
                "is_meshed": True,
                "node_interfaces": [
                    {"type": "WLAN", "name": "AP:2G:0", "node_links": [link("l1", 180000, 286000)]},
                    {
                        "type": "WLAN",
                        "name": "AP:5G:0",
                        "node_links": [link("l2", 1100000, 2400000)],
                    },
                ],
            },
            {
                "uid": "repeater",
                "device_name": "Repeater",
                "is_meshed": True,
                "node_interfaces": [],
            },
        ]
    }


def _webui() -> MagicMock:
    client = MagicMock()
    client.fetch_aha.side_effect = lambda *_a, **_k: _aha()
    client.fetch_page.side_effect = lambda page: (
        _docsis()
        if page == "docInfo"
        else {
            "data": {
                "opt_ds": [["sa", 9, int(_wobble(-65, "ds", 0.1))]],
                "opt_us": [["sa", 9, int(_wobble(-54, "us", 0.1))]],
            }
        }
    )
    client.fetch_api.side_effect = lambda path: _segment() if "segment" in path else CONNECTIONS_RAW
    return client


def demo_collector() -> FritzCollector:
    """The collector with both demo boxes set up."""
    services = {}
    for caps in fc_services_capabilities.values():
        for service, actions in caps.items():
            services.setdefault(service, []).extend(
                a for a in actions if a not in services.get(service, [])
            )

    boxes = [
        ("demo-cable", "Demo Cable", "DEMOCABLE01", "FRITZ!Box 6690 Cable", "X_AVM-DE_Cable"),
        ("demo-fiber", "Demo Fiber", "DEMOFIBER01", "FRITZ!Box 5590 Fiber", "X_AVM-DE_Fiber"),
    ]
    services.setdefault("Hosts1", []).append("X_AVM-DE_GetMeshListPath")
    fcs = {}
    for host, _, serial, model, access in boxes:
        fc = MagicMock()
        fc.call_action.side_effect = _actions(serial, model, access)
        fc.call_http.return_value = {"content": "", "encoding": "utf-8"}
        fc.services = create_fc_services(services)
        fc.session.get.side_effect = lambda *_a, **_k: web_reply(json.dumps(_mesh_topology()))
        fcs[host] = fc

    collector = FritzCollector()
    with patch.multiple(
        "fritzexporter.fritzdevice",
        create_fritz_connection=lambda **kw: fcs[kw["address"]],
        FritzWebUiClient=MagicMock(side_effect=lambda *_a, **_k: _webui()),
    ):
        for host, name, *_ in boxes:
            collector.add_device(
                DeviceSpec(
                    FritzCredentials(host, "demo", "demo"),
                    name,
                    host_info=True,
                    wifi_client_info=True,
                )
            )
    return collector


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9788
    REGISTRY.register(demo_collector())
    start_http_server(port, "127.0.0.1")
    print(f"demo exporter on 127.0.0.1:{port}", flush=True)  # noqa: T201
    threading.Event().wait()


if __name__ == "__main__":
    main()
