"""Fritz!Box connection endpoints: TR-064 and web UI ports, WAN remote access.

Local access uses separate ports: TR-064 on 49000 (HTTP) or 49443 (TLS), the
web UI (login, data.lua, REST API, AHA) on 80 or 443. WAN remote access serves
both on the one remote HTTPS port; TR-064 then needs the /tr064 path prefix.

See https://fritz.support/resources/TR-064_Remote_Access.pdf
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.etree.ElementTree import ParseError

import requests
from attrs import define
from fritzconnection import FritzConnection  # type: ignore[import]
from fritzconnection.core.exceptions import FritzConnectionException  # type: ignore[import]

REMOTE_TR064_PREFIX = "/tr064"
REMOTE_ACCESS_DEFAULT_PORT = 443


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
        self,
        method: str | bytes,
        url: str | bytes,
        *args: Any,  # noqa: ANN401
        **kwargs: Any,  # noqa: ANN401
    ) -> requests.Response:
        if isinstance(url, bytes):
            url = url.decode()
        url = rewrite_tr064_remote_url(url)
        return super().request(method, url, *args, **kwargs)


@contextmanager
def fritz_connection_session(*, remote_access: bool, timeout: int | None) -> Iterator[None]:
    """Configure sessions created while initializing ``FritzConnection``.

    FritzConnection creates its Session inside ``__init__`` before loading the
    router API, so the subclass must be installed before FritzConnection runs.
    Its AHA HTTP calls omit a timeout, unlike its TR-064 SOAP calls; add the
    configured timeout to every request and normalize request timeouts to the
    exception type used by the exporter.
    """
    original_session = requests.Session

    if remote_access:

        class FritzConnectionSession(Tr064RemoteAccessSession):
            def request(
                self,
                method: str | bytes,
                url: str | bytes,
                *args: Any,  # noqa: ANN401
                **kwargs: Any,  # noqa: ANN401
            ) -> requests.Response:
                if timeout is not None and kwargs.get("timeout") is None:
                    kwargs["timeout"] = timeout
                try:
                    return super().request(method, url, *args, **kwargs)
                except requests.Timeout as err:
                    msg = "Request to Fritz device timed out"
                    raise FritzConnectionException(msg) from err

    else:

        class FritzConnectionSession(requests.Session):
            def request(
                self,
                method: str | bytes,
                url: str | bytes,
                *args: Any,  # noqa: ANN401
                **kwargs: Any,  # noqa: ANN401
            ) -> requests.Response:
                if timeout is not None and kwargs.get("timeout") is None:
                    kwargs["timeout"] = timeout
                try:
                    return super().request(method, url, *args, **kwargs)
                except requests.Timeout as err:
                    msg = "Request to Fritz device timed out"
                    raise FritzConnectionException(msg) from err

    requests.Session = FritzConnectionSession  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        requests.Session = original_session


@define(frozen=True)
class ConnectionOptions:
    """TR-064 connection options shared by ``FritzDevice`` and ``create_fritz_connection``."""

    connection_timeout: int | None = None
    use_tls: bool = False
    port: int | None = None
    remote_access: bool = False

    @property
    def tr064_port(self) -> int | None:
        """TR-064 port; ``None`` lets fritzconnection pick 49000 or 49443."""
        if self.remote_access:
            return self.port or REMOTE_ACCESS_DEFAULT_PORT
        return self.port

    @property
    def web_port(self) -> int | None:
        """Web UI port; ``None`` means the scheme default (80 or 443).

        Locally, ``port`` is the TR-064 port only and does not apply here.
        """
        if self.remote_access:
            return self.port or REMOTE_ACCESS_DEFAULT_PORT
        return None


def create_fritz_connection(
    *,
    address: str,
    user: str,
    password: str,
    connection: ConnectionOptions | None = None,
) -> FritzConnection:
    """Create a FritzConnection, optionally rewriting paths for WAN remote access."""
    options = connection or ConnectionOptions()
    with fritz_connection_session(
        remote_access=options.remote_access, timeout=options.connection_timeout
    ):
        try:
            return FritzConnection(
                address=address,
                user=user,
                password=password,
                timeout=options.connection_timeout,
                use_tls=options.use_tls,
                port=options.tr064_port,
            )
        except ParseError as err:
            # Fritz returns HTML (often text/html; charset=utf-8) for missing/auth
            # paths; fritzconnection then fails XML parse instead of a typed error.
            msg = f"Invalid TR-064 response from {address} (not XML): {err}"
            raise FritzConnectionException(msg) from err
