"""Tests for the generic Fritz!Box web interface client (fritz_webui.py)."""

from unittest.mock import MagicMock, patch

import pytest

from fritzexporter.fritz_webui import FritzWebUiClient, FritzWebUiError


# ---------------------------------------------------------------------------
# FritzWebUiClient authentication
# ---------------------------------------------------------------------------


class TestFritzWebUiClientAuth:
    def test_md5_response(self):
        # Known-good vector: challenge "12345678", password "test"
        # MD5 over UTF-16LE of "12345678-test"
        response = FritzWebUiClient._md5_response("12345678", "test")
        assert response.startswith("12345678-")
        assert len(response) == 8 + 1 + 32

    def test_pbkdf2_response(self):
        challenge = "2$1000$00112233445566778899aabbccddeeff$1000$ffeeddccbbaa99887766554433221100"
        response = FritzWebUiClient._pbkdf2_response(challenge, "test")
        assert response.startswith(challenge + "$")
        assert len(response) == len(challenge) + 1 + 64

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
