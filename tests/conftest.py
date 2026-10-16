"""Shared test setup."""

import pytest
import requests


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail every real HTTP request.

    Test configs use host names such as ``fritz.box``, which resolve on a real
    home network; a test that forgets to mock the web UI client would otherwise
    send logins with test credentials to a real device and get it to block logins.
    Tests that replace ``requests.Session`` with a mock are not affected.
    """

    def refuse(self, request, **kwargs):
        raise requests.ConnectionError(f"network access in tests: {request.method} {request.url}")

    monkeypatch.setattr(requests.Session, "send", refuse)
