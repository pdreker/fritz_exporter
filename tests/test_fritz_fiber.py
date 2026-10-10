"""Tests for the fibre level fallback (fritz_fiber.py)."""

from fritzexporter.fritz_fiber import parse_fiber_stats_response


class TestParseFiberStats:
    def test_newest_entry_of_each_series_in_dbm(self):
        raw = {"data": {"opt_ds": [["sa", 9, -65], ["sa", 8, -66]], "opt_us": [["sa", 9, -54]]}}
        assert parse_fiber_stats_response(raw) == {"receive_dbm": -6.5, "transmit_dbm": -5.4}

    def test_missing_or_malformed_series_give_none(self):
        assert parse_fiber_stats_response({}) == {"receive_dbm": None, "transmit_dbm": None}
        raw = {"data": {"opt_ds": [], "opt_us": [["sa", 9, "n/a"]]}}
        assert parse_fiber_stats_response(raw) == {"receive_dbm": None, "transmit_dbm": None}
