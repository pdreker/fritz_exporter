"""AVM WAN remote TR-064 URL rewriting (/tr064 path prefix).

See https://fritz.support/resources/TR-064_Remote_Access.pdf
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit
from xml.etree.ElementTree import ParseError

import requests
from fritzconnection import FritzConnection  # type: ignore[import]
from fritzconnection.core.exceptions import FritzConnectionException  # type: ignore[import]

REMOTE_TR064_PREFIX = "/tr064"


def rewrite_tr064_remote_url(url: str) -> str:
    """Prepend /tr064 to the URL path unless it is already present."""
    parts = urlsplit(url)
    path = parts.path or "/"
    if path == REMOTE_TR064_PREFIX or path.startswith(f"{REMOTE_TR064_PREFIX}/"):
        return url
    if path.startswith("/"):
        new_path = f"{REMOTE_TR064_PREFIX}{path}"
    else:
        new_path = f"{REMOTE_TR064_PREFIX}/{path}"
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


class Tr064RemoteAccessSession(requests.Session):
    """Session that rewrites TR-064 paths for AVM WAN remote access.

    FritzConnection mounts its own ``HTTPAdapter`` after constructing the
    session, which would replace an adapter-based rewrite. Overriding
    ``request`` keeps the ``/tr064`` prefix regardless of later mounts.
    """

    def request(
        self, method: str | bytes, url: str | bytes, *args: object, **kwargs: object
    ) -> requests.Response:
        if isinstance(url, bytes):
            url = url.decode()
        url = rewrite_tr064_remote_url(url)
        return super().request(method, url, *args, **kwargs)  # type: ignore[arg-type]


@contextmanager
def remote_tr064_session(*, enabled: bool) -> Iterator[None]:
    """While enabled, make ``requests.Session`` the remote-access subclass.

    FritzConnection creates its Session inside ``__init__`` before loading the
    router API, so the subclass must be installed before FritzConnection runs.
    """
    if not enabled:
        yield
        return

    original_session = requests.Session
    requests.Session = Tr064RemoteAccessSession  # type: ignore[misc,assignment]
    try:
        yield
    finally:
        requests.Session = original_session  # type: ignore[misc]


def create_fritz_connection(  # noqa: PLR0913
    *,
    address: str,
    user: str,
    password: str,
    timeout: float | tuple[float, float] | None,
    use_tls: bool,
    port: int | None,
    remote_access: bool = False,
) -> FritzConnection:
    """Create a FritzConnection, optionally rewriting paths for WAN remote access."""
    with remote_tr064_session(enabled=remote_access):
        try:
            return FritzConnection(
                address=address,
                user=user,
                password=password,
                timeout=timeout,
                use_tls=use_tls,
                port=port,
            )
        except ParseError as err:
            # Fritz returns HTML (often text/html; charset=utf-8) for missing/auth
            # paths; fritzconnection then fails XML parse instead of a typed error.
            msg = f"Invalid TR-064 response from {address} (not XML): {err}"
            raise FritzConnectionException(msg) from err
