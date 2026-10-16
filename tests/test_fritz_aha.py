"""Tests for the smart home device list parser (fritz_aha.py)."""

import pytest

from fritzexporter.fritz_aha import parse_aha_devicelist_xml
from fritzexporter.reply import DataError, Issues


class TestParseAhaDevicelistXml:
    def test_should_raise_data_error_on_parse_error(self):
        issues = Issues()
        with pytest.raises(DataError, match="not valid XML"):
            parse_aha_devicelist_xml("this is not valid xml <<<", issues)

    def test_should_raise_data_error_without_a_devicelist(self):
        issues = Issues()
        with pytest.raises(DataError, match="no <devicelist> element"):
            parse_aha_devicelist_xml("<html><body>login</body></html>", issues)

    def test_should_raise_data_error_on_forbidden_xml(self):
        # defusedxml refuses entities; that is bad device data, not a bug.
        issues = Issues()
        with pytest.raises(DataError, match="not valid XML"):
            parse_aha_devicelist_xml('<!DOCTYPE x [<!ENTITY a "b">]><devicelist>&a;</devicelist>', issues)

    def test_should_record_non_numeric_int_fields_and_skip_the_value(self):
        # Prepare
        deviceinfo = """<?xml version="1.0" encoding="utf-8"?>
        <devicelist version="1">
        <device identifier="123456789012" id="123" functionbitmask="1"
                fwversion="1.2" manufacturer="AVM" productname="Fritz!DECT 200">
            <present>1</present>
            <name>Fritz!DECT 200</name>
            <battery>not-a-number</battery>
        </device>
        </devicelist>
        """
        issues = Issues()

        devices = parse_aha_devicelist_xml(deviceinfo, issues)

        assert devices[0]["battery_level"] is None
        assert devices[0]["device_name"] == "Fritz!DECT 200"
        assert issues.messages == ["AHA device battery: expected a whole number, got str"]

    def test_empty_battery_low_and_lock_mean_unknown_not_zero(self):
        # AVM leaves these empty when the value is unknown; exporting 0 would
        # claim "battery fine" / "not locked".
        deviceinfo = """<devicelist version="1">
        <device identifier="1" id="1" manufacturer="AVM" productname="Fritz!DECT 301">
            <present>1</present><name>x</name><battery>80</battery><batterylow></batterylow>
            <switch><state>1</state><mode>manuell</mode><lock></lock></switch>
        </device></devicelist>"""

        [device] = parse_aha_devicelist_xml(deviceinfo, Issues())

        assert device["battery_level"] == 80
        assert device["battery_low"] is None
        assert device["switch"] is not None
        assert device["switch"]["lock"] is None

    def test_should_treat_empty_int_fields_as_absent(self):
        deviceinfo = """<devicelist version="1">
        <device identifier="1" id="1" manufacturer="AVM" productname="Fritz!DECT 200">
            <present>1</present><name>x</name><battery></battery>
        </device></devicelist>"""

        assert parse_aha_devicelist_xml(deviceinfo, Issues())[0]["battery_level"] is None

    def test_should_fall_back_to_hkr_battery_when_missing_on_device(self):
        # Prepare - some firmware versions only report battery data nested
        # inside <hkr> instead of as a direct child of <device>.
        deviceinfo = """<?xml version="1.0" encoding="utf-8"?>
        <devicelist version="1">
        <device identifier="123456789012" id="123" functionbitmask="1"
                fwversion="1.2" manufacturer="AVM" productname="Comet DECT">
            <present>1</present>
            <name>Comet DECT</name>
            <hkr>
                <tist>245</tist>
                <tsoll>234</tsoll>
                <absenk>234</absenk>
                <komfort>234</komfort>
                <battery>80</battery>
                <batterylow>1</batterylow>
            </hkr>
        </device>
        </devicelist>
        """
        # Act
        devices = parse_aha_devicelist_xml(deviceinfo, Issues())

        # Check
        assert devices[0]["battery_level"] == 80
        assert devices[0]["battery_low"] == "1"

    def test_should_correctly_parse_aha_xml(self):
        # Prepare
        deviceinfo = """<?xml version="1.0" encoding="utf-8"?>
        <devicelist version="1">
        <device identifier="123456789012" id="123" functionbitmask="1"
                fwversion="1.2" manufacturer="AVM" productname="Fritz!DECT 200">
            <present>1</present>
            <name>Fritz!DECT 200</name>
            <battery>100</battery>
            <batterylow>0</batterylow>
        </device>
        </devicelist>
        """
        # Act
        devices = parse_aha_devicelist_xml(deviceinfo, Issues())

        # Check
        assert len(devices) == 1
        assert devices[0]["battery_level"] == 100
        assert devices[0]["battery_low"] == "0"

    def test_should_correctly_parse_aha_xml_when_empty(self):
        # Prepare
        deviceinfo = """<?xml version="1.0" encoding="utf-8"?>
        <devicelist version="1">
        </devicelist>
        """
        # Act
        devices = parse_aha_devicelist_xml(deviceinfo, Issues())

        # Check
        assert devices == []
