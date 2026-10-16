"""Write the Grafana dashboards in this directory.

    uv run python misc/grafana/build_dashboards.py

* ``fritz-exporter-overview.json`` shows every metric the exporter has, grouped by
  topic, with a range of panel types: a reference of what is there and what
  Grafana can make of it.
* ``fritz-exporter-home.json`` shows what is useful every day, on one screen.

Edit this file, not the JSON: ``tests/test_grafana_dashboards.py`` checks that
the JSON is up to date and that the overview uses every metric.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent

DS = {"type": "prometheus", "uid": "${datasource}"}
DEV = 'friendly_name="$device"'
GRID_WIDTH = 24

Panel = dict[str, Any]


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def target(
    expr: str, legend: str = "", *, table: bool = False, now: bool = False, ref: str = "A"
) -> dict:
    """A Prometheus query.

    ``table`` asks for the current values as a table; ``now`` for the current
    values only, so a label that changed (a new Fritz!OS version) shows once.
    """
    query: dict[str, Any] = {"datasource": DS, "expr": expr, "refId": ref}
    if table:
        query.update(format="table", instant=True, range=False)
    else:
        query["legendFormat"] = legend or "__auto"
        if now:
            query.update(instant=True, range=False)
    return query


def targets(*queries: tuple[str, str] | str, table: bool = False) -> list[dict]:
    """Several queries, lettered A, B, ...; each a bare expression or (expr, legend)."""
    result = []
    for index, query in enumerate(queries):
        expr, legend = (query, "") if isinstance(query, str) else query
        result.append(target(expr, legend, table=table, ref=chr(ord("A") + index)))
    return result


def now_only(queries: list[dict]) -> list[dict]:
    """The queries as instant queries: the current values only."""
    return [{**query, "instant": True, "range": False} for query in queries]


def thresholds(*steps: tuple[float | None, str]) -> dict:
    return {
        "mode": "absolute",
        "steps": [{"color": color, "value": value} for value, color in steps],
    }


def mapping(values: dict[str, tuple[str, str]]) -> list[dict]:
    """Value mappings: raw value -> (text, color)."""
    return [
        {
            "type": "value",
            "options": {
                raw: {"text": text, "color": color, "index": index}
                for index, (raw, (text, color)) in enumerate(values.items())
            },
        }
    ]


UP_DOWN = mapping({"1": ("Up", "green"), "0": ("Down", "red")})
ON_OFF = mapping({"1": ("On", "green"), "0": ("Off", "text")})
LINK_STATE = mapping({"1": ("connected", "green"), "0": ("down", "red")})
YES_NO = mapping({"1": ("Yes", "orange"), "0": ("No", "green")})
CONNECTION_MODES = mapping(
    {
        "0": ("Offline", "red"),
        "1": ("DSL", "green"),
        "2": ("Mobile fallback", "orange"),
        "3": ("Mobile only", "yellow"),
        "4": ("Fibre", "green"),
        "5": ("Cable", "green"),
    }
)
HIDDEN_COLUMNS = ["Time", "__name__", "job", "instance", "source", "serial", "friendly_name"]


def panel(  # noqa: PLR0913
    kind: str,
    title: str,
    queries: list[dict],
    *,
    description: str = "",
    unit: str | None = None,
    defaults: dict | None = None,
    options: dict | None = None,
    overrides: list | None = None,
    transformations: list | None = None,
) -> Panel:
    field: dict[str, Any] = dict(defaults or {})
    if unit:
        field["unit"] = unit
    result: Panel = {
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": queries,
        "fieldConfig": {"defaults": field, "overrides": overrides or []},
        "options": options or {},
    }
    if transformations:
        result["transformations"] = transformations
    return result


def reduce_last() -> dict:
    return {"calcs": ["lastNotNull"], "fields": "", "values": False}


def timeseries(  # noqa: PLR0913
    title: str,
    queries: list[dict],
    *,
    unit: str = "short",
    description: str = "",
    fill: int = 15,
    stack: bool = False,
    legend: str = "list",
    calcs: list[str] | None = None,
    overrides: list | None = None,
    draw: str = "line",
    decimals: int | None = None,
    soft_min: float | None = None,
) -> Panel:
    custom: dict[str, Any] = {
        "drawStyle": draw,
        "lineWidth": 2 if draw == "line" else 1,
        "fillOpacity": fill,
        "gradientMode": "opacity",
        "showPoints": "never",
        "spanNulls": True,
        "axisSoftMin": soft_min,
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
    }
    defaults: dict[str, Any] = {"custom": custom, "color": {"mode": "palette-classic"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return panel(
        "timeseries",
        title,
        queries,
        description=description,
        unit=unit,
        defaults=defaults,
        overrides=overrides,
        options={
            "legend": {
                "displayMode": legend,
                "placement": "bottom",
                "calcs": calcs or ([] if legend == "list" else ["lastNotNull", "max"]),
                "showLegend": True,
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    )


def stat(  # noqa: PLR0913
    title: str,
    queries: list[dict],
    *,
    unit: str = "short",
    description: str = "",
    color: str = "value",
    graph: bool = False,
    text: str = "auto",
    steps: dict | None = None,
    mappings: list | None = None,
    decimals: int | None = None,
    fixed: str | None = None,
    overrides: list | None = None,
    orientation: str = "auto",
) -> Panel:
    defaults: dict[str, Any] = {
        # "No data" in the large stat font drowns the panels that have data.
        "noValue": "-",
        "thresholds": steps or thresholds((None, "green")),
        "mappings": mappings or [],
        "color": {"mode": "fixed", "fixedColor": fixed} if fixed else {"mode": "thresholds"},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return panel(
        "stat",
        title,
        queries,
        description=description,
        unit=unit,
        defaults=defaults,
        overrides=overrides,
        options={
            "reduceOptions": reduce_last(),
            "colorMode": color,
            "graphMode": "area" if graph else "none",
            "textMode": text,
            "justifyMode": "center",
            "orientation": orientation,
            "wideLayout": True,
        },
    )


def label_stat(title: str, expr: str, legend: str, *, description: str = "") -> Panel:
    """Show a label (model, firmware, address) as the panel's text."""
    return stat(
        title,
        [target(expr, legend, now=True)],
        description=description,
        text="name",
        color="none",
    )


def gauge(  # noqa: PLR0913
    title: str,
    queries: list[dict],
    *,
    unit: str = "short",
    minimum: float = 0,
    maximum: float = 100,
    steps: dict | None = None,
    description: str = "",
    decimals: int | None = None,
) -> Panel:
    defaults: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "thresholds": steps or thresholds((None, "green"), (70, "orange"), (90, "red")),
        "color": {"mode": "thresholds"},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return panel(
        "gauge",
        title,
        queries,
        description=description,
        unit=unit,
        defaults=defaults,
        options={
            "reduceOptions": reduce_last(),
            "showThresholdMarkers": True,
            "showThresholdLabels": False,
            "orientation": "auto",
        },
    )


def bars(  # noqa: PLR0913
    title: str,
    queries: list[dict],
    *,
    unit: str = "short",
    minimum: float | None = 0,
    maximum: float | None = None,
    steps: dict | None = None,
    mode: str = "gradient",
    scheme: str | None = None,
    description: str = "",
) -> Panel:
    defaults: dict[str, Any] = {
        "thresholds": steps or thresholds((None, "green")),
        "color": {"mode": scheme} if scheme else {"mode": "thresholds"},
    }
    if minimum is not None:
        defaults["min"] = minimum
    if maximum is not None:
        defaults["max"] = maximum
    # A bar gauge shows one value per series: the current one. Instant queries
    # also keep series out that stopped, e.g. after a label changed.
    return panel(
        "bargauge",
        title,
        now_only(queries),
        description=description,
        unit=unit,
        defaults=defaults,
        options={
            "reduceOptions": reduce_last(),
            "displayMode": mode,
            "orientation": "horizontal",
            "showUnfilled": True,
            "valueMode": "color",
            "namePlacement": "auto",
            "sizing": "auto",
        },
    )


def table(  # noqa: PLR0913
    title: str,
    queries: list[dict],
    *,
    rename: dict[str, str],
    description: str = "",
    overrides: list | None = None,
    sort: str | None = None,
    hide: list[str] | None = None,
) -> Panel:
    """A table of current values; ``rename`` also gives the column order."""
    transformations: list[dict] = []
    if len(queries) > 1:
        # "merge" joins rows whose shared columns agree; the metric name differs
        # between the queries, so drop it ("+ 0").
        queries = [{**query, "expr": f"{query['expr']} + 0"} for query in queries]
        transformations.append({"id": "merge", "options": {}})
    excluded = dict.fromkeys([*HIDDEN_COLUMNS, *(hide or [])], True)
    transformations.append(
        {
            "id": "organize",
            "options": {
                "excludeByName": excluded,
                "renameByName": rename,
                "indexByName": {name: index for index, name in enumerate(rename)},
            },
        }
    )
    options: dict[str, Any] = {"showHeader": True, "cellHeight": "sm"}
    if sort:
        options["sortBy"] = [{"displayName": sort, "desc": True}]
    return panel(
        "table",
        title,
        queries,
        description=description,
        defaults={"custom": {"align": "auto", "filterable": True}},
        overrides=overrides,
        options=options,
        transformations=transformations,
    )


def timeline(
    title: str,
    queries: list[dict],
    *,
    mappings: list,
    description: str = "",
) -> Panel:
    return panel(
        "state-timeline",
        title,
        queries,
        description=description,
        # No color mode: with "thresholds" the timeline labels values by their
        # threshold range ("< 80") and ignores the mappings.
        defaults={"mappings": mappings, "custom": {"fillOpacity": 80}},
        options={
            "mergeValues": True,
            "showValue": "auto",
            "alignValue": "center",
            "rowHeight": 0.85,
            "legend": {"showLegend": False},
        },
    )


def text(title: str, content: str) -> Panel:
    return {
        "type": "text",
        "title": title,
        "options": {"mode": "markdown", "content": content},
    }


def column(name: str, **props: Any) -> dict:  # noqa: ANN401
    """Override for one table column (or series) by its name."""
    return {
        "matcher": {"id": "byName", "options": name},
        "properties": [{"id": key, "value": value} for key, value in props.items()],
    }


def series_regex(pattern: str, **props: Any) -> dict:  # noqa: ANN401
    return {
        "matcher": {"id": "byRegexp", "options": pattern},
        "properties": [{"id": key, "value": value} for key, value in props.items()],
    }


def dashed(pattern: str, dash: tuple[int, int] = (10, 10)) -> dict:
    """Draw matching series as dashed lines without fill: limits, maxima, set points."""
    return series_regex(
        pattern,
        **{"custom.lineStyle": {"fill": "dash", "dash": list(dash)}, "custom.fillOpacity": 0},
    )


def upload_below(pattern: str = ".*(tx|[Uu]pload|[Uu]pstream).*") -> dict:
    """Draw upload below the axis, so download and upload mirror each other."""
    return series_regex(pattern, **{"custom.transform": "negative-Y"})


def cell(kind: str, **extra: Any) -> dict:  # noqa: ANN401
    return {"type": kind, **extra}


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


class Layout:
    """Places panels left to right in rows of 24 units; rows may be collapsed."""

    def __init__(self) -> None:
        self.panels: list[Panel] = []
        self.x = 0
        self.y = 0
        self.line_height = 0
        self.row: Panel | None = None
        self.next_id = 1

    def _break(self) -> None:
        if self.x:
            self.y += self.line_height
            self.x = 0
            self.line_height = 0

    def row_start(self, title: str, *, collapsed: bool = False) -> None:
        self._break()
        row: Panel = {
            "type": "row",
            "title": title,
            "collapsed": collapsed,
            "id": self._id(),
            "gridPos": {"h": 1, "w": GRID_WIDTH, "x": 0, "y": self.y},
            "panels": [],
        }
        self.panels.append(row)
        self.row = row if collapsed else None
        self.y += 1

    def add(self, item: Panel, w: int, h: int) -> None:
        if self.x + w > GRID_WIDTH:
            self._break()
        item = {**item, "id": self._id(), "gridPos": {"h": h, "w": w, "x": self.x, "y": self.y}}
        (self.row["panels"] if self.row is not None else self.panels).append(item)
        self.x += w
        self.line_height = max(self.line_height, h)
        if self.x >= GRID_WIDTH:
            self._break()

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id - 1


def dashboard(uid: str, title: str, description: str, layout: Layout, *, refresh: str) -> dict:
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": ["fritz-exporter", "fritzbox"],
        "editable": True,
        "graphTooltip": 1,
        "schemaVersion": 41,
        "version": 1,
        "refresh": refresh,
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "annotations": {"list": []},
        "links": [
            {
                "title": "Fritz!Exporter dashboards",
                "type": "dashboards",
                "tags": ["fritz-exporter"],
                "asDropdown": True,
                "includeVars": True,
                "keepTime": True,
                "icon": "external link",
                "targetBlank": False,
            },
            {
                "title": "Metrics reference",
                "type": "link",
                "url": "https://fritz-exporter.readthedocs.io/en/latest/",
                "icon": "doc",
                "targetBlank": True,
            },
        ],
        "templating": {"list": variables()},
        "panels": layout.panels,
    }


def variables() -> list[dict]:
    query = "label_values(fritz_device_reachable, friendly_name)"
    return [
        {
            "name": "datasource",
            "label": "Data source",
            "type": "datasource",
            "query": "prometheus",
            "current": {},
            "hide": 0,
            "refresh": 1,
            "options": [],
        },
        {
            "name": "device",
            "label": "Device",
            "type": "query",
            "datasource": DS,
            "definition": query,
            "query": {"query": query, "refId": "device"},
            "refresh": 2,
            "sort": 1,
            "current": {},
            "includeAll": False,
            "multi": False,
            "hide": 0,
            "options": [],
        },
    ]


def q(metric: str, **labels: str) -> str:
    """``metric`` of the selected device, with more label matchers from ``labels``."""
    matchers = [DEV, *(f'{name}="{value}"' for name, value in labels.items())]
    return f"{metric}{{{','.join(matchers)}}}"


def joined(expr: str, info: str, on: str, *labels: str) -> str:
    """``expr`` with ``labels`` from the ``info`` metric (value 1), joined ``on`` its keys."""
    return f"{expr} * on ({on}) group_left ({', '.join(labels)}) {q(info)}"


WIFI_SSID = ("fritz_wifi_info", "serial, wifi_index", "ssid")


def rate(metric: str, **labels: str) -> str:
    return f"rate({q(metric, **labels)}[$__rate_interval])"


# ---------------------------------------------------------------------------
# Overview: every metric, grouped by topic
# ---------------------------------------------------------------------------


def overview() -> dict:  # noqa: PLR0915
    lay = Layout()

    lay.row_start("Device and exporter")
    lay.add(
        stat(
            "Reachable", [target(q("fritz_device_reachable"))], mappings=UP_DOWN, color="background"
        ),
        3,
        4,
    )
    lay.add(
        stat("Uptime", [target(q("fritz_uptime_seconds_total"), now=True)], unit="s", decimals=1),
        3,
        4,
    )
    lay.add(label_stat("Model", q("fritz_device_info"), "{{modelname}}"), 4, 4)
    lay.add(label_stat("Fritz!OS", q("fritz_device_info"), "{{softwareversion}}"), 3, 4)
    lay.add(
        label_stat(
            "New Fritz!OS",
            joined(
                q("fritz_update_available"), "fritz_update_info", "serial", "newsoftwareversion"
            ),
            "{{newsoftwareversion}}",
            description="The version of an available update; empty when there is none.",
        ),
        4,
        4,
    )
    lay.add(
        stat(
            "Connection mode",
            [target(q("fritz_connection_mode"), now=True)],
            mappings=CONNECTION_MODES,
            color="background",
        ),
        4,
        4,
    )
    lay.add(
        stat(
            "Read problems",
            [target(f"count(fritz_read_problem{{{DEV}}}) or vector(0)")],
            steps=thresholds((None, "green"), (1, "orange")),
            color="background",
            description="Metric groups the exporter could not read in full in the last scrape.",
        ),
        3,
        4,
    )
    lay.add(
        table(
            "Read problems",
            [target(q("fritz_read_problem"), table=True)],
            rename={"capability": "Metric group", "level": "Level"},
            hide=["Value"],
            description="partial = some values or items, skipped = the whole group, "
            "error = a bug in the exporter. The log has the details.",
        ),
        8,
        6,
    )
    lay.add(
        timeseries(
            "Completed scrapes",
            targets((f"rate({q('fritz_scrapes_total')}[$__rate_interval]) * 60", "per minute")),
            unit="short",
            description="Scrapes of this device per minute.",
        ),
        8,
        6,
    )
    lay.add(
        timeseries(
            "Exporter: timed out and failed scrapes",
            targets(
                (
                    "increase(fritz_scrapes_timeouts_total[$__rate_interval])",
                    "timed out {{instance}}",
                ),
                ("increase(fritz_scrapes_failed_total[$__rate_interval])", "failed {{instance}}"),
            ),
            draw="bars",
            fill=80,
            description="For the whole exporter, not per device.",
        ),
        8,
        6,
    )
    lay.add(stat("Known hosts", [target(q("fritz_known_devices_count"))], graph=True), 4, 4)
    lay.add(stat("Telephone numbers", [target(q("fritz_telephony_numbers"))]), 4, 4)

    lay.row_start("Internet")
    lay.add(
        timeseries(
            "Current data rate",
            targets(
                (f"{q('fritz_wan_datarate_bytes', direction='rx')} * 8", "download"),
                (f"{q('fritz_wan_datarate_bytes', direction='tx')} * 8", "upload"),
            ),
            unit="bps",
            overrides=[upload_below()],
            fill=25,
            legend="table",
            description="As reported by the device (fritz_wan_datarate_bytes); "
            "upload is drawn below.",
        ),
        12,
        8,
    )
    lay.add(
        timeseries(
            "Traffic from the byte counters",
            targets(
                (
                    f"{rate('fritz_wan_data_bytes_total', direction='rx')} * 8",
                    "download",
                ),
                (
                    f"{rate('fritz_wan_data_bytes_total', direction='tx')} * 8",
                    "upload",
                ),
            ),
            unit="bps",
            overrides=[upload_below()],
            fill=25,
            legend="table",
            description="rate() of fritz_wan_data_bytes_total: the average over the interval.",
        ),
        12,
        8,
    )
    lay.add(
        timeseries(
            "Packets",
            targets((rate("fritz_wan_data_packets_count_total"), "{{direction}}")),
            unit="pps",
            overrides=[upload_below()],
        ),
        8,
        6,
    )
    lay.add(
        bars(
            "Maximum bit rate",
            targets(
                (q("fritz_wan_layer1_max_bitrate_bps"), "layer 1 {{direction}}"),
                (q("fritz_wan_max_bitrate_bps"), "line {{direction}}"),
            ),
            unit="bps",
            scheme="continuous-BlPu",
        ),
        8,
        6,
    )
    lay.add(
        stat(
            "Physical link",
            [
                target(
                    joined(
                        q("fritz_wan_phys_link_status"),
                        "fritz_wan_access_type_info",
                        "serial",
                        "access_type",
                    ),
                    "{{access_type}}",
                )
            ],
            mappings=UP_DOWN,
            color="background",
            text="value_and_name",
        ),
        4,
        6,
    )
    lay.add(
        stat(
            "PPP",
            [
                target(
                    joined(
                        q("fritz_ppp_connection_state"),
                        "fritz_ppp_connection_info",
                        "serial",
                        "last_error",
                    ),
                    "last error: {{last_error}}",
                    now=True,
                )
            ],
            mappings=mapping({"1": ("Connected", "green"), "0": ("Disconnected", "red")}),
            color="background",
            text="value_and_name",
        ),
        4,
        6,
    )
    lay.add(
        stat(
            "PPP uptime",
            [target(q("fritz_ppp_connection_uptime_seconds_total"))],
            unit="s",
            decimals=1,
        ),
        4,
        7,
    )
    lay.add(
        table(
            "Connections",
            targets(
                q("fritz_wan_connection_status"),
                q("fritz_wan_connection_uptime_seconds_total"),
                q("fritz_wan_connection_info"),
                table=True,
            ),
            rename={
                "connection_name": "Connection",
                "media_type": "Medium",
                "stack": "Stack",
                "state": "State",
                "Value #B": "Uptime",
                "ip_address": "Address",
            },
            hide=["connection", "Value #A", "Value #C"],
            overrides=[
                column("Uptime", unit="s", decimals=0),
                column(
                    "State",
                    **{
                        "custom.cellOptions": cell("color-text"),
                        "mappings": mapping(
                            {
                                "connected": ("connected", "green"),
                                "disabled": ("disabled", "text"),
                                "connecting": ("connecting", "orange"),
                                "disconnected": ("disconnected", "red"),
                            }
                        ),
                    },
                ),
            ],
            description="All WAN connections from the REST API, also the disabled ones.",
        ),
        12,
        7,
    )
    lay.add(
        table(
            "IPv6 prefix",
            [target(q("fritz_wan_ipv6_prefix_info"), table=True)],
            rename={"prefix": "Prefix", "prefix_length": "Length"},
            hide=["Value"],
        ),
        8,
        7,
    )
    lay.add(
        timeseries(
            "Connection uptime",
            targets(
                (q("fritz_wan_connection_uptime_seconds_total"), "{{connection_name}} {{stack}}")
            ),
            unit="s",
            description="Drops to 0 on a reconnect.",
        ),
        24,
        6,
    )

    lay.row_start("DSL", collapsed=True)
    lay.add(stat("DSL enabled", [target(q("fritz_dsl_status_enabled"))], mappings=ON_OFF), 3, 4)
    lay.add(
        stat("DSL line", [target(q("fritz_dsl_status"))], mappings=UP_DOWN, color="background"),
        3,
        4,
    )
    for direction, name in (("rx", "Downstream"), ("tx", "Upstream")):
        lay.add(
            gauge(
                f"{name} sync / attainable",
                [
                    target(
                        f"{q('fritz_dsl_datarate_kbps', direction=direction, type='curr')}"
                        " / ignoring (type) "
                        f"{q('fritz_dsl_datarate_kbps', direction=direction, type='max')}"
                        " * 100"
                    )
                ],
                unit="percent",
                steps=thresholds((None, "red"), (70, "orange"), (85, "green")),
                description="How much of the attainable rate the line is synced at.",
            ),
            3,
            4,
        )
    lay.add(
        timeseries(
            "Sync rate",
            targets((q("fritz_dsl_datarate_kbps"), "{{direction}} {{type}}")),
            unit="Kbits",
            legend="table",
        ),
        12,
        8,
    )
    lay.add(
        timeseries(
            "Noise margin",
            targets((q("fritz_dsl_noise_margin_dB"), "{{direction}}")),
            unit="dB",
            soft_min=0,
            description="Higher is better; below about 6 dB the line may drop.",
        ),
        6,
        4,
    )
    lay.add(
        timeseries(
            "Attenuation", targets((q("fritz_dsl_attenuation_dB"), "{{direction}}")), unit="dB"
        ),
        6,
        4,
    )
    lay.add(
        timeseries(
            "Errors",
            targets(
                (
                    f"increase({q('fritz_dsl_fec_errors_count_total')}[$__rate_interval])",
                    "FEC (corrected)",
                ),
                (f"increase({q('fritz_dsl_crc_errors_count_total')}[$__rate_interval])", "CRC"),
            ),
            draw="bars",
            fill=80,
        ),
        24,
        5,
    )

    lay.row_start("Cable (DOCSIS)", collapsed=True)
    for direction in ("downstream", "upstream"):
        lay.add(
            timeseries(
                f"{direction.capitalize()} power",
                targets(
                    (
                        q("fritz_docsis_power_dBmV", direction=direction),
                        "{{standard}} ch {{channel_id}}",
                    )
                ),
                unit="suffix: dBmV",
            ),
            8,
            7,
        )
    lay.add(
        timeseries(
            "Shared segment utilization",
            targets((q("fritz_cable_segment_utilization_percent"), "{{scope}} {{direction}}")),
            unit="percent",
            description="own = this box, total = everyone on the segment. Cable boxes only.",
        ),
        8,
        7,
    )
    lay.add(
        timeseries(
            "MER (DOCSIS 3.1) and MSE (DOCSIS 3.0)",
            targets(
                (q("fritz_docsis_mer_dB"), "MER ch {{channel_id}}"),
                (q("fritz_docsis_mse_dB"), "MSE ch {{channel_id}}"),
            ),
            unit="dB",
        ),
        8,
        7,
    )
    lay.add(
        timeseries(
            "Codeword errors",
            targets(
                (
                    f"increase({q('fritz_docsis_corrected_errors_total')}[$__rate_interval])",
                    "corrected ch {{channel_id}}",
                ),
                (
                    f"increase({q('fritz_docsis_uncorrected_errors_total')}[$__rate_interval])",
                    "uncorrected ch {{channel_id}}",
                ),
            ),
            draw="bars",
            fill=80,
            stack=True,
        ),
        8,
        7,
    )
    lay.add(
        timeseries(
            "Latency", targets((q("fritz_docsis_latency_ms"), "ch {{channel_id}}")), unit="ms"
        ),
        8,
        7,
    )
    lay.add(
        table(
            "Channels",
            [target(q("fritz_docsis_channel_info"), table=True)],
            rename={
                "direction": "Direction",
                "standard": "Standard",
                "channel_id": "Channel",
                "frequency": "Frequency (MHz)",
                "modulation": "Modulation",
            },
            hide=["Value"],
        ),
        16,
        7,
    )
    lay.add(
        stat(
            "Segment sample age",
            [target(f"time() - {q('fritz_cable_segment_sample_timestamp_seconds')}")],
            unit="s",
            steps=thresholds((None, "green"), (180, "orange"), (600, "red")),
        ),
        8,
        7,
    )

    lay.row_start("Fibre", collapsed=True)
    lay.add(
        timeseries(
            "Optical levels",
            targets(
                (q("fritz_fiber_optical_signal_level_dBm"), "receive"),
                (q("fritz_fiber_transmit_optical_level_dBm"), "transmit"),
                (q("fritz_fiber_optical_threshold_dBm"), "receive {{bound}} limit"),
                (q("fritz_fiber_transmit_power_threshold_dBm"), "transmit {{bound}} limit"),
            ),
            unit="suffix: dBm",
            overrides=[dashed(".*limit")],
            legend="table",
        ),
        12,
        8,
    )
    lay.add(
        timeseries(
            "Fibre traffic",
            targets((f"{rate('fritz_fiber_data_bytes_total')} * 8", "{{direction}}")),
            unit="bps",
            overrides=[upload_below()],
        ),
        12,
        8,
    )
    lay.add(
        timeseries(
            "Packets, multicast and errors",
            targets(
                (rate("fritz_fiber_data_packets_total"), "packets {{direction}}"),
                (rate("fritz_fiber_packets_multicast_total"), "multicast"),
                (rate("fritz_fiber_packet_errors_total"), "errors {{direction}}"),
            ),
            unit="pps",
        ),
        12,
        6,
    )
    lay.add(
        stat(
            "Connection rate",
            [target(q("fritz_fiber_connection_rate"), "{{direction}}")],
            text="value_and_name",
            description="As the device reports it; the unit differs between models.",
        ),
        4,
        6,
    )
    lay.add(
        stat("TX wavelength", [target(q("fritz_fiber_tx_wavelength_nm"))], unit="suffix: nm"), 4, 3
    )
    lay.add(stat("GPON ONU ID", [target(q("fritz_fiber_gpon_onu_id"))]), 2, 3)
    lay.add(stat("GEM ports", [target(q("fritz_fiber_gpon_gem_port_count"))]), 2, 3)
    lay.add(
        table(
            "SFP module",
            [target(q("fritz_fiber_info"), table=True)],
            rename={
                "fiber_mode": "Mode",
                "sfp_vendor": "Vendor",
                "sfp_part_number": "Part",
                "sfp_serial_number": "Serial",
                "sfp_type": "Type",
            },
            hide=["Value"],
        ),
        12,
        4,
    )
    lay.add(
        table(
            "GPON",
            [target(q("fritz_fiber_gpon_info"), table=True)],
            rename={"gpon_serial": "GPON serial", "pon_id": "PON ID", "uni_type": "UNI type"},
            hide=["Value"],
        ),
        12,
        4,
    )

    lay.row_start("LAN", collapsed=True)
    lay.add(stat("LAN enabled", [target(q("fritz_lan_status_enabled"))], mappings=ON_OFF), 4, 6)
    lay.add(
        stat("LAN status", [target(q("fritz_lan_status"))], mappings=UP_DOWN, color="background"),
        4,
        6,
    )
    lay.add(
        timeseries(
            "LAN traffic",
            targets((f"{rate('fritz_lan_data_bytes_total')} * 8", "{{direction}}")),
            unit="bps",
            overrides=[upload_below()],
        ),
        8,
        6,
    )
    lay.add(
        timeseries(
            "LAN packets",
            targets((rate("fritz_lan_packet_count_total"), "{{direction}}")),
            unit="pps",
            overrides=[upload_below()],
        ),
        8,
        6,
    )

    lay.row_start("WiFi", collapsed=True)
    lay.add(
        table(
            "Networks",
            targets(
                q("fritz_wifi_status"),
                q("fritz_wifi_channel"),
                q("fritz_wifi_associations_count"),
                q("fritz_wifi_enabled"),
                q("fritz_wifi_info"),
                table=True,
            ),
            rename={
                "wifi_name": "Band",
                "ssid": "SSID",
                "standard": "Standard",
                "Value #B": "Channel",
                "Value #C": "Clients",
                "Value #D": "Enabled",
                "Value #A": "Status",
            },
            hide=["wifi_index", "Value #E"],
            overrides=[
                column(
                    "Status", mappings=UP_DOWN, **{"custom.cellOptions": cell("color-background")}
                ),
                column("Enabled", mappings=ON_OFF),
                column("Clients", **{"custom.cellOptions": cell("gauge", mode="basic"), "max": 30}),
            ],
        ),
        12,
        6,
    )
    lay.add(
        timeseries(
            "Clients per network",
            targets(
                (joined(q("fritz_wifi_associations_count"), *WIFI_SSID), "{{wifi_name}} {{ssid}}")
            ),
            stack=True,
            fill=40,
            decimals=0,
        ),
        12,
        6,
    )
    lay.add(
        timeseries(
            "Packets per network",
            targets((rate("fritz_wifi_packets_count_total"), "{{wifi_name}} {{direction}}")),
            unit="pps",
            overrides=[upload_below()],
        ),
        12,
        7,
    )
    lay.add(
        table(
            "Clients (wifi_client_info)",
            targets(
                q("fritz_wifi_client_signal_strength"),
                q("fritz_wifi_client_speed"),
                q("fritz_wifi_client_info"),
                table=True,
            ),
            rename={
                "wifi_name": "Band",
                "client_mac": "MAC",
                "client_ip": "Address",
                "Value #A": "Signal",
                "Value #B": "Speed",
            },
            hide=["Value #C"],
            sort="Signal",
            overrides=[
                column(
                    "Signal",
                    unit="percent",
                    min=0,
                    max=100,
                    thresholds=thresholds((None, "red"), (40, "orange"), (60, "green")),
                    **{"custom.cellOptions": cell("gauge", mode="basic")},
                ),
                column("Speed", unit="Mbits"),
            ],
            description="Needs wifi_client_info: true.",
        ),
        12,
        7,
    )

    lay.row_start("Mesh", collapsed=True)
    lay.add(
        timeline(
            "Backhaul links",
            targets((q("fritz_mesh_link_available"), "{{node}} ↔ {{peer}} ({{interface}})")),
            mappings=LINK_STATE,
            description="Read from the mesh master.",
        ),
        24,
        5,
    )
    lay.add(
        timeseries(
            "Link data rate (dashed: maximum)",
            targets(
                (q("fritz_mesh_link_current_data_rate_kbps"), "{{node}} → {{peer}} {{direction}}"),
                (q("fritz_mesh_link_max_data_rate_kbps"), "max {{node}} → {{peer}} {{direction}}"),
            ),
            unit="Kbits",
            overrides=[dashed("max .*")],
            legend="table",
        ),
        24,
        8,
    )

    lay.row_start("Hosts (host_info)", collapsed=True)
    lay.add(
        stat(
            "Active hosts",
            [target(f"sum({q('fritz_host_active')})")],
            graph=True,
            fixed="blue",
            color="background",
        ),
        4,
        8,
    )
    lay.add(
        timeseries(
            "Active hosts by interface",
            targets(
                (
                    (
                        "sum by (interface) (label_replace("
                        f"{q('fritz_host_active')} * on (serial, mac_address) "
                        f"group_left (interface) {q('fritz_host_info')}, "
                        '"interface", "unknown", "interface", ""))'
                    ),
                    "{{interface}}",
                )
            ),
            stack=True,
            fill=40,
            decimals=0,
        ),
        20,
        8,
    )
    lay.add(
        table(
            "Hosts",
            targets(
                q("fritz_host_info"), q("fritz_host_active"), q("fritz_host_speed"), table=True
            ),
            rename={
                "hostname": "Name",
                "ip_address": "Address",
                "mac_address": "MAC",
                "interface": "Interface",
                "port": "Port",
                "model": "Model",
                "Value #B": "Active",
                "Value #C": "Speed",
            },
            hide=["Value #A"],
            sort="Active",
            overrides=[
                column(
                    "Active",
                    mappings=mapping({"1": ("active", "green"), "0": ("inactive", "text")}),
                    **{"custom.cellOptions": cell("color-text")},
                ),
                column("Speed", unit="Mbits"),
            ],
            description="Needs host_info: true.",
        ),
        24,
        10,
    )

    lay.row_start("Smart home", collapsed=True)
    ha_name = "{{device_name}}"
    lay.add(
        timeseries(
            "Temperature",
            targets(
                (q("fritz_ha_temperature_C"), ha_name),
                (q("fritz_ha_temperature_offset_C"), f"{ha_name} offset"),
            ),
            unit="celsius",
            decimals=1,
        ),
        12,
        7,
    )
    lay.add(
        timeseries(
            "Heating: measured and set",
            targets(
                (q("fritz_ha_heater_temperature_C"), f"{ha_name} measured"),
                (q("fritz_ha_heater_set_temperature_C"), f"{ha_name} set"),
                (q("fritz_ha_heater_comfort_temperature_C"), f"{ha_name} comfort"),
                (q("fritz_ha_heater_reduced_temperature_C"), f"{ha_name} reduced"),
            ),
            unit="celsius",
            overrides=[dashed(".*(comfort|reduced)", dash=(4, 6))],
        ),
        12,
        7,
    )
    lay.add(
        timeseries(
            "Power", targets((q("fritz_ha_multimeter_power_W"), ha_name)), unit="watt", fill=30
        ),
        12,
        7,
    )
    lay.add(
        bars(
            "Energy in the selected time range",
            [
                target(
                    f"increase({q('fritz_ha_multimeter_energy_Wh')}[$__range])",
                    ha_name,
                    now=True,
                )
            ],
            unit="watth",
            scheme="continuous-GrYlRd",
        ),
        6,
        7,
    )
    lay.add(
        bars(
            "Battery",
            targets((q("fritz_ha_battery_level_percent"), ha_name)),
            unit="percent",
            maximum=100,
            steps=thresholds((None, "red"), (20, "orange"), (40, "green")),
            mode="lcd",
        ),
        6,
        7,
    )
    valve = mapping({"0": ("closed", "blue"), "1": ("open", "red"), "2": ("temperature", "green")})
    lay.add(
        timeline(
            "Valves",
            targets(
                (q("fritz_ha_heater_valve_set_state"), f"{ha_name} set"),
                (q("fritz_ha_heater_comfort_valve_state"), f"{ha_name} comfort"),
                (q("fritz_ha_heater_reduced_valve_state"), f"{ha_name} reduced"),
            ),
            mappings=valve,
        ),
        12,
        6,
    )
    lay.add(
        table(
            "Devices",
            targets(
                q("fritz_ha_device_present"),
                q("fritz_ha_switch_state"),
                q("fritz_ha_switch_mode"),
                q("fritz_ha_switch_lock"),
                q("fritz_ha_battery_low"),
                table=True,
            ),
            rename={
                "device_name": "Name",
                "productname": "Product",
                "manufacturer": "Maker",
                "Value #A": "Present",
                "Value #B": "Switch",
                "Value #C": "Mode",
                "Value #D": "Locked",
                "Value #E": "Battery low",
            },
            hide=["ain", "device_id"],
            overrides=[
                column(
                    "Present",
                    mappings=mapping({"2": ("yes", "green"), "0": ("no", "red")}),
                    **{"custom.cellOptions": cell("color-text")},
                ),
                column("Switch", mappings=ON_OFF, **{"custom.cellOptions": cell("color-text")}),
                column("Mode", mappings=mapping({"0": ("manual", "text"), "1": ("auto", "text")})),
                column("Locked", mappings=mapping({"1": ("yes", "orange"), "0": ("no", "text")})),
                column(
                    "Battery low", mappings=YES_NO, **{"custom.cellOptions": cell("color-text")}
                ),
            ],
            description="fritz_ha_device_present is 2 for present, kept for compatibility.",
        ),
        12,
        6,
    )

    lay.row_start("Provider management (TR-069, USP)", collapsed=True)
    lay.add(
        table(
            "TR-069 management server",
            [target(q("fritz_tr069_acs_info"), table=True)],
            rename={"acs_host": "ACS host"},
            hide=["Value"],
        ),
        8,
        4,
    )
    lay.add(
        stat(
            "Periodic inform", [target(q("fritz_tr069_periodic_inform_enabled"))], mappings=ON_OFF
        ),
        4,
        4,
    )
    lay.add(
        stat(
            "Inform interval", [target(q("fritz_tr069_periodic_inform_interval_seconds"))], unit="s"
        ),
        4,
        4,
    )
    lay.add(
        stat(
            "Upgrades managed by provider",
            [target(q("fritz_tr069_upgrades_managed"))],
            mappings=YES_NO,
        ),
        4,
        4,
    )
    lay.add(
        stat(
            "USP controllers",
            targets(
                (q("fritz_usp_controller_enabled"), "controller {{index}}"),
                (q("fritz_usp_myfritz_enabled"), "MyFRITZ"),
            ),
            mappings=ON_OFF,
            text="value_and_name",
        ),
        4,
        4,
    )

    return dashboard(
        "fritz-exporter-overview",
        "Fritz!Exporter Overview",
        "Every metric of the Fritz! exporter, grouped by topic. Rows that do not apply "
        "to a device (cable on a DSL box, ...) stay empty.",
        lay,
        refresh="1m",
    )


# ---------------------------------------------------------------------------
# Home: what is useful every day
# ---------------------------------------------------------------------------


def home() -> dict:
    lay = Layout()
    download = f"{q('fritz_wan_datarate_bytes', direction='rx')} * 8"
    upload = f"{q('fritz_wan_datarate_bytes', direction='tx')} * 8"

    lay.add(
        stat(
            "Internet",
            [target(q("fritz_connection_mode"), now=True)],
            mappings=CONNECTION_MODES,
            color="background",
        ),
        4,
        5,
    )
    lay.add(
        stat(
            "Online for",
            [
                target(
                    f"max(({q('fritz_wan_connection_uptime_seconds_total')} "
                    "and on (serial, connection, stack) "
                    f"({q('fritz_wan_connection_status')} == 1)) "
                    f"or {q('fritz_ppp_connection_uptime_seconds_total')})"
                )
            ],
            unit="s",
            decimals=1,
            color="background",
            fixed="purple",
        ),
        4,
        5,
    )
    lay.add(
        stat(
            "Download now",
            [target(download)],
            unit="bps",
            graph=True,
            color="background",
            fixed="green",
        ),
        4,
        5,
    )
    lay.add(
        stat(
            "Upload now", [target(upload)], unit="bps", graph=True, color="background", fixed="blue"
        ),
        4,
        5,
    )
    lay.add(
        stat(
            "Devices online",
            targets(
                (f"sum({q('fritz_host_active')})", "hosts"),
                (f"sum({q('fritz_wifi_associations_count')})", "on WiFi"),
            ),
            text="value_and_name",
            color="background",
            fixed="orange",
            description="Hosts need host_info: true.",
        ),
        4,
        5,
    )
    lay.add(
        stat(
            "Fritz!OS",
            [target(q("fritz_update_available"), now=True)],
            mappings=mapping({"1": ("Update available", "orange"), "0": ("Up to date", "green")}),
            color="background",
            # The version would shrink the text; it is on the overview.
            description="The new version is on the Fritz!Exporter Overview dashboard.",
        ),
        4,
        5,
    )

    lay.add(
        timeseries(
            "Internet traffic",
            targets((download, "download"), (upload, "upload")),
            unit="bps",
            overrides=[
                upload_below(),
                column("download", color={"mode": "fixed", "fixedColor": "green"}),
                column("upload", color={"mode": "fixed", "fixedColor": "blue"}),
            ],
            fill=35,
            legend="table",
            calcs=["mean", "max", "lastNotNull"],
        ),
        16,
        9,
    )
    for direction, name in (("rx", "Download"), ("tx", "Upload")):
        lay.add(
            gauge(
                f"{name} line usage",
                [
                    target(
                        f"{q('fritz_wan_datarate_bytes', direction=direction)} * 8 / "
                        f"{q('fritz_wan_layer1_max_bitrate_bps', direction=direction)} * 100"
                    )
                ],
                unit="percent",
                decimals=0,
                description="Current rate against the line's maximum.",
            ),
            4,
            9,
        )

    lay.add(
        stat(
            "Line quality",
            targets(
                (q("fritz_dsl_noise_margin_dB", direction="rx"), "DSL noise margin"),
                (f"min({q('fritz_docsis_mer_dB')})", "DOCSIS MER (worst)"),
                (q("fritz_fiber_optical_signal_level_dBm"), "Fibre receive level"),
            ),
            unit="dB",
            overrides=[column("Fibre receive level", unit="suffix: dBm")],
            text="value_and_name",
            fixed="teal",
            color="value",
            description="Whichever applies to the line: DSL, cable or fibre.",
        ),
        6,
        6,
    )
    lay.add(
        bars(
            "Sync rate",
            targets(
                (q("fritz_wan_layer1_max_bitrate_bps", direction="rx"), "down"),
                (q("fritz_wan_layer1_max_bitrate_bps", direction="tx"), "up"),
            ),
            unit="bps",
            scheme="continuous-BlPu",
            mode="lcd",
        ),
        6,
        6,
    )
    lay.add(
        bars(
            "WiFi networks",
            targets(
                (joined(q("fritz_wifi_associations_count"), *WIFI_SSID), "{{ssid}} ({{wifi_name}})")
            ),
            unit="short",
            scheme="continuous-YlBl",
        ),
        6,
        6,
    )
    lay.add(
        timeline(
            "Mesh",
            targets((q("fritz_mesh_link_available"), "{{peer}} {{interface}}")),
            mappings=LINK_STATE,
        ),
        6,
        6,
    )

    lay.add(
        bars(
            "Room temperatures",
            targets((q("fritz_ha_temperature_C"), "{{device_name}}")),
            unit="celsius",
            minimum=10,
            maximum=30,
            steps=thresholds((None, "blue"), (18, "green"), (24, "orange"), (27, "red")),
            mode="gradient",
        ),
        8,
        7,
    )
    lay.add(
        timeseries(
            "Power",
            targets((q("fritz_ha_multimeter_power_W"), "{{device_name}}")),
            unit="watt",
            fill=40,
            stack=True,
        ),
        10,
        7,
    )
    lay.add(
        stat(
            "Energy, last 24 hours",
            [target(f"sum(increase({q('fritz_ha_multimeter_energy_Wh')}[24h]))", now=True)],
            unit="watth",
            decimals=0,
            color="background",
            fixed="yellow",
            description="All smart plugs together.",
        ),
        6,
        7,
    )

    return dashboard(
        "fritz-exporter-home",
        "Fritz!Exporter Home",
        "The everyday view of a Fritz!Box: internet, devices, WiFi, mesh and smart home.",
        lay,
        refresh="30s",
    )


DASHBOARDS = {
    "fritz-exporter-overview.json": overview,
    "fritz-exporter-home.json": home,
}


def render(build: Any) -> str:  # noqa: ANN401
    return json.dumps(build(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    for name, build in DASHBOARDS.items():
        (out / name).write_text(render(build))


if __name__ == "__main__":
    main()
