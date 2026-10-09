import logging
from typing import Any
from unittest.mock import patch

import pytest
from fritzconnection.core.exceptions import FritzActionError
from prometheus_client.core import Metric
from requests.exceptions import ConnectionError as RequestsConnectionError

from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice
from fritzexporter.safe_url import UnsafeDevicePath, device_url

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_devices

HOST = "http://192.0.2.1"
PORT = 49000
SID_PATH = "/meshlist.lua?sid=0123456789abcdef"

TOPOLOGY = {
    "nodes": [
        {
            "uid": "n1",
            "device_name": "fritzbox",
            "is_meshed": True,
            "node_interfaces": [
                {
                    "type": "WLAN",
                    "name": "AP:5G:0",
                    "node_links": [
                        {
                            "uid": "l1",
                            "node_1_uid": "n1",
                            "node_2_uid": "n2",
                            "state": "CONNECTED",
                            "cur_data_rate_rx": 100,
                            "cur_data_rate_tx": 200,
                            "max_data_rate_rx": 300,
                            "max_data_rate_tx": 400,
                        }
                    ],
                }
            ],
        },
        {"uid": "n2", "device_name": "repeater", "is_meshed": True, "node_interfaces": []},
    ]
}

HOSTILE_PATHS = [
    "@evil.example/x",
    "//evil.example/x",
    "/a b",
    "/a#f",
    "http://evil.example/x",
    None,
    42,
    "",
    "/meshlist.lua\n",
]


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, location: str | None = None):
        self.status_code = status_code
        self.ok = status_code < 400
        self.is_redirect = location is not None
        self._payload = TOPOLOGY if payload is None else payload

    def json(self):
        return self._payload


class FakeSessions:
    """Stands in for requests.Session; records every URL that would be requested."""

    def __init__(self, response=None, error=None):
        self.urls: list[str] = []
        self.timeouts: list[object] = []
        self.verify_flags: list[object] = []
        self.redirect_flags: list[object] = []
        self.created = 0
        self.response = response or FakeResponse()
        self.error = error

    def __call__(self):
        self.created += 1
        owner = self

        class _Session:
            verify = True

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url, **kwargs):
                owner.urls.append(url)
                owner.timeouts.append(kwargs.get("timeout"))
                owner.redirect_flags.append(kwargs.get("allow_redirects"))
                owner.verify_flags.append(self.verify)
                if owner.error is not None:
                    raise owner.error
                return owner.response

        return _Session()


@pytest.fixture
def setup(request):
    """A collector with one mesh-master device; ``paths`` is the path the box reports."""
    with patch("fritzexporter.tr064_remote.FritzConnection") as mock_fritzconnection:
        state: dict[str, Any] = {"path": SID_PATH, "error": None}

        def call_with_mesh(service, action, **kwargs):
            if service == "Hosts1" and action == "X_AVM-DE_GetMeshListPath":
                if state["error"] is not None:
                    raise state["error"]
                return {"NewX_AVM-DE_MeshListPath": state["path"]}
            return call_action_mock(service, action, **kwargs)

        fc = mock_fritzconnection.return_value
        fc.call_action.side_effect = call_with_mesh
        fc.services = create_fc_services(fc_services_devices["FritzBox 7590"])
        fc.address = HOST
        fc.port = PORT
        fc.timeout = 7
        collector = FritzCollector()
        # The web UI client must never reach the network.
        with patch("fritzexporter.fritz_webui.requests.Session") as webui_session_cls:
            webui_session_cls.return_value.get.side_effect = RequestsConnectionError("offline")
            webui_session_cls.return_value.post.side_effect = RequestsConnectionError("offline")
            collector.register(
                FritzDevice(FritzCredentials("somehost", "someuser", "password"), "FritzMock")
            )
        yield collector, fc, state


def collect_mesh(collector: FritzCollector):
    metrics: list[Metric] = list(collector.collect())
    return [m for m in metrics if m.name == "fritz_mesh_link_available"][0]


def test_legitimate_path_is_fetched_with_exactly_the_device_url(setup):
    collector, fc, _ = setup
    sessions = FakeSessions()
    with patch("fritzexporter.fritzcapabilities.requests.Session", sessions):
        mesh = collect_mesh(collector)
    assert sessions.urls == [f"{HOST}:{PORT}{SID_PATH}"]
    assert sessions.timeouts == [7]
    assert sessions.verify_flags == [False]
    assert sessions.redirect_flags == [False]
    assert len(mesh.samples) == 1
    fc.session.get.assert_not_called()


def test_remote_access_requests_the_path_without_tr064_prefix(setup):
    collector, fc, _ = setup
    fc.address = "https://fritz.example.invalid"
    fc.port = 443
    sessions = FakeSessions()
    with patch("fritzexporter.fritzcapabilities.requests.Session", sessions):
        collect_mesh(collector)
    assert sessions.urls == ["https://fritz.example.invalid:443" + SID_PATH]
    assert "/tr064" not in sessions.urls[0]
    fc.session.get.assert_not_called()


def test_fixture_topology_yields_the_same_metrics_as_before(setup):
    collector, _, _ = setup
    with patch("fritzexporter.fritzcapabilities.requests.Session", FakeSessions()):
        mesh = collect_mesh(collector)
    (sample,) = mesh.samples
    assert sample.labels["node"] == "fritzbox"
    assert sample.labels["peer"] == "repeater"
    assert sample.labels["type"] == "WLAN"
    assert sample.labels["interface"] == "AP:5G:0"
    assert sample.value == 1.0


@pytest.mark.parametrize("hostile", HOSTILE_PATHS, ids=repr)
def test_hostile_path_makes_no_request_and_is_not_logged(setup, caplog, hostile):
    collector, _, state = setup
    caplog.set_level(logging.DEBUG)
    state["path"] = hostile
    sessions = FakeSessions()
    with patch("fritzexporter.fritzcapabilities.requests.Session", sessions):
        mesh = collect_mesh(collector)
        assert sessions.urls == []
        assert mesh.samples == []
        for record in caplog.records:
            text = record.getMessage() + (record.exc_text or "")
            assert "evil.example" not in text
            assert "meshlist" not in text
            if isinstance(hostile, str) and hostile:
                assert hostile not in text
        assert sum("mesh topology" in r.getMessage() for r in caplog.records) == 1

        # a later legitimate path works again
        state["path"] = SID_PATH
        mesh = collect_mesh(collector)
    assert sessions.urls == [f"{HOST}:{PORT}{SID_PATH}"]
    assert len(mesh.samples) == 1


def test_unsafe_path_warns_once_per_streak(setup, caplog):
    collector, _, state = setup
    state["path"] = "@evil.example/x"
    with patch("fritzexporter.fritzcapabilities.requests.Session", FakeSessions()):
        collect_mesh(collector)
        collect_mesh(collector)
    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "mesh" in r.getMessage()
    ]
    assert len(warnings) == 1


def test_non_master_404_stays_quiet_and_device_stays_available(setup, caplog):
    collector, _, _ = setup
    caplog.set_level(logging.DEBUG)
    sessions = FakeSessions(response=FakeResponse(404))
    with patch("fritzexporter.fritzcapabilities.requests.Session", sessions):
        metrics = list(collector.collect())
    assert not [
        r for r in caplog.records if r.levelno >= logging.WARNING and "mesh" in r.getMessage()
    ]
    assert any("not the mesh master" in r.getMessage() for r in caplog.records)
    reachable = [m for m in metrics if m.name == "fritz_device_reachable"]
    assert reachable[0].samples[0].value == 1.0


def test_action_error_stays_quiet(setup, caplog):
    collector, _, state = setup
    state["error"] = FritzActionError("no access")
    sessions = FakeSessions()
    with patch("fritzexporter.fritzcapabilities.requests.Session", sessions):
        mesh = collect_mesh(collector)
    assert sessions.urls == []
    assert mesh.samples == []
    assert not [
        r for r in caplog.records if r.levelno >= logging.WARNING and "mesh" in r.getMessage()
    ]


def test_transport_error_warns_once_without_url_and_recovers(setup, caplog):
    collector, _, _ = setup
    caplog.set_level(logging.DEBUG)
    failing = FakeSessions(error=RequestsConnectionError(f"boom {HOST}:{PORT}{SID_PATH}"))
    with patch("fritzexporter.fritzcapabilities.requests.Session", failing):
        collect_mesh(collector)
        collect_mesh(collector)
    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "mesh" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "ConnectionError" in warnings[0].getMessage()
    assert all("sid=" not in r.getMessage() + (r.exc_text or "") for r in caplog.records)

    caplog.clear()
    with patch("fritzexporter.fritzcapabilities.requests.Session", FakeSessions()):
        assert len(collect_mesh(collector).samples) == 1
    with patch("fritzexporter.fritzcapabilities.requests.Session", failing):
        collect_mesh(collector)
    assert (
        len(
            [r for r in caplog.records if r.levelno == logging.WARNING and "mesh" in r.getMessage()]
        )
        == 1
    )


def test_non_object_json_body_skips_metrics(setup):
    collector, _, _ = setup
    with patch(
        "fritzexporter.fritzcapabilities.requests.Session", FakeSessions(FakeResponse(200, [1]))
    ):
        assert collect_mesh(collector).samples == []


class TestDeviceUrl:
    def test_plain_path(self):
        assert device_url("http://192.0.2.1", 49000, "/a/b.lua") == "http://192.0.2.1:49000/a/b.lua"

    def test_path_with_query(self):
        assert (
            device_url("http://192.0.2.1", 49000, SID_PATH) == f"http://192.0.2.1:49000{SID_PATH}"
        )

    def test_https(self):
        assert (
            device_url("https://fritz.example.invalid", 443, "/x")
            == "https://fritz.example.invalid:443/x"
        )

    def test_ipv6_literal(self):
        assert (
            device_url("http://[2001:db8::1]", 49000, "/x?sid=1")
            == "http://[2001:db8::1]:49000/x?sid=1"
        )

    @pytest.mark.parametrize("hostile", HOSTILE_PATHS, ids=repr)
    def test_hostile_paths_raise_without_leaking_the_path(self, hostile):
        with pytest.raises(UnsafeDevicePath) as excinfo:
            device_url("http://192.0.2.1", 49000, hostile)
        if isinstance(hostile, str) and hostile:
            assert hostile not in str(excinfo.value)

    def test_is_a_value_error(self):
        assert issubclass(UnsafeDevicePath, ValueError)


def test_redirect_is_not_followed_and_recovers(setup, caplog):
    collector, _, _ = setup
    caplog.set_level(logging.DEBUG)
    redirect = FakeSessions(
        response=FakeResponse(302, payload={}, location="http://evil.example/x")
    )
    with patch("fritzexporter.fritzcapabilities.requests.Session", redirect):
        mesh = collect_mesh(collector)
    assert redirect.urls == [f"{HOST}:{PORT}{SID_PATH}"]
    assert mesh.samples == []
    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "mesh" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert all("evil.example" not in r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert all("sid=" not in r.getMessage() + (r.exc_text or "") for r in caplog.records)
    with patch("fritzexporter.fritzcapabilities.requests.Session", FakeSessions()):
        assert len(collect_mesh(collector).samples) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"nodes": 5},
        {"nodes": ["x"]},
        {"nodes": [{"node_interfaces": 3}]},
        {"nodes": None},
        {"nodes": [{"uid": ["a"], "is_meshed": True}]},
        {"nodes": [{"node_interfaces": [{"node_links": 1}]}]},
        {"nodes": [{"node_interfaces": [{"node_links": [{"node_1_uid": ["a"]}]}]}]},
        {"nodes": [{"node_interfaces": ["x"]}]},
    ],
    ids=repr,
)
def test_malformed_topology_skips_metrics_and_warns_once(setup, caplog, payload):
    collector, _, _ = setup
    with patch(
        "fritzexporter.fritzcapabilities.requests.Session",
        FakeSessions(FakeResponse(200, payload)),
    ):
        assert collect_mesh(collector).samples == []
        assert collect_mesh(collector).samples == []
    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "mesh" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "TypeError" in warnings[0].getMessage()


@pytest.mark.parametrize("status", [401, 500])
def test_any_non_ok_status_is_the_quiet_not_master_path(setup, caplog, status):
    collector, _, _ = setup
    caplog.set_level(logging.DEBUG)
    with patch(
        "fritzexporter.fritzcapabilities.requests.Session",
        FakeSessions(FakeResponse(status)),
    ):
        metrics = list(collector.collect())
    assert not [
        r for r in caplog.records if r.levelno >= logging.WARNING and "mesh" in r.getMessage()
    ]
    assert any("not the mesh master" in r.getMessage() for r in caplog.records)
    reachable = [m for m in metrics if m.name == "fritz_device_reachable"]
    assert reachable[0].samples[0].value == 1.0


def test_timeout_none_is_passed_through(setup):
    collector, fc, _ = setup
    fc.timeout = None
    sessions = FakeSessions()
    with patch("fritzexporter.fritzcapabilities.requests.Session", sessions):
        collect_mesh(collector)
    assert sessions.timeouts == [None]
