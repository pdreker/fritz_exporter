"""Tests for the generic Fritz!Box web interface client (fritz_webui.py)."""

import errno
import hashlib
import logging
import socket
import traceback
from unittest.mock import MagicMock, patch

import pytest
import requests
import urllib3.connection
import urllib3.exceptions

from fritzexporter.fritz_webui import (
    FritzWebUiClient,
    FritzWebUiError,
    FritzWebUiMd5DisabledError,
    _failure_text,
    _http_error_text,
)


# ---------------------------------------------------------------------------
# FritzWebUiClient authentication
# ---------------------------------------------------------------------------


class TestFritzWebUiClientAuth:
    def test_md5_response(self):
        # Known-good vector: challenge "12345678", password "test"
        # MD5 over UTF-16LE of "12345678-test"
        response = FritzWebUiClient._md5_response("12345678", "test")
        assert response == "12345678-a51138a45d6b4b9a2397e5f370f4e850"

    def test_pbkdf2_response(self):
        challenge = "2$9000$00112233445566778899aabbccddeeff$1000$ffeeddccbbaa99887766554433221100"
        response = FritzWebUiClient._pbkdf2_response(challenge, "test")
        assert response == (
            "2$9000$00112233445566778899aabbccddeeff$1000$ffeeddccbbaa99887766554433221100$"
            "d7328ff621925c1094730e3784cf1c7eb3782c989e1eab4a56c9b426c55b57c8"
        )

    def test_pbkdf2_response_rejects_bad_challenge(self):
        with pytest.raises(FritzWebUiError):
            FritzWebUiClient._pbkdf2_response("not-a-challenge", "test")

    def test_pbkdf2_response_known_answer_independent(self):
        salt1, salt2 = bytes.fromhex("00112233445566778899aabbccddeeff"), bytes.fromhex("aabbcc")
        challenge = f"2$60000${salt1.hex()}$6000${salt2.hex()}"
        hash1 = hashlib.pbkdf2_hmac("sha256", b"test", salt1, 60000, 32)
        hash2 = hashlib.pbkdf2_hmac("sha256", hash1, salt2, 6000, 32)
        assert FritzWebUiClient._pbkdf2_response(challenge, "test") == f"{challenge}${hash2.hex()}"

    @pytest.mark.parametrize(
        ("iter1", "iter2", "ok"),
        [
            # The bounds apply to the sum, which is what a response costs.
            (60000, 6000, True),
            (10000, 2000, True),
            (9000, 1000, True),
            (8999, 1000, False),
            (5000, 4999, False),
            (1000, 1000, False),
            (999_000, 1000, True),
            (1000, 999_000, True),
            (999_001, 1000, False),
            (500_001, 500_000, False),
            (1_000_000, 1_000_000, False),
            (0, 60000, False),
            (60000, 0, False),
        ],
    )
    def test_pbkdf2_iteration_bounds(self, monkeypatch, iter1, iter2, ok):
        calls = []
        monkeypatch.setattr(hashlib, "pbkdf2_hmac", lambda *a: calls.append(a) or b"\0" * 32)
        challenge = f"2${iter1}$aabb${iter2}$ccdd"
        if ok:
            assert FritzWebUiClient._pbkdf2_response(challenge, "test").startswith(challenge)
            assert len(calls) == 2
        else:
            with pytest.raises(FritzWebUiError):
                FritzWebUiClient._pbkdf2_response(challenge, "test")
            assert not calls

    def test_pbkdf2_huge_iterations_rejected_without_hashing(self, monkeypatch):
        def fail(*_args):
            raise AssertionError("pbkdf2_hmac must not run")

        monkeypatch.setattr(hashlib, "pbkdf2_hmac", fail)
        for challenge in (
            "2$999999999$aa$999999999$bb",
            "2$" + "9" * 5000 + "$aa$1000$bb",
        ):
            with pytest.raises(FritzWebUiError):
                FritzWebUiClient._pbkdf2_response(challenge, "test")

    @pytest.mark.parametrize(
        "challenge",
        [
            "2$9000$" + "ab" * 65 + "$1000$aabb",  # salt of 65 bytes
            "2$9000$aabb$1000$" + "ab" * 65,
            "2$1000$$1000$aabb",  # empty salt
            "2$1000$abc$1000$aabb",  # odd-length hex
            "2$1000$aabb$1000$abc",
            "2$1000$zzzz$1000$aabb",  # non-hex
            "2$1000$aabb$1000$aabb$extra",  # trailing garbage
            "2$1000$aabb$1000$aabb\n",
            "2$1000$aabb$1000$aabb$",
            "2$1000$aabb",
            "2$\u0661\u0660\u0660\u0660$aabb$1000$aabb",  # non-ASCII digits
            "x" * 10000,
        ],
    )
    def test_pbkdf2_malformed_challenge_rejected_with_fixed_message(self, challenge):
        with pytest.raises(FritzWebUiError) as exc:
            FritzWebUiClient._pbkdf2_response(challenge, "test")
        assert len(str(exc.value)) < 200
        assert challenge.strip() not in str(exc.value)

    def test_pbkdf2_accepts_avm_documented_shape(self):
        salt1, salt2 = bytes.fromhex("5A1711"), bytes.fromhex("2ca9a9b1")
        challenge = "2$10000$5A1711$2000$2ca9a9b1"
        hash1 = hashlib.pbkdf2_hmac("sha256", b"test", salt1, 10000, 32)
        hash2 = hashlib.pbkdf2_hmac("sha256", hash1, salt2, 2000, 32)
        assert FritzWebUiClient._pbkdf2_response(challenge, "test") == f"{challenge}${hash2.hex()}"

    def test_pbkdf2_accepts_salt_of_64_hex_chars(self):
        challenge = "2$60000$" + "ab" * 32 + "$6000$" + "cd" * 32
        assert FritzWebUiClient._pbkdf2_response(challenge, "test").startswith(challenge + "$")

    def test_pbkdf2_accepts_salt_of_64_bytes(self):
        challenge = "2$60000$" + "ab" * 64 + "$6000$" + "cd" * 64
        assert FritzWebUiClient._pbkdf2_response(challenge, "test").startswith(challenge + "$")

    def test_pbkdf2_accepts_real_shaped_challenge(self):
        challenge = "2$60000$0123456789abcdef0123456789abcdef$6000$fedcba9876543210fedcba9876543210"
        assert FritzWebUiClient._pbkdf2_response(challenge, "test").startswith(challenge + "$")

    @pytest.mark.parametrize(
        "challenge",
        [
            "x" * 10000,
            "x" * 33,
            "12345678\n",
            "1234 678",
            "1234567$",
            "1234567\u00e4",
            "\u0661" * 8,
        ],
    )
    def test_md5_rejects_malformed_challenge(self, challenge):
        with pytest.raises(FritzWebUiError) as exc:
            FritzWebUiClient._md5_response(challenge, "test")
        assert len(str(exc.value)) < 200
        assert challenge.strip() not in str(exc.value)

    def test_empty_challenge_rejected(self):
        with pytest.raises(FritzWebUiError):
            FritzWebUiClient._md5_response("", "test")
        with pytest.raises(FritzWebUiError):
            FritzWebUiClient._pbkdf2_response("", "test")

    @pytest.mark.parametrize("challenge", ["deadbeef", "1234567z", "ABCDEF12", "x" * 32])
    def test_md5_accepts_documented_challenges(self, challenge):
        assert FritzWebUiClient._md5_response(challenge, "test").startswith(challenge + "-")

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_rejects_hostile_challenge_without_posting(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value
        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>2$1$aa$1$bb</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        client = FritzWebUiClient("fritz.box", "user", "pass")
        with pytest.raises(FritzWebUiError):
            client._login()
        session.post.assert_not_called()

    @pytest.mark.parametrize("challenge", ["x" * 10000, "abc\ndef", "1234 5678", "a" * 33])
    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_rejects_hostile_legacy_challenge_without_posting(
        self, mock_session_cls: MagicMock, challenge: str
    ):
        session = mock_session_cls.return_value
        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            f"<Challenge>{challenge}</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        client = FritzWebUiClient("fritz.box", "user", "pass")
        with pytest.raises(FritzWebUiError) as exc:
            client._login()
        assert str(exc.value) == "unexpected legacy login challenge format"
        session.post.assert_not_called()

    @pytest.mark.parametrize("block_time", ["x" * 10000, "60\nINJECT", "9" * 50, "-5", "6.5"])
    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_blocked_message_ignores_hostile_block_time(
        self, mock_session_cls: MagicMock, block_time: str
    ):
        session = mock_session_cls.return_value
        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID><Challenge>12345678</Challenge>"
            f"<BlockTime>{block_time}</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        client = FritzWebUiClient("fritz.box", "user", "pass")
        with pytest.raises(FritzWebUiError) as exc:
            client._login()
        assert str(exc.value) == "login blocked (too many failed attempts)"
        session.post.assert_not_called()

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_uses_pbkdf2_for_modern_firmware(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        # First GET returns a challenge (no valid SID)
        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>2$9000$00112233445566778899aabbccddeeff$1000$"
            "ffeeddccbbaa99887766554433221100</Challenge>"
            "<BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        # POST returns a valid SID
        post_resp = MagicMock()
        post_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.post.return_value = post_resp

        client = FritzWebUiClient("fritz.box", "user", "pass")
        sid = client._login()

        assert sid == "0123456789abcdef"
        # The POST must carry the PBKDF2 response
        posted_data = session.post.call_args.kwargs["data"]
        assert posted_data["response"].startswith("2$")

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_asks_for_the_pbkdf2_challenge(self, mock_session_cls: MagicMock):
        # Fritz!OS sends the PBKDF2 challenge only for version=2 and falls back to
        # MD5 otherwise, also on current firmware.
        session = mock_session_cls.return_value
        session.get.return_value = MagicMock(
            text="<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>2$60000$aabb$6000$ccdd</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.post.return_value = MagicMock(
            text="<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        )

        FritzWebUiClient("fritz.box", "user", "pass")._login()

        assert session.get.call_args.kwargs["params"] == {"version": "2", "username": "user"}
        assert session.post.call_args.kwargs["params"] == {"version": "2"}

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_without_user_name_still_asks_for_version_2(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value
        session.get.return_value = MagicMock(
            text="<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        )

        FritzWebUiClient("fritz.box", "", "pass")._login()

        assert session.get.call_args.kwargs["params"] == {"version": "2"}

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_refuses_md5_by_default(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value
        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>12345678</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        client = FritzWebUiClient("fritz.box", "user", "pass")
        with pytest.raises(FritzWebUiMd5DisabledError) as exc:
            client._login()

        # Not a FritzWebUiError: capabilities would swallow that and carry on.
        assert not isinstance(exc.value, FritzWebUiError)
        assert "fritz.box" in str(exc.value)
        assert "allow_unsafe_md5_password" in str(exc.value)
        assert "FRITZ_ALLOW_UNSAFE_MD5_PASSWORD" in str(exc.value)
        session.post.assert_not_called()

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_uses_md5_for_legacy_firmware(self, mock_session_cls: MagicMock, caplog):
        session = mock_session_cls.return_value

        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>12345678</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        post_resp = MagicMock()
        post_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.post.return_value = post_resp

        client = FritzWebUiClient("fritz.box", "user", "pass", allow_unsafe_md5_password=True)
        sid = client._login()

        assert sid == "0123456789abcdef"
        posted_data = session.post.call_args.kwargs["data"]
        assert posted_data["response"].startswith("12345678-")
        assert "unsafe legacy MD5 web login for fritz.box" in caplog.text

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_raises_on_wrong_password(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>12345678</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        post_resp = MagicMock()
        post_resp.text = "<SessionInfo><SID>0000000000000000</SID></SessionInfo>"
        session.post.return_value = post_resp

        client = FritzWebUiClient("fritz.box", "user", "wrong", allow_unsafe_md5_password=True)
        with pytest.raises(FritzWebUiError):
            client._login()

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_raises_when_blocked(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>12345678</Challenge><BlockTime>60</BlockTime></SessionInfo>"
        )
        session.get.return_value = get_resp

        client = FritzWebUiClient("fritz.box", "user", "pass")
        with pytest.raises(FritzWebUiError, match="blocked"):
            client._login()


class TestFritzWebUiClientChallenge:
    @staticmethod
    def _client(mock_session_cls: MagicMock, challenge: str, block_time: str = "0"):
        session = mock_session_cls.return_value
        session.get.return_value = MagicMock(
            text="<SessionInfo><SID>0000000000000000</SID>"
            f"<Challenge>{challenge}</Challenge><BlockTime>{block_time}</BlockTime></SessionInfo>"
        )
        session.post.return_value = MagicMock(
            text="<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        )
        return FritzWebUiClient("fritz.box", "user", "pass"), session

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_empty_challenge_names_the_cause(self, mock_session_cls: MagicMock):
        client, session = self._client(mock_session_cls, "")
        with pytest.raises(FritzWebUiError) as exc:
            client._login()
        assert str(exc.value) == "no login challenge in login_sid.lua response"
        session.post.assert_not_called()

    @pytest.mark.parametrize("challenge", ["3$60000$aabb$6000$ccdd", "1$abc", "$"])
    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_unknown_challenge_version_names_the_cause(
        self, mock_session_cls: MagicMock, challenge: str
    ):
        client, session = self._client(mock_session_cls, challenge)
        with pytest.raises(FritzWebUiError) as exc:
            client._login()
        assert str(exc.value) == "unsupported login challenge version"
        session.post.assert_not_called()

    @pytest.mark.parametrize("block_time", ["00", "000000"])
    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_zero_block_time_with_leading_zeros_is_not_a_block(
        self, mock_session_cls: MagicMock, block_time: str
    ):
        client, session = self._client(mock_session_cls, "2$60000$aabb$6000$ccdd", block_time)
        assert client._login() == "0123456789abcdef"
        session.post.assert_called_once()

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_block_time_reported_as_number(self, mock_session_cls: MagicMock):
        client, _ = self._client(mock_session_cls, "12345678", "0060")
        with pytest.raises(FritzWebUiError) as exc:
            client._login()
        assert str(exc.value) == "login blocked for 60 seconds (too many failed attempts)"

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_rejected_challenge_logged_at_debug(self, mock_session_cls: MagicMock, caplog):
        caplog.set_level(logging.DEBUG, logger="fritzexporter.fritz_webui")
        client, _ = self._client(mock_session_cls, "2$500$aabb$500$ccdd")
        with pytest.raises(FritzWebUiError):
            client._login()
        assert "Rejected login challenge from fritz.box: '2$500$aabb$500$ccdd'" in caplog.text

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_rejected_challenge_log_is_truncated_and_escaped(
        self, mock_session_cls: MagicMock, caplog
    ):
        caplog.set_level(logging.DEBUG, logger="fritzexporter.fritz_webui")
        client, _ = self._client(mock_session_cls, "x" * 10000 + "\nINJECT")
        with pytest.raises(FritzWebUiError):
            client._login()
        assert "x" * 200 in caplog.text
        assert "x" * 201 not in caplog.text
        assert "INJECT" not in caplog.text


# ---------------------------------------------------------------------------
# FritzWebUiClient data fetching
# ---------------------------------------------------------------------------


class TestFritzWebUiClientTls:
    def test_tls_skips_certificate_verification(self):
        # Fritz!Box devices serve a self-signed certificate.
        client = FritzWebUiClient("fritz.box", "user", "pw", use_tls=True)
        assert client.base_url == "https://fritz.box"
        assert client.session.verify is False


class TestFritzWebUiClientFetch:
    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_fetch_page_returns_json(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        # Login: return a valid SID directly (no challenge needed)
        get_resp = MagicMock()
        get_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.get.return_value = get_resp

        # data.lua returns the DOCSIS JSON
        post_resp = MagicMock()
        post_resp.text = '{"pid":"docInfo","data":{"readyState":"ready"}}'
        post_resp.json.return_value = {"pid": "docInfo", "data": {"readyState": "ready"}}
        session.post.return_value = post_resp

        client = FritzWebUiClient("fritz.box", "user", "pass")
        result = client.fetch_page("docInfo")

        assert result["data"]["readyState"] == "ready"
        # data.lua must be called with the docInfo page params
        posted_data = session.post.call_args.kwargs["data"]
        assert posted_data["page"] == "docInfo"
        assert posted_data["xhrId"] == "all"
        assert posted_data["xhr"] == "1"

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_fetch_raises_when_html_returned(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        get_resp = MagicMock()
        get_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.get.return_value = get_resp

        post_resp = MagicMock()
        post_resp.text = "<html><body>login page</body></html>"
        session.post.return_value = post_resp

        client = FritzWebUiClient("fritz.box", "user", "pass")
        with pytest.raises(FritzWebUiError, match="HTML"):
            client.fetch_page("docInfo")

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_fetch_retries_once_on_session_expiry(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        # First login gives a SID
        get_resp = MagicMock()
        get_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        session.get.return_value = get_resp

        # First data.lua call returns HTML (session expired), second returns JSON
        html_resp = MagicMock()
        html_resp.text = "<html>login</html>"
        json_resp = MagicMock()
        json_resp.text = '{"data":{"readyState":"ready"}}'
        json_resp.json.return_value = {"data": {"readyState": "ready"}}
        session.post.side_effect = [html_resp, json_resp]

        client = FritzWebUiClient("fritz.box", "user", "pass")
        result = client.fetch_page("docInfo")

        assert result["data"]["readyState"] == "ready"
        assert session.post.call_count == 2

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_fetch_aha_retries_once_on_forbidden(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        def sid_resp(sid: str) -> MagicMock:
            resp = MagicMock()
            resp.text = f"<SessionInfo><SID>{sid}</SID></SessionInfo>"
            return resp

        # The box answers an expired SID with 403; the client logs in again once.
        forbidden = MagicMock()
        forbidden.ok = False
        forbidden.status_code = 403
        forbidden.reason = "Forbidden"
        ok = MagicMock()
        ok.text = '<devicelist version="1"></devicelist>'
        session.get.side_effect = [
            sid_resp("0123456789abcdef"),
            forbidden,
            sid_resp("fedcba9876543210"),
            ok,
        ]

        client = FritzWebUiClient("fritz.box", "user", "pass", use_tls=True)
        result = client.fetch_aha("getdevicelistinfos")

        assert result == '<devicelist version="1"></devicelist>'
        aha_call = session.get.call_args_list[3]
        assert aha_call.args[0] == "https://fritz.box/webservices/homeautoswitch.lua"
        assert aha_call.kwargs["params"] == {
            "sid": "fedcba9876543210",
            "switchcmd": "getdevicelistinfos",
        }

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_fetch_aha_raises_on_empty_response(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value
        login = MagicMock()
        login.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        empty = MagicMock()
        empty.text = ""
        session.get.side_effect = [login, empty, login, empty]

        client = FritzWebUiClient("fritz.box", "user", "pass")
        with pytest.raises(FritzWebUiError, match="empty AHA response"):
            client.fetch_aha("getdevicelistinfos")

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_fetch_api_uses_auth_header(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        # First GET is the login_sid.lua challenge, second is the REST API call
        login_resp = MagicMock()
        login_resp.text = "<SessionInfo><SID>0123456789abcdef</SID></SessionInfo>"
        api_resp = MagicMock()
        api_resp.text = '{"connection": []}'
        api_resp.json.return_value = {"connection": []}
        session.get.side_effect = [login_resp, api_resp]

        client = FritzWebUiClient("fritz.box", "user", "pass")
        result = client.fetch_api("/api/v0/generic/connections")

        assert result == {"connection": []}
        # The REST API authenticates via the AVM-SID header
        headers = session.get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "AVM-SID 0123456789abcdef"


# ---------------------------------------------------------------------------
# Error text must not carry the request URL (SID, user name)
# ---------------------------------------------------------------------------

_SID = "0123456789abcdef"
_AHA_URL = f"http://example.invalid/webservices/homeautoswitch.lua?sid={_SID}&switchcmd=x"
_LOGIN_URL = "http://example.invalid/login_sid.lua?username=exporter"
_URL_TEXT = f"for url: {_AHA_URL} and {_LOGIN_URL}"
_SECRETS = (_SID, "username=exporter", "example.invalid", "login_sid.lua?")


def _http_error(code: int, reason: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = code
    response.reason = reason
    return requests.HTTPError(f"{code} Error: {reason} {_URL_TEXT}", response=response)


# (exception raised by the session, text the message must still carry)
_FAILURES = [
    pytest.param(
        requests.ConnectionError(f"Max retries exceeded {_URL_TEXT}"),
        "ConnectionError",
        id="connection-error",
    ),
    pytest.param(
        requests.ConnectTimeout(f"connect timed out {_URL_TEXT}"),
        "ConnectTimeout",
        id="connect-timeout",
    ),
    pytest.param(
        requests.ReadTimeout(f"read timed out {_URL_TEXT}"), "ReadTimeout", id="read-timeout"
    ),
    pytest.param(_http_error(403, "Forbidden"), "HTTP 403 Forbidden", id="http-403"),
    pytest.param(
        _http_error(500, "Internal Server Error"), "HTTP 500 Internal Server Error", id="http-500"
    ),
]


def _status_response(code: int, reason: str) -> MagicMock:
    resp = MagicMock()
    resp.ok = False
    resp.status_code = code
    resp.reason = reason
    resp.raise_for_status.side_effect = _http_error(code, reason)
    return resp


def _login_ok() -> MagicMock:
    resp = MagicMock()
    resp.text = f"<SessionInfo><SID>{_SID}</SID></SessionInfo>"
    return resp


def _assert_clean(caplog: pytest.LogCaptureFixture, err: FritzWebUiError, expected: str) -> None:
    assert err.__cause__ is None
    texts = [str(err), "".join(traceback.format_exception(err))]
    for record in caplog.records:
        texts.append(record.getMessage())
        if record.exc_info:
            texts.append("".join(traceback.format_exception(*record.exc_info)))
    for text in texts:
        for secret in _SECRETS:
            assert secret not in text
    assert expected in str(err)


def _log_like_capabilities(err: FritzWebUiError) -> None:
    """Log the way fritzcapabilities.py does, plus the exc_info variant."""
    logger = logging.getLogger("fritzexporter.fritzcapabilities")
    logger.warning("Failed to fetch home automation data from %s: %s", "fritz.box", err)
    logger.error("with traceback", exc_info=err)


@pytest.mark.parametrize(("failure", "expected"), _FAILURES)
class TestFritzWebUiErrorTextHasNoUrl:
    @pytest.fixture(autouse=True)
    def _debug_logging(self, caplog: pytest.LogCaptureFixture):
        caplog.set_level(logging.DEBUG)

    @staticmethod
    def _failing(failure: BaseException) -> MagicMock | BaseException:
        """Either raise ``failure`` from the session or answer with its status."""
        if isinstance(failure, requests.HTTPError) and failure.response is not None:
            return _status_response(failure.response.status_code, failure.response.reason)
        return failure

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_challenge(self, mock_session_cls, caplog, failure, expected):
        session = mock_session_cls.return_value
        session.get.side_effect = [self._failing(failure)] * 4
        client = FritzWebUiClient("fritz.box", "exporter", "pass")

        with pytest.raises(FritzWebUiError, match="login_sid.lua request failed") as info:
            client.fetch_aha("getdevicelistinfos")

        _log_like_capabilities(info.value)
        _assert_clean(caplog, info.value, expected)

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_post(self, mock_session_cls, caplog, failure, expected):
        session = mock_session_cls.return_value
        challenge = MagicMock()
        challenge.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>2$10000$aabb$2000$ccdd</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        )
        session.get.return_value = challenge
        session.post.side_effect = [self._failing(failure)]
        client = FritzWebUiClient("fritz.box", "exporter", "pass")

        with pytest.raises(FritzWebUiError, match="login POST failed") as info:
            client.fetch_page("docInfo")

        _log_like_capabilities(info.value)
        _assert_clean(caplog, info.value, expected)

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_data_lua(self, mock_session_cls, caplog, failure, expected):
        session = mock_session_cls.return_value
        session.get.return_value = _login_ok()
        session.post.side_effect = [self._failing(failure)] * 2
        client = FritzWebUiClient("fritz.box", "exporter", "pass")

        with pytest.raises(FritzWebUiError, match="data.lua request failed") as info:
            client.fetch_page("docInfo")

        assert session.post.call_count == 2
        _log_like_capabilities(info.value)
        _assert_clean(caplog, info.value, expected)

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_aha_retries_once(self, mock_session_cls, caplog, failure, expected):
        session = mock_session_cls.return_value
        session.get.side_effect = [
            _login_ok(),
            self._failing(failure),
            _login_ok(),
            self._failing(failure),
        ]
        client = FritzWebUiClient("fritz.box", "exporter", "pass")

        with pytest.raises(FritzWebUiError, match="AHA request failed for x") as info:
            client.fetch_aha("x")

        assert session.get.call_count == 4
        _log_like_capabilities(info.value)
        _assert_clean(caplog, info.value, expected)

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_rest_api(self, mock_session_cls, caplog, failure, expected):
        session = mock_session_cls.return_value
        session.get.side_effect = [
            _login_ok(),
            self._failing(failure),
            _login_ok(),
            self._failing(failure),
        ]
        client = FritzWebUiClient("fritz.box", "exporter", "pass")

        with pytest.raises(FritzWebUiError, match="REST API request failed for /api/v0/x") as info:
            client.fetch_api("/api/v0/x")

        assert session.get.call_count == 4
        _log_like_capabilities(info.value)
        _assert_clean(caplog, info.value, expected)


# ---------------------------------------------------------------------------
# Transport failures stay distinguishable by class name
# ---------------------------------------------------------------------------


_POOL = urllib3.HTTPConnectionPool("example.invalid")
_CONN = urllib3.connection.HTTPConnection("example.invalid")


def _max_retry(reason: Exception) -> requests.ConnectionError:
    retry_error = urllib3.exceptions.MaxRetryError(_POOL, _AHA_URL + _LOGIN_URL, reason=reason)
    return requests.ConnectionError(retry_error)


def _refused() -> requests.ConnectionError:
    cause = ConnectionRefusedError(111, f"refused {_URL_TEXT}")
    new = urllib3.exceptions.NewConnectionError(_CONN, f"cannot connect {_URL_TEXT}")
    new.__cause__ = cause
    return _max_retry(new)


def _dns() -> requests.ConnectionError:
    cause = socket.gaierror(-2, f"Name or service not known {_URL_TEXT}")
    new = urllib3.exceptions.NameResolutionError("example.invalid", _CONN, cause)
    new.__cause__ = cause
    return _max_retry(new)


def _reset() -> requests.ConnectionError:
    cause = ConnectionResetError(104, f"reset {_URL_TEXT}")
    return requests.ConnectionError(urllib3.exceptions.ProtocolError(f"aborted {_URL_TEXT}", cause))


def _unreachable() -> requests.ConnectionError:
    new = urllib3.exceptions.NewConnectionError(_CONN, f"cannot connect {_URL_TEXT}")
    # errno values differ between platforms (101 on Linux, 51 on macOS)
    new.__cause__ = OSError(errno.ENETUNREACH, f"unreachable {_URL_TEXT}")
    return _max_retry(new)


def _read_timeout() -> requests.ReadTimeout:
    inner = urllib3.exceptions.ReadTimeoutError(_POOL, _AHA_URL, f"timed out {_URL_TEXT}")
    return requests.ReadTimeout(_max_retry(inner).args[0])


_TRANSPORT = [
    pytest.param(
        _refused, "ConnectionError: NewConnectionError: ConnectionRefusedError", id="refused"
    ),
    pytest.param(_dns, "ConnectionError: NameResolutionError: gaierror", id="dns"),
    pytest.param(_reset, "ConnectionError: ProtocolError: ConnectionResetError", id="reset"),
    pytest.param(
        _unreachable, "ConnectionError: NewConnectionError: ENETUNREACH", id="unreachable"
    ),
    pytest.param(_read_timeout, "ReadTimeout: ReadTimeoutError", id="read-timeout"),
]


class TestTransportFailuresKeepTheirKind:
    @pytest.mark.parametrize(("make", "suffix"), _TRANSPORT)
    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_kind_is_named_and_nothing_else(self, mock_session_cls, caplog, make, suffix):
        caplog.set_level(logging.DEBUG)
        session = mock_session_cls.return_value
        session.get.side_effect = [_login_ok(), make(), _login_ok(), make()]
        client = FritzWebUiClient("fritz.box", "exporter", "pass")

        with pytest.raises(FritzWebUiError) as info:
            client.fetch_aha("x")

        assert session.get.call_count == 4
        _log_like_capabilities(info.value)
        _assert_clean(caplog, info.value, f"request failed ({suffix})")

    def test_kinds_are_distinguishable(self):
        texts = {
            _failure_text(make()) for make in (_refused, _dns, _reset, _unreachable, _read_timeout)
        }
        assert len(texts) == len(_TRANSPORT)

    def test_cause_chain_is_bounded_and_cycle_safe(self):
        a = requests.ConnectionError("a")
        b = OSError("b")
        a.__cause__, b.__cause__ = b, a
        assert _failure_text(a) == "request failed (ConnectionError: OSError)"
        deep = requests.RequestException("0")
        for _ in range(20):
            err = requests.RequestException("x")
            err.__cause__ = deep
            deep = err
        assert _failure_text(deep).count(":") == 4  # five names, then cut off


class TestHttpErrorText:
    @pytest.mark.parametrize(
        ("code", "reason", "expected"),
        [
            (500, "Internal Server Error", "HTTP 500 Internal Server Error"),
            (500, "", "HTTP 500 Internal Server Error"),
            (403, None, "HTTP 403 Forbidden"),
            (404, "Gone", "HTTP 404 Not Found"),
            (599, "Whatever", "HTTP 599"),
        ],
    )
    def test_standard_phrase_replaces_server_reason(self, code, reason, expected):
        resp = requests.Response()
        resp.status_code = code
        resp.reason = reason
        assert _http_error_text(resp) == expected

    def test_secret_in_server_reason_is_not_used(self):
        resp = requests.Response()
        resp.status_code = 500
        resp.reason = f"Failed /webservices/homeautoswitch.lua?sid={_SID}"
        text = _http_error_text(resp)
        assert _SID not in text
        assert text == "HTTP 500 Internal Server Error"
