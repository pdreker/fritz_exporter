"""Collect the metrics of one fake device, for tests of single capabilities."""

from collections.abc import Callable, Mapping
from typing import Any
from unittest.mock import MagicMock

from prometheus_client.core import Metric

from fritzexporter.fritzdevice import FritzCollector, FritzCredentials, FritzDevice

from .fc_services_mock import call_action_mock, create_fc_services, fc_services_capabilities

# A reply for one (service, action): a dict is laid over the mock's reply, a
# callable gets the action's arguments and returns the whole reply (or raises).
Override = Mapping[str, Any] | Callable[..., Any]

CABLE = {("WANCommonInterfaceConfig1", "GetCommonLinkProperties"): {"NewWANAccessType": "X_AVM-DE_Cable"}}


def sample_map(metric: Metric) -> dict[tuple[tuple[str, str], ...], float]:
    """The metric's values by their sorted label pairs."""
    return {tuple(sorted(sample.labels.items())): sample.value for sample in metric.samples}


def services(*capabilities: str, **extra: list[str]) -> dict[str, list[str]]:
    """The TR-064 services of the mock's ``capabilities``, plus ``extra`` services and actions."""
    merged: dict[str, list[str]] = {}
    for capability in capabilities:
        merged.update(fc_services_capabilities[capability])
    return {**merged, **extra}


def fake_actions(overrides: Mapping[tuple[str, str], Override] | None = None) -> Callable[..., Any]:
    """``call_action_mock`` with the replies in ``overrides`` replaced."""
    overrides = overrides or {}

    def call_action(service: str, action: str, **kwargs: Any) -> Any:  # noqa: ANN401
        override = overrides.get((service, action))
        if callable(override):
            return override(**kwargs)
        result = call_action_mock(service, action, **kwargs)
        return {**result, **override} if override is not None else result

    return call_action


def fake_device(
    mock_fritzconnection: MagicMock,
    device_services: dict[str, list[str]],
    *,
    overrides: Mapping[tuple[str, str], Override] | None = None,
    name: str = "FritzMock",
    host_info: bool = False,
) -> FritzDevice:
    """One device set up on the patched ``FritzConnection``."""
    fc = mock_fritzconnection.return_value
    fc.call_action.side_effect = fake_actions(overrides)
    fc.services = create_fc_services(device_services)
    return FritzDevice(FritzCredentials("somehost", "someuser", "password"), name, host_info=host_info)


def collect_device(  # noqa: PLR0913
    mock_fritzconnection: MagicMock,
    device_services: dict[str, list[str]],
    *,
    overrides: Mapping[tuple[str, str], Override] | None = None,
    webui_client: MagicMock | None = None,
    name: str = "FritzMock",
    host_info: bool = False,
    scrapes: int = 1,
) -> dict[str, Metric]:
    """Set up one device and scrape it; the last scrape's metrics by name.

    ``webui_client`` replaces the device's web client; ``None`` keeps the real one.
    """
    device = fake_device(
        mock_fritzconnection, device_services, overrides=overrides, name=name, host_info=host_info
    )
    if webui_client is not None:
        device.webui_client = webui_client
    collector = FritzCollector()
    collector.register(device)
    for _ in range(scrapes - 1):
        list(collector.collect())
    return {metric.name: metric for metric in collector.collect()}
