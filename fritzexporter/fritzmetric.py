from typing import Optional

from attrs import define


@define
class FritzMetric:
    name: str
    value: Optional[float] = None
    unit: Optional[str] = None
    attributes: dict[str, str] = {}


@define
class FritzDeviceMetrics:
    """_summary_: A list of FritzMetric objects."""

    serial: Optional[str] = None
    model: Optional[str] = None
    friendly_name: Optional[str] = None

    metrics: list[FritzMetric] = []
