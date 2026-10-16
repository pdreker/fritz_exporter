"""Demo exporter: the real collector against fake devices whose values move over time.

    uv run python misc/grafana/demo_exporter.py [port]   # default 9788, from the repo root

Serves a cable box ("Demo Cable") and a fibre box ("Demo Fiber") with smart home
devices, built from the test fixtures, so the dashboard panels for hardware you do
not have show data. Listens on 127.0.0.1 only.
"""

import copy
import math
import sys
import threading
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

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

START = time.time()

HOSTS = ["laptop", "phone", "tv", "nas", "printer", "watch"]

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
    if value == 0:
        return value
    return type(value)(_wobble(value, seed + key))


def _actions(serial: str, model: str, access_type: str) -> Callable[..., object]:
    def call_action(service: str, action: str, **kwargs: object) -> object:
        result = copy.deepcopy(call_action_mock(service, action, **kwargs))
        if (service, action) == ("DeviceInfo1", "GetInfo"):
            result.update(
                {"NewSerialNumber": serial, "NewModelName": model, "NewSoftwareVersion": "8.25"}
            )
        if action == "GetCommonLinkProperties":
            result["NewWANAccessType"] = access_type
        if (service, action) == ("Hosts1", "GetHostNumberOfEntries"):
            result["NewHostNumberOfEntries"] = len(HOSTS)
        if (service, action) == ("Hosts1", "GetGenericHostEntry"):
            arguments: Any = kwargs.get("arguments") or {}
            index: int = kwargs.get("NewIndex", arguments.get("NewIndex", 0))  # ty: ignore[invalid-assignment]
            return {
                "NewIPAddress": f"192.168.178.{20 + index}",
                "NewMACAddress": f"AA:BB:CC:00:00:{index:02X}",
                "NewHostName": HOSTS[index],
                "NewActive": int(HOSTS[index] != "printer"),
            }
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


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9788
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
    fcs = {}
    for host, _, serial, model, access in boxes:
        fc = MagicMock()
        fc.call_action.side_effect = _actions(serial, model, access)
        fc.call_http.return_value = {"content": "", "encoding": "utf-8"}
        fc.services = create_fc_services(services)
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
    REGISTRY.register(collector)
    start_http_server(port, "127.0.0.1")
    print(f"demo exporter on 127.0.0.1:{port}", flush=True)  # noqa: T201
    threading.Event().wait()


if __name__ == "__main__":
    main()
