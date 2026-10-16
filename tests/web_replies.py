"""Replies of the Fritz!Box web interface, for tests that mock ``requests.Session``."""

import requests


def web_reply(
    text: str = "", *, status: int = 200, headers: dict[str, str] | None = None
) -> requests.Response:
    """A real reply, which the web client streams like one from the network.

    The body is kept, so a test can answer several requests with the same reply.
    """
    resp = requests.Response()
    resp.status_code = status
    resp._content = text.encode()
    resp._content_consumed = True  # ty: ignore[unresolved-attribute]
    resp.headers.update(headers or {})
    return resp
