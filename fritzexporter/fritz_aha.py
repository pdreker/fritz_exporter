from defusedxml import ElementTree


def parse_aha_device_xml(deviceinfo: str) -> dict[str, str]:
    device: ElementTree = ElementTree.fromstring(deviceinfo)

    battery_level = device.find("battery").text
    battery_low = device.find("batterylow").text

    result = {}
    if battery_level:
        result["battery_level"] = battery_level
    if battery_low:
        result["battery_low"] = battery_low

    return result
