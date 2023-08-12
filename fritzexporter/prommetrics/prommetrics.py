from typing import Union

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from fritzexporter.fritzmetric import FritzDeviceMetrics

PrometheusMetric = Union[CounterMetricFamily, GaugeMetricFamily]


def _check_fritz_metrics_consistent(fritz_device_metrics: list[FritzDeviceMetrics]) -> None:
    """
    Check the list of FritzDeviceMetrics for metrics with the same name but a different promtype
    and raise ValueError if any are found. Also check for metrics with the same name but different
    attributes and raise ValueError if any are found.
    """
    for fritz_device_metric in fritz_device_metrics:
        for metric in fritz_device_metric.metrics:
            for other_metric in fritz_device_metric.metrics:
                if metric.name == other_metric.name:
                    if metric.promtype != other_metric.promtype:
                        raise ValueError(f"Metric {metric.name} has inconsistent promtypes")
                    if metric.attributes != other_metric.attributes:
                        raise ValueError(f"Metric {metric.name} has inconsistent attributes")


def create_prometheus_metrics(
    fritz_device_metrics: list[FritzDeviceMetrics],
) -> list[PrometheusMetric]:
    """Create a list of PrometheusMetric objects from a list of
    FritzDeviceMetrics objects."""
    prometheus_metrics: list[PrometheusMetric] = []

    _check_fritz_metrics_consistent(fritz_device_metrics)

    # Create a PrometheusMetric object for each FritzMetric object. Use the attributes of the
    # FritzMetric object and the FritzDeviceMetric object to set the labels of the PrometheusMetric
    # Store the PrometheusMetrics in a dictionary with the name of the metric as the key.
    # If a metric with the same name already exists, do not create a new metric.
    # Add all metrics to the list of PrometheusMetrics.
    for fritz_device_metric in fritz_device_metrics:
        for metric in fritz_device_metric.metrics:
            labels = ["serial", "model", "friendly_name"] + list(metric.attributes.keys())
            if metric.promtype == "gauge":
                prometheus_metric: PrometheusMetric = GaugeMetricFamily(
                    metric.name,
                    metric.name,
                    labels=labels,
                )
            elif metric.promtype == "counter":
                prometheus_metric = CounterMetricFamily(
                    metric.name,
                    metric.name,
                    labels=labels,
                )
            else:
                raise ValueError(f"Invalid promtype: {metric.promtype}")

            if metric.value is not None:
                prometheus_metric.add_metric(
                    labels=[
                        fritz_device_metric.serial,
                        fritz_device_metric.model,
                        fritz_device_metric.friendly_name,
                    ]
                    + list(metric.attributes.values()),
                    value=metric.value,
                )

            prometheus_metrics.append(prometheus_metric)
    return prometheus_metrics
