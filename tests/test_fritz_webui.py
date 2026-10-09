"""Tests for the generic Fritz!Box web interface client (fritz_webui.py)."""

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
        challenge = "2$1000$00112233445566778899aabbccddeeff$1000$ffeeddccbbaa99887766554433221100"
        response = FritzWebUiClient._pbkdf2_response(challenge, "test")
        assert response == (
            "2$1000$00112233445566778899aabbccddeeff$1000$ffeeddccbbaa99887766554433221100$"
            "e039b5674c985eb212d01729920996ca45cb31635332d1b3c175d77a1b6bc0cc"
        )

    def test_pbkdf2_response_rejects_bad_challenge(self):
        with pytest.raises(FritzWebUiError):
            FritzWebUiClient._pbkdf2_response("not-a-challenge", "test")

    @patch("fritzexporter.fritz_webui.requests.Session")
    def test_login_uses_pbkdf2_for_modern_firmware(self, mock_session_cls: MagicMock):
        session = mock_session_cls.return_value

        # First GET returns a challenge (no valid SID)
        get_resp = MagicMock()
        get_resp.text = (
            "<SessionInfo><SID>0000000000000000</SID>"
            "<Challenge>2$1000$00112233445566778899aabbccddeeff$1000$"
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
    def test_login_uses_md5_for_legacy_firmware(self, mock_session_cls: MagicMock):
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

        client = FritzWebUiClient("fritz.box", "user", "pass")
        sid = client._login()

        assert sid == "0123456789abcdef"
        posted_data = session.post.call_args.kwargs["data"]
        assert posted_data["response"].startswith("12345678-")

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

        client = FritzWebUiClient("fritz.box", "user", "wrong")
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
            "<Challenge>12345678</Challenge><BlockTime>0</BlockTime></SessionInfo>"
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
    new.__cause__ = OSError(101, f"unreachable {_URL_TEXT}")
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
            (500, "", "HTTP 500"),
            (403, None, "HTTP 403"),
            (404, "Gone", "HTTP 404 Gone"),
        ],
    )
    def test_reason_is_omitted_cleanly(self, code, reason, expected):
        resp = requests.Response()
        resp.status_code = code
        resp.reason = reason
        assert _http_error_text(resp) == expected
