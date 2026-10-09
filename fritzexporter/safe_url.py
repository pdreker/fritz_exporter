"""Build URLs on the device from paths the device itself reports."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_DEVICE_PATH_RE = re.compile(r"/(?!/)[A-Za-z0-9._~/%-]*(\?[A-Za-z0-9._~%=&-]*)?")


class UnsafeDevicePath(ValueError):  # noqa: N818 - named by the interface contract
    """The device reported a path that does not stay on the device.

    The message never contains the path: it may carry a session id.
    """


def device_url(address: str, port: int | str, path: object) -> str:
    """Return ``{address}:{port}{path}`` if ``path`` cannot lead to another host.

    A path such as ``@other.example/x`` would turn the address into userinfo and
    send the request, and the credentials a session adds to it, elsewhere.
    """
    base = f"{address}:{port}"
    if not isinstance(path, str) or not _DEVICE_PATH_RE.fullmatch(path):
        msg = "the reported path is not a path on the device"
        raise UnsafeDevicePath(msg)
    url = f"{base}{path}"
    if urlsplit(url)[:2] != urlsplit(base)[:2]:
        msg = "the reported path leads away from the device"
        raise UnsafeDevicePath(msg)
    return url
