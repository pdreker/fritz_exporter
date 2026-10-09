import logging
from typing import Any
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from fritzconnection.core.exceptions import FritzActionError, FritzConnectionException

from fritzexporter.fritz_eventlog import (
    Event,
    EventLogParseError,
    EventLogTracker,
    format_event,
    parse_event_log,
    resolve_utc_offset,
)
from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice

from .fc_services_mock import (
    call_action_mock,
    create_fc_services,
    fc_services_capabilities,
)

CET = timedelta(hours=1)
CEST = timedelta(hours=2)
TZ_RULE = "CET-1CEST,M3.5.0,M10.5.0/3"


def event_xml(*events: tuple[str, str, str, str, str]) -> bytes:
    body = "".join(
        f"<Event><id>{i}</id><group>{g}</group><date>{d}</date><time>{t}</time><msg>{m}</msg></Event>"
        for i, g, d, t, m in events
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><DeviceLog><!-- filter :all -->{body}</DeviceLog>'.encode()


LOGIN = (
    "504",
    "sys",
    "09.10.26",
    "21:49:06",
    "Anmeldung des Benutzers exporter an der FRITZ!Box-Benutzeroberfläche "
    "von IP-Adresse 192.0.2.10. [2 Meldungen seit 09.10.26 21:48:36]",
)
CONNECTED = (
    "22",
    "net",
    "09.10.26",
    "08:31:57",
    "Internetverbindung wurde erfolgreich hergestellt. IP-Adresse: 198.51.100.7, "
    "DNS-Server: 198.51.100.1 und 198.51.100.2, Gateway: 198.51.100.1",
)
PROVIDER = (
    "332",
    "sys",
    "09.10.26",
    "08:31:40",
    "Der Dienstanbieter hat erfolgreich Einstellungen an dieses Gerät übertragen.",
)


class TestParse:
    def test_parses_all_fields_newest_first(self):
        parsed = parse_event_log(event_xml(LOGIN, CONNECTED, PROVIDER))

        assert parsed.rejected == []
        assert [e.id for e in parsed.events] == [504, 22, 332]
        first = parsed.events[0]
        assert first.group == "sys"
        assert first.timestamp == datetime(2026, 10, 9, 21, 49, 6)
        assert first.msg.endswith("[2 Meldungen seit 09.10.26 21:48:36]")

    @pytest.mark.parametrize(
        "bad",
        [
            ("x", "sys", "09.10.26", "08:31:40", "m"),
            ("1", "sys", "2026-10-09", "08:31:40", "m"),
            ("1", "sys", "31.02.26", "08:31:40", "m"),
            ("1", "sys", "09.10.26", "8:31", "m"),
            ("1", "sys", "09.10.26", "25:00:00", "m"),
            ("1", "sys group", "09.10.26", "08:31:40", "m"),
            ("1", "", "09.10.26", "08:31:40", "m"),
        ],
    )
    def test_bad_entry_is_rejected_and_good_ones_survive(self, bad):
        parsed = parse_event_log(event_xml(bad, PROVIDER))

        assert [e.id for e in parsed.events] == [332]
        assert len(parsed.rejected) == 1

    def test_entry_with_missing_field_is_rejected(self):
        xml = b"<DeviceLog><Event><id>1</id><group>sys</group></Event></DeviceLog>"

        parsed = parse_event_log(xml)

        assert parsed.events == []
        assert "missing date, time, msg" in parsed.rejected[0]

    @pytest.mark.parametrize(
        "xml",
        [
            b"not xml",
            b"<html><body>login</body></html>",
            b'<!DOCTYPE x [<!ENTITY a "b">]><DeviceLog>&a;</DeviceLog>',
        ],
    )
    def test_document_that_is_not_a_device_log_raises(self, xml):
        with pytest.raises(EventLogParseError):
            parse_event_log(xml)

    def test_empty_log_is_valid(self):
        assert parse_event_log(b"<DeviceLog></DeviceLog>").events == []


def take(tracker: EventLogTracker, parsed):
    """Collect the new entries and commit, as the capability does after emitting."""
    new = tracker.new_events(parsed)
    tracker.commit(parsed)
    return new


class TestTracker:
    def test_emits_each_entry_once_oldest_first(self):
        tracker = EventLogTracker()
        parsed = parse_event_log(event_xml(LOGIN, CONNECTED, PROVIDER))

        first = take(tracker, parsed)
        second = take(tracker, parsed)

        assert [e.id for e in first] == [332, 22, 504]
        assert second == []

    def test_events_are_not_lost_when_emission_does_not_happen(self):
        tracker = EventLogTracker()
        parsed = parse_event_log(event_xml(LOGIN, CONNECTED))

        assert len(tracker.new_events(parsed)) == 2
        assert len(tracker.new_events(parsed)) == 2  # nothing committed, nothing consumed
        tracker.commit(parsed)
        assert tracker.new_events(parsed) == []

    def test_identical_entries_in_the_same_second_are_each_emitted(self):
        tracker = EventLogTracker()
        fail = ("505", "sys", "09.10.26", "21:50:00", "Anmeldung des Benutzers exporter gescheitert.")
        parsed = parse_event_log(event_xml(fail, fail))

        assert len(take(tracker, parsed)) == 2
        assert take(tracker, parsed) == []
        three = parse_event_log(event_xml(fail, fail, fail))
        assert len(take(tracker, three)) == 1

    def test_only_new_entry_is_emitted_on_next_collection(self):
        tracker = EventLogTracker()
        take(tracker, parse_event_log(event_xml(CONNECTED, PROVIDER)))

        new = take(tracker, parse_event_log(event_xml(LOGIN, CONNECTED, PROVIDER)))

        assert [e.id for e in new] == [504]

    def test_folded_row_counts_as_new_entry(self):
        tracker = EventLogTracker()
        single = (*LOGIN[:3], "21:48:36", "Anmeldung des Benutzers exporter")
        folded = (
            *LOGIN[:3],
            "21:49:06",
            "Anmeldung des Benutzers exporter [2 Meldungen seit 09.10.26 21:48:36]",
        )
        take(tracker, parse_event_log(event_xml(single)))

        new = take(tracker, parse_event_log(event_xml(folded)))

        assert len(new) == 1
        assert new[0].timestamp.second == 6

    def test_seen_set_is_bounded_to_the_current_buffer(self):
        tracker = EventLogTracker()
        take(tracker, parse_event_log(event_xml(LOGIN, CONNECTED)))

        take(tracker, parse_event_log(event_xml(LOGIN)))

        assert len(tracker._emitted) == 1

    def test_buffer_cleared_by_restart_emits_again(self):
        tracker = EventLogTracker()
        take(tracker, parse_event_log(event_xml(CONNECTED)))
        take(tracker, parse_event_log(event_xml()))

        assert len(take(tracker, parse_event_log(event_xml(CONNECTED)))) == 1

    def test_rejected_entries_warn_once_not_every_collection(self, caplog):
        tracker = EventLogTracker()
        bad = event_xml(("x", "sys", "09.10.26", "08:31:40", "m"), PROVIDER)

        with caplog.at_level(logging.WARNING, logger="fritzexporter.fritz_eventlog"):
            for _ in range(3):
                take(tracker, parse_event_log(bad))

        assert len([r for r in caplog.records if "failed validation" in r.message]) == 1


class TestFormat:
    def event(self, msg: str, ts: datetime = datetime(2026, 10, 9, 22, 11, 35)) -> Event:  # noqa: B008
        return Event(id=504, group="sys", timestamp=ts, msg=msg)

    def test_line_layout(self):
        line = format_event(self.event("hello"), CEST)

        assert line == 'event_time=2026-10-09T22:11:35+02:00 group=sys id=504 msg="hello"'

    def test_unknown_offset_gives_time_without_offset(self):
        assert format_event(self.event("hello"), None).startswith("event_time=2026-10-09T22:11:35 ")

    def test_newlines_and_control_characters_cannot_forge_a_line(self):
        evil = 'user\nevent_time=2000-01-01T00:00:00Z group=sys id=1 msg="x"\r\x1b[2J \x00'

        line = format_event(self.event(evil), CEST)

        assert "\n" not in line
        assert "\r" not in line
        assert "\x1b" not in line
        assert " " not in line
        assert "\x00" not in line
        assert line.count("event_time=") == 2  # the forged one is inside the quoted msg
        assert '\\"x\\"' in line
        assert "\\n" in line
        assert "\\u001b" in line

    def test_backslash_is_escaped_so_escaping_is_unambiguous(self):
        assert 'msg="a\\\\nb"' in format_event(self.event("a\\nb"), CEST)

    def test_ddns_token_is_redacted(self):
        msg = "update https://ddns.example.invalid/u?domain=host.example.invalid&q=not-a-real-token&ip=192.0.2.1"

        line = format_event(self.event(msg), CEST)

        assert "not-a-real-token" not in line
        assert "&q=<redacted>&ip=192.0.2.1" in line

    def test_token_at_start_and_end_is_redacted_but_other_q_words_are_not(self):
        assert "q=<redacted>" in format_event(self.event("q=abc123"), CEST)
        assert "freq=5" in format_event(self.event("freq=5 and seq=7"), CEST)


class TestUtcOffset:
    @pytest.mark.parametrize(
        ("local", "expected"),
        [
            (datetime(2026, 1, 15, 12, 0, 0), CET),
            (datetime(2026, 7, 15, 12, 0, 0), CEST),
            # spring forward: 2026-03-29 02:00 -> 03:00
            (datetime(2026, 3, 29, 1, 59, 59), CET),
            (datetime(2026, 3, 29, 2, 30, 0), CET),  # skipped hour: standard offset
            (datetime(2026, 3, 29, 3, 0, 0), CEST),
            # fall back: 2026-10-25 03:00 -> 02:00, 02:00-03:00 occurs twice
            (datetime(2026, 10, 25, 1, 59, 59), CEST),
            (datetime(2026, 10, 25, 2, 0, 0), CEST),  # repeated hour: first occurrence
            (datetime(2026, 10, 25, 2, 59, 59), CEST),
            (datetime(2026, 10, 25, 3, 0, 0), CET),
        ],
    )
    def test_both_switches(self, local, expected):
        assert resolve_utc_offset(local, TZ_RULE, timedelta(hours=9)) == expected

    def test_entry_before_last_switch_does_not_use_current_offset(self):
        # Box is in CET now (current offset +1), the entry is from summer.
        assert resolve_utc_offset(datetime(2026, 8, 1, 12, 0), TZ_RULE, CET) == CEST

    def test_southern_hemisphere_rule(self):
        rule = "AEST-10AEDT,M10.1.0,M4.1.0/3"

        assert resolve_utc_offset(datetime(2026, 1, 15, 12, 0), rule, None) == timedelta(hours=11)
        assert resolve_utc_offset(datetime(2026, 7, 15, 12, 0), rule, None) == timedelta(hours=10)

    def test_explicit_dst_offset_and_no_dst(self):
        assert resolve_utc_offset(
            datetime(2026, 7, 1), "XXX-1YYY-3,M3.5.0,M10.5.0", None
        ) == timedelta(hours=3)
        assert resolve_utc_offset(datetime(2026, 7, 1), "UTC0", CET) == timedelta(0)

    @pytest.mark.parametrize(
        "rule",
        [None, "", "Europe/Berlin", "CET-1CEST", "CET-1CEST,J60,J300", "CET-1CEST,M13.5.0,M10.5.0", "CET-30CEST,M3.5.0,M10.5.0"],
    )
    def test_unparsable_rule_falls_back_to_current_offset(self, rule):
        assert resolve_utc_offset(datetime(2026, 7, 1), rule, CET) == CET
        assert resolve_utc_offset(datetime(2026, 7, 1), rule, None) is None


LOG_PATH = "/devicelog.lua?sid=0123456789abcdef"


@patch("fritzexporter.tr064_remote.FritzConnection")
class TestEventLogCapability:
    """The EventLog capability: wiring, fetching, and never failing a scrape."""

    def setup_device(self, mock_fritzconnection: MagicMock, *, event_log: bool, responses=None):
        fc = mock_fritzconnection.return_value
        fc.address = "http://somehost"
        fc.port = 49000
        fc.timeout = 10
        state: dict[str, Any] = {
            "xml": responses if responses is not None else [event_xml(LOGIN, CONNECTED, PROVIDER)],
            "time": {
                "NewCurrentLocalTime": "2026-10-09T22:14:57+02:00",
                "NewLocalTimeZoneName": TZ_RULE,
            },
        }

        def call_action(service, action, **kwargs):
            if (service, action) == ("DeviceInfo1", "X_AVM-DE_GetDeviceLogPath"):
                return {"NewDeviceLogPath": state.get("path", LOG_PATH)}
            if (service, action) == ("Time1", "GetInfo"):
                if state.get("time_error"):
                    raise state["time_error"]
                if state["time"] is None:
                    raise FritzActionError
                return state["time"]
            return call_action_mock(service, action, **kwargs)

        fc.call_action.side_effect = call_action
        # both capabilities live in DeviceInfo1, so the action lists are joined
        fc.services = create_fc_services(
            {
                "DeviceInfo1": [
                    *fc_services_capabilities["DeviceInfo"]["DeviceInfo1"],
                    *fc_services_capabilities["EventLog"]["DeviceInfo1"],
                ]
            }
        )
        device = FritzDevice(
            FritzCredentials("somehost", "someuser", "password"), "FritzMock", event_log=event_log
        )
        collector = FritzCollector()
        collector.register(device)
        return collector, device, state

    def collect(self, collector: FritzCollector, state, caplog) -> list[str]:
        """Run one collection with the next queued XML; return the event lines."""
        caplog.clear()
        payload = state["xml"].pop(0) if len(state["xml"]) > 1 else state["xml"][0]
        with patch("fritzexporter.fritzcapabilities.requests.Session") as session_cls:
            session = session_cls.return_value.__enter__.return_value
            if isinstance(payload, Exception):
                session.get.side_effect = payload
            elif isinstance(payload, tuple):
                session.get.return_value.status_code = payload[0]
                session.get.return_value.reason = payload[1]
            else:
                session.get.return_value.status_code = 200
                session.get.return_value.content = payload
            with caplog.at_level(logging.INFO, logger="fritzexporter.event_log"):
                metrics = list(collector.collect())
            state["session"] = session
            state.setdefault("urls", []).extend(c.args[0] for c in session.get.call_args_list)
        state["metrics"] = metrics
        return [r.getMessage() for r in caplog.records if r.name == "fritzexporter.event_log"]

    def test_flag_off_means_no_capability_and_no_requests(self, mock_fritzconnection, caplog):
        collector, device, state = self.setup_device(mock_fritzconnection, event_log=False)

        lines = self.collect(collector, state, caplog)

        assert device.capabilities["EventLog"].present is False
        assert lines == []
        state["session"].get.assert_not_called()

    def test_flag_on_without_action_means_no_capability(self, mock_fritzconnection):
        _, device, _ = self.setup_device(mock_fritzconnection, event_log=True)
        assert device.capabilities["EventLog"].present is True

        device.fc.services = create_fc_services(fc_services_capabilities["DeviceInfo"])
        device.capabilities.check_present(device)

        assert device.capabilities["EventLog"].present is False

    def test_first_collection_emits_whole_buffer_oldest_first_with_offsets(
        self, mock_fritzconnection, caplog
    ):
        collector, device, state = self.setup_device(mock_fritzconnection, event_log=True)

        lines = self.collect(collector, state, caplog)

        assert device.capabilities["EventLog"].present is True
        assert len(lines) == 3
        assert lines[0].startswith("event_time=2026-10-09T08:31:40+02:00 group=sys id=332 msg=")
        assert lines[1].startswith("event_time=2026-10-09T08:31:57+02:00 group=net id=22 ")
        assert lines[2].startswith("event_time=2026-10-09T21:49:06+02:00 group=sys id=504 ")
        state["session"].get.assert_called_once_with(f"http://somehost:49000{LOG_PATH}", timeout=10)

    def test_repeated_collections_emit_only_new_entries(self, mock_fritzconnection, caplog):
        newer = (
            "22",
            "net",
            "09.10.26",
            "22:40:00",
            "Internetverbindung wurde erfolgreich hergestellt.",
        )
        collector, _, state = self.setup_device(
            mock_fritzconnection,
            event_log=True,
            responses=[
                event_xml(LOGIN, CONNECTED, PROVIDER),
                event_xml(LOGIN, CONNECTED, PROVIDER),
                event_xml(newer, LOGIN, CONNECTED, PROVIDER),
            ],
        )

        counts = [len(self.collect(collector, state, caplog)) for _ in range(3)]

        assert counts == [3, 0, 1]

    def test_no_metrics_are_added(self, mock_fritzconnection, caplog):
        collector, _, state = self.setup_device(mock_fritzconnection, event_log=True)
        self.collect(collector, state, caplog)
        with_flag = {m.name for m in state["metrics"]}

        collector, _, state = self.setup_device(mock_fritzconnection, event_log=False)
        self.collect(collector, state, caplog)

        assert {m.name for m in state["metrics"]} == with_flag

    def test_missing_time_zone_logs_time_without_offset(self, mock_fritzconnection, caplog):
        collector, _, state = self.setup_device(mock_fritzconnection, event_log=True)
        state["time"] = None

        lines = self.collect(collector, state, caplog)

        assert lines[0].startswith("event_time=2026-10-09T08:31:40 group=sys")

    def test_unparsable_rule_uses_current_offset(self, mock_fritzconnection, caplog):
        july = ("22", "net", "15.07.26", "12:00:00", "Internetverbindung wurde hergestellt.")
        collector, _, state = self.setup_device(
            mock_fritzconnection, event_log=True, responses=[event_xml(july)]
        )
        state["time"]["NewCurrentLocalTime"] = "2026-10-09T22:14:57+01:00"
        state["time"]["NewLocalTimeZoneName"] = "Mars/Olympus"

        lines = self.collect(collector, state, caplog)

        # the correct rule would give +02:00 for July; the fallback is the current +01:00
        assert lines[0].startswith("event_time=2026-07-15T12:00:00+01:00 ")

    def test_rule_is_used_when_current_time_has_no_offset(self, mock_fritzconnection, caplog):
        collector, _, state = self.setup_device(mock_fritzconnection, event_log=True)
        state["time"]["NewCurrentLocalTime"] = "2026-10-09T22:14:57"

        lines = self.collect(collector, state, caplog)

        assert lines[0].startswith("event_time=2026-10-09T08:31:40+02:00 ")

    @pytest.mark.parametrize(
        "error", [requests.ConnectionError("boom"), requests.Timeout("slow"), FritzConnectionException("down")]
    )
    def test_time_zone_failure_is_not_fatal_and_loses_no_entries(
        self, mock_fritzconnection, caplog, error
    ):
        collector, device, state = self.setup_device(mock_fritzconnection, event_log=True)
        state["time_error"] = error

        lines = self.collect(collector, state, caplog)
        again = self.collect(collector, state, caplog)

        assert len(lines) == 3
        assert lines[0].startswith("event_time=2026-10-09T08:31:40 group=sys")
        assert again == []
        assert device.available is True

    def test_entries_are_retried_if_emission_is_interrupted(self, mock_fritzconnection, caplog):
        collector, _, state = self.setup_device(mock_fritzconnection, event_log=True)
        with patch(
            "fritzexporter.fritzcapabilities.format_event", side_effect=RuntimeError("bug")
        ), pytest.raises(RuntimeError):
            self.collect(collector, state, caplog)

        lines = self.collect(collector, state, caplog)

        assert len(lines) == 3

    def test_identical_entries_in_one_second_are_both_logged(self, mock_fritzconnection, caplog):
        fail = ("505", "sys", "09.10.26", "21:50:00", "Anmeldung des Benutzers exporter gescheitert.")
        collector, _, state = self.setup_device(
            mock_fritzconnection, event_log=True, responses=[event_xml(fail, fail)]
        )

        first = self.collect(collector, state, caplog)
        second = self.collect(collector, state, caplog)

        assert len(first) == 2
        assert second == []

    def test_session_follows_ca_bundle_environment(self, mock_fritzconnection, caplog, monkeypatch):
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/nonexistent/ca.pem")
        monkeypatch.setenv("CURL_CA_BUNDLE", "/nonexistent/ca.pem")
        collector, _, state = self.setup_device(mock_fritzconnection, event_log=True)
        seen = {}

        def send(self_, request, **kwargs):
            seen.update(kwargs)
            response = requests.Response()
            response.status_code = 200
            response._content = event_xml(PROVIDER)
            return response

        with patch("requests.adapters.HTTPAdapter.send", send), caplog.at_level(
            logging.INFO, logger="fritzexporter.event_log"
        ):
            list(collector.collect())

        assert seen["verify"] == "/nonexistent/ca.pem"
        assert [r for r in caplog.records if r.name == "fritzexporter.event_log"]

    def test_unparsable_body_is_retried_and_recovers(self, mock_fritzconnection, caplog):
        collector, device, state = self.setup_device(
            mock_fritzconnection,
            event_log=True,
            responses=[b"", b"<html>login</html>", event_xml(PROVIDER)],
        )

        first = self.collect(collector, state, caplog)
        first_warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        self.collect(collector, state, caplog)
        second_warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        recovered = self.collect(collector, state, caplog)

        assert first == []
        assert len(first_warnings) == 1
        assert second_warnings == []
        assert len(recovered) == 1
        assert device.capabilities["EventLog"].present is True

    def test_http_error_status_is_retried(self, mock_fritzconnection, caplog):
        collector, device, state = self.setup_device(
            mock_fritzconnection,
            event_log=True,
            responses=[(503, "Service Unavailable"), event_xml(PROVIDER)],
        )

        self.collect(collector, state, caplog)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        recovered = self.collect(collector, state, caplog)

        assert "HTTP 503 Service Unavailable" in warnings[0].message
        assert len(recovered) == 1
        assert device.capabilities["EventLog"].present is True

    @pytest.mark.parametrize(
        "payload",
        [
            requests.ConnectionError(f"HTTPConnectionPool(host='somehost', port=49000): Max retries exceeded with url: {LOG_PATH}"),
            requests.exceptions.ConnectTimeout(f"timed out for {LOG_PATH}"),
            requests.HTTPError(f"500 Server Error for url: http://somehost:49000{LOG_PATH}"),
        ],
    )
    def test_session_id_and_path_never_reach_the_logs(self, mock_fritzconnection, caplog, payload):
        collector, _, state = self.setup_device(mock_fritzconnection, event_log=True, responses=[payload])
        caplog.set_level(logging.DEBUG)

        self.collect(collector, state, caplog)
        records = list(caplog.records)
        self.collect(collector, state, caplog)
        records += caplog.records

        assert [r for r in records if r.levelno == logging.WARNING]
        for record in records:
            text = record.getMessage() + str(record.exc_info) + str(record.exc_text)
            assert "0123456789abcdef" not in text
            assert "devicelog" not in text
            assert "sid=" not in text
        assert type(payload).__name__ in " ".join(r.getMessage() for r in records)

    def test_action_failure_disables_the_feature(self, mock_fritzconnection, caplog):
        collector, device, state = self.setup_device(mock_fritzconnection, event_log=True)
        original = mock_fritzconnection.return_value.call_action.side_effect

        def failing(service, action, **kwargs):
            if action == "X_AVM-DE_GetDeviceLogPath":
                raise FritzActionError
            return original(service, action, **kwargs)

        mock_fritzconnection.return_value.call_action.side_effect = failing

        self.collect(collector, state, caplog)

        assert device.capabilities["EventLog"].present is False
        assert device.available is True

    def test_transient_fetch_error_does_not_disable_or_fail_the_scrape(
        self, mock_fritzconnection, caplog
    ):
        collector, device, state = self.setup_device(
            mock_fritzconnection,
            event_log=True,
            responses=[
                requests.ConnectionError("boom"),
                requests.ConnectionError("boom"),
                event_xml(PROVIDER),
            ],
        )

        first = self.collect(collector, state, caplog)
        first_warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        self.collect(collector, state, caplog)
        second_warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        recovered = self.collect(collector, state, caplog)

        assert first == []
        assert len(first_warnings) == 1
        assert second_warnings == []
        assert len(recovered) == 1
        assert device.capabilities["EventLog"].present is True
        assert device.available is True

    @pytest.mark.parametrize(
        "path",
        [
            "@attacker.example/x",
            ".evil.example/",
            "//evil.example/x",
            "/ok\\@evil.example",
            "/a b",
            "/a#frag",
            "/a\n",
            "http://evil.example/x",
            "",
            None,
            42,
        ],
    )
    def test_hostile_log_path_is_rejected_without_a_request(
        self, mock_fritzconnection, caplog, path
    ):
        collector, device, state = self.setup_device(
            mock_fritzconnection, event_log=True, responses=[event_xml(PROVIDER)]
        )
        state["path"] = path
        caplog.set_level(logging.DEBUG)

        first = self.collect(collector, state, caplog)
        records = list(caplog.records)
        self.collect(collector, state, caplog)
        records += caplog.records
        state["path"] = LOG_PATH
        recovered = self.collect(collector, state, caplog)

        assert first == []
        # no request for the hostile path, only for the legitimate one afterwards
        assert state["urls"] == [f"http://somehost:49000{LOG_PATH}"]
        assert len([r for r in records if r.levelno == logging.WARNING]) == 1
        for record in records:
            text = record.getMessage() + str(record.exc_info)
            assert "evil" not in text
            assert "attacker" not in text
            assert str(path) not in text or str(path) in ("", "None", "42")
        assert len(recovered) == 1
        assert device.capabilities["EventLog"].present is True

    def test_legitimate_log_path_is_fetched(self, mock_fritzconnection, caplog):
        collector, _, state = self.setup_device(mock_fritzconnection, event_log=True)

        assert len(self.collect(collector, state, caplog)) == 3
        state["session"].get.assert_called_once_with(f"http://somehost:49000{LOG_PATH}", timeout=10)
