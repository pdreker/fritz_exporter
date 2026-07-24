from unittest.mock import MagicMock, patch

import requests
from requests.adapters import HTTPAdapter

from fritzexporter.tr064_remote import (
    ConnectionOptions,
    Tr064RemoteAccessSession,
    create_fritz_connection,
    rewrite_tr064_remote_url,
)


class TestRewriteTr064RemoteUrl:
    def test_prepends_prefix_to_description(self):
        assert (
            rewrite_tr064_remote_url("https://box.example:11243/tr64desc.xml")
            == "https://box.example:11243/tr064/tr64desc.xml"
        )

    def test_prepends_prefix_to_soap_control_url(self):
        assert (
            rewrite_tr064_remote_url("https://box.example:11243/upnp/control/deviceinfo")
            == "https://box.example:11243/tr064/upnp/control/deviceinfo"
        )

    def test_prepends_prefix_to_scpd_path(self):
        assert (
            rewrite_tr064_remote_url("https://box.example:11243/deviceinfoSCPD.xml")
            == "https://box.example:11243/tr064/deviceinfoSCPD.xml"
        )

    def test_idempotent_when_prefix_already_present(self):
        url = "https://box.example:11243/tr064/tr64desc.xml"
        assert rewrite_tr064_remote_url(url) == url

    def test_preserves_query_string(self):
        assert (
            rewrite_tr064_remote_url("https://box.example:11243/foo.xml?x=1")
            == "https://box.example:11243/tr064/foo.xml?x=1"
        )


class TestTr064RemoteAccessSession:
    def test_request_rewrites_url(self):
        session = Tr064RemoteAccessSession()
        seen: list[str] = []

        def fake_request(self, method, url, *args, **kwargs):  # noqa: ANN001
            seen.append(url)
            response = MagicMock()
            response.status_code = 200
            return response

        with patch.object(requests.Session, "request", fake_request):
            session.request("GET", "https://box.example:11243/tr64desc.xml")

        assert seen == ["https://box.example:11243/tr064/tr64desc.xml"]

    def test_rewrite_survives_fritzconnection_adapter_remount(self):
        """FritzConnection replaces mounted adapters after Session().

        A Session.request override must still rewrite URLs after that remount.
        """
        session = Tr064RemoteAccessSession()
        session.mount("https://", HTTPAdapter())  # simulates FritzConnection
        seen: list[str] = []

        def fake_request(self, method, url, *args, **kwargs):  # noqa: ANN001
            seen.append(url)
            response = MagicMock()
            response.status_code = 200
            return response

        with patch.object(requests.Session, "request", fake_request):
            session.request("GET", "https://box.example:11243/igddesc.xml")

        assert seen == ["https://box.example:11243/tr064/igddesc.xml"]
        assert type(session.adapters["https://"]) is HTTPAdapter


class TestCreateFritzConnection:
    @patch("fritzexporter.tr064_remote.FritzConnection")
    def test_remote_access_uses_rewriting_session(self, mock_fc: MagicMock):
        captured_sessions: list[requests.Session] = []

        def fake_fc(**_kwargs: object) -> MagicMock:
            session = requests.Session()
            # Simulate FritzConnection remounting a plain adapter
            session.mount("https://", HTTPAdapter())
            captured_sessions.append(session)
            return MagicMock()

        mock_fc.side_effect = fake_fc

        create_fritz_connection(
            address="box.example",
            user="user",
            password="pass",
            connection=ConnectionOptions(use_tls=True, port=11243, remote_access=True),
        )

        assert len(captured_sessions) == 1
        assert isinstance(captured_sessions[0], Tr064RemoteAccessSession)
        mock_fc.assert_called_once_with(
            address="box.example",
            user="user",
            password="pass",
            timeout=None,
            use_tls=True,
            port=11243,
        )

    @patch("fritzexporter.tr064_remote.FritzConnection")
    def test_parse_error_becomes_fritz_connection_exception(self, mock_fc: MagicMock):
        from xml.etree.ElementTree import ParseError

        from fritzconnection.core.exceptions import FritzConnectionException
        import pytest

        mock_fc.side_effect = ParseError("not well-formed")

        with pytest.raises(FritzConnectionException, match="not XML"):
            create_fritz_connection(
                address="box.example",
                user="user",
                password="pass",
                connection=ConnectionOptions(use_tls=True, port=11243, remote_access=True),
            )
