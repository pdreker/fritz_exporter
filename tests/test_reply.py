"""Typed reading of device replies (fritzexporter/reply.py)."""

import math
from unittest.mock import MagicMock

import pytest

from fritzexporter.reply import (
    DataError,
    Issues,
    Reply,
    read_action,
    to_bool,
    to_float,
    to_int,
    to_str,
)

SECRET = "192.0.2.77"


class TestConverters:
    @pytest.mark.parametrize("absent", [None, ""])
    @pytest.mark.parametrize("convert", [to_float, to_int, to_bool, to_str])
    def test_absent_values_are_none(self, convert, absent):
        assert convert(absent, "x") is None

    @pytest.mark.parametrize(
        ("value", "expected"), [(1, 1.0), (2.5, 2.5), ("-3.5", -3.5), (" 7 ", 7.0), ("1e3", 1000.0)]
    )
    def test_to_float(self, value, expected):
        assert to_float(value, "x") == expected

    def test_to_float_keeps_nan_and_inf(self):
        nan = to_float("nan", "x")
        assert nan is not None
        assert math.isnan(nan)
        assert to_float("1e999", "x") == math.inf

    @pytest.mark.parametrize("value", ["abc", True, [], {}, object()])
    def test_to_float_rejects(self, value):
        with pytest.raises(DataError, match="x: expected a number"):
            to_float(value, "x")

    @pytest.mark.parametrize(("value", "expected"), [(3, 3), (" 42 ", 42), (5.0, 5), ("-1", -1)])
    def test_to_int(self, value, expected):
        assert to_int(value, "x") == expected

    @pytest.mark.parametrize("value", ["4.5", 4.5, math.inf, math.nan, "1e999", False, "abc"])
    def test_to_int_rejects(self, value):
        with pytest.raises(DataError, match="x: expected a whole number"):
            to_int(value, "x")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(True, True), (False, False), (1, True), (0, False), ("1", True), ("0", False),
         ("true", True), ("False", False), (" on ", True)],
    )
    def test_to_bool(self, value, expected):
        assert to_bool(value, "x") is expected

    @pytest.mark.parametrize("value", [2, -1, "maybe", 1.0, []])
    def test_to_bool_rejects(self, value):
        with pytest.raises(DataError, match="x: expected a flag"):
            to_bool(value, "x")

    @pytest.mark.parametrize(
        ("value", "expected"), [("a", "a"), (5, "5"), (1.5, "1.5"), (True, "1"), (False, "0")]
    )
    def test_to_str(self, value, expected):
        assert to_str(value, "x") == expected

    @pytest.mark.parametrize("value", [[], {}, object()])
    def test_to_str_rejects(self, value):
        with pytest.raises(DataError, match="x: expected text"):
            to_str(value, "x")

    def test_error_names_the_field_but_never_the_value(self):
        with pytest.raises(DataError) as exc:
            to_float(SECRET, "Hosts1.GetGenericHostEntry NewIPAddress")
        assert str(exc.value) == (
            "Hosts1.GetGenericHostEntry NewIPAddress: expected a number, got str"
        )
        assert SECRET not in str(exc.value)


class TestReply:
    reply = Reply(
        {
            "count": "3",
            "rate": 1.5,
            "empty": "",
            "none": None,
            "name": "box",
            "on": True,
            "nested": {"inner": 1},
            "rows": [{"a": 1}, {"a": 2}],
            "samples": [1, None, "2"],
            "bad_rows": [{"a": 1}, "x"],
        },
        "Svc.Action",
    )

    def test_must_be_an_object(self):
        with pytest.raises(DataError, match="Svc.Action: expected an object, got list"):
            Reply([], "Svc.Action")

    def test_required_reads(self):
        assert self.reply.integer("count") == 3
        assert self.reply.num("rate") == 1.5
        assert self.reply.text("name") == "box"
        assert self.reply.flag("on") is True
        nested = self.reply.opt_obj("nested")
        assert nested is not None
        assert nested.integer("inner") == 1

    @pytest.mark.parametrize("key", ["empty", "none", "missing"])
    def test_required_read_of_absent_value_raises(self, key):
        for read in (self.reply.num, self.reply.integer, self.reply.flag, self.reply.text):
            with pytest.raises(DataError, match=f"Svc.Action {key}: missing"):
                read(key)

    @pytest.mark.parametrize("key", ["empty", "none", "missing"])
    def test_optional_read_of_absent_value_is_none(self, key):
        assert self.reply.opt_num(key) is None
        assert self.reply.opt_integer(key) is None
        assert self.reply.opt_flag(key) is None
        assert self.reply.opt_text(key) is None
        assert self.reply.opt_obj(key) is None

    def test_text_default_replaces_only_absent_values(self):
        assert self.reply.text("missing", "n/a") == "n/a"
        assert self.reply.text("empty", "") == ""
        assert self.reply.text("name", "n/a") == "box"
        with pytest.raises(DataError):
            self.reply.text("missing")

    def test_present_but_malformed_raises_even_when_optional(self):
        with pytest.raises(DataError, match="Svc.Action name: expected a number"):
            self.reply.opt_num("name")
        with pytest.raises(DataError, match="Svc.Action nested: expected text"):
            self.reply.opt_text("nested")
        with pytest.raises(DataError, match="Svc.Action name: expected an object"):
            self.reply.opt_obj("name")

    def test_lists(self):
        assert self.reply.items("samples") == [1, None, "2"]
        assert self.reply.items("missing") == []
        assert self.reply.items("none") == []

    def test_list_errors_name_the_field(self):
        with pytest.raises(DataError, match="Svc.Action name: expected a list"):
            self.reply.items("name")


class TestReadAction:
    def test_wraps_the_result_and_passes_arguments(self):
        device = MagicMock()
        device.fc.call_action.return_value = {"NewIndex": "4"}

        reply = read_action(device, "Hosts1", "GetGenericHostEntry", NewIndex=4)

        device.fc.call_action.assert_called_once_with("Hosts1", "GetGenericHostEntry", NewIndex=4)
        assert reply.integer("NewIndex") == 4
        with pytest.raises(DataError, match="Hosts1.GetGenericHostEntry NewHostName: missing"):
            reply.text("NewHostName")

    def test_non_dict_result_raises(self):
        device = MagicMock()
        device.fc.call_action.return_value = None

        with pytest.raises(DataError, match="DeviceInfo1.GetInfo: expected an object, got NoneType"):
            read_action(device, "DeviceInfo1", "GetInfo")


class TestIssues:
    reply = Reply({"n": "4", "bad": "x", "empty": "", "flag": "1", "state": "Up"}, "S.A")

    def test_readers_return_values_and_record_nothing(self):
        issues = Issues()

        assert issues.num(self.reply, "n") == 4.0
        assert issues.num(self.reply, "n", divide_by=10) == 0.4
        assert issues.opt_num(self.reply, "empty") is None
        assert issues.opt_integer(self.reply, "n") == 4
        assert issues.flag(self.reply, "flag") == 1
        assert issues.equals(self.reply, "state", "Up") == 1
        assert issues.equals(self.reply, "missing", "Up") == 0
        assert issues.text(self.reply, "missing", "n/a") == "n/a"
        assert issues.num_or(self.reply, "missing", 0.0) == 0.0
        assert issues.convert("2.5", to_float, "x") == 2.5
        assert issues.messages == []

    def test_defects_give_none_and_are_recorded(self):
        issues = Issues()

        assert issues.num(self.reply, "bad") is None
        assert issues.num(self.reply, "missing") is None
        assert issues.num_or(self.reply, "bad", 0.0) is None
        assert issues.flag(self.reply, "bad") is None
        assert issues.convert("x", to_int, "item 3") is None
        assert issues.messages == [
            "S.A bad: expected a number, got str",
            "S.A missing: missing",
            "S.A bad: expected a number, got str",
            "S.A bad: expected a flag, got str",
            "item 3: expected a whole number, got str",
        ]

    def test_other_errors_are_not_contained(self):
        def broken():
            raise KeyError("x")

        with pytest.raises(KeyError):
            Issues().value(broken)

    def test_objects_skips_and_records_elements_that_are_no_objects(self):
        issues = Issues()
        reply = Reply({"rows": [{"a": 1}, "x", {"a": 2}]}, "S.A")

        rows = [(index, row.integer("a")) for index, row in issues.objects(reply, "rows")]

        assert rows == [(0, 1), (2, 2)]
        assert issues.messages == ["S.A rows[1] skipped: S.A rows[1]: expected an object, got str"]

    def test_objects_raises_when_the_list_itself_is_wrong(self):
        with pytest.raises(DataError, match="S.A rows: expected a list"):
            list(Issues().objects(Reply({"rows": "x"}, "S.A"), "rows"))

    def test_item_skips_the_rest_of_the_block_and_undoes_it(self):
        issues = Issues()
        done: list[int | str] = []

        def undo() -> None:
            done.pop()

        for index in range(3):
            with issues.item(f"item {index}", on_skip=undo):
                done.append(index)
                if index == 1:
                    to_int("x", "item value")
                done.append("ok")

        assert done == [0, "ok", 2, "ok"]
        assert issues.messages == ["item 1 skipped: item value: expected a whole number, got str"]

    def test_item_lets_other_errors_through(self):
        with pytest.raises(KeyError), Issues().item("item"):
            raise KeyError("x")
