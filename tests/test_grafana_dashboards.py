"""The Grafana dashboards in misc/grafana are generated, current and complete."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from fritzexporter.fritzdevice import FritzCollector

GRAFANA = Path(__file__).parent.parent / "misc" / "grafana"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, GRAFANA / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load("build_dashboards")


def _panels(items: list[dict]):
    for panel in items:
        yield panel
        yield from _panels(panel.get("panels", []))


def _queries(board: dict) -> list[str]:
    return [t["expr"] for p in _panels(board["panels"]) for t in p.get("targets", [])]


def _demo_labels() -> dict[str, dict[str, set[str]]]:
    """Label names and the values seen, per sample name, from the demo exporter's boxes."""
    labels: dict[str, dict[str, set[str]]] = {}
    for family in _load("demo_exporter").demo_collector().collect():
        if not family.samples:
            labels[family.name] = {name: set() for name in family._labelnames}
        for sample in family.samples:
            sent = labels.setdefault(sample.name, {})
            for name, value in sample.labels.items():
                sent.setdefault(name, set()).add(value)
    return labels


DEMO_LABELS = _demo_labels()
TARGET_LABELS = {"job", "instance"}


@pytest.mark.parametrize("name", list(BUILDER.DASHBOARDS))
def test_committed_json_is_current(name):
    # Edit build_dashboards.py and run it; do not edit the JSON.
    assert (GRAFANA / name).read_text() == BUILDER.render(BUILDER.DASHBOARDS[name])


def test_overview_shows_every_metric():
    # A collector without devices still yields every metric family it has.
    families = {family.name for family in FritzCollector().collect()}
    queries = "\n".join(_queries(BUILDER.overview()))

    missing = [
        name
        for name in sorted(families)
        if not re.search(rf"\b{re.escape(name)}(_total)?(?![A-Za-z0-9_])", queries)
    ]

    assert missing == [], "add these metrics to the overview in misc/grafana/build_dashboards.py"


@pytest.mark.parametrize("name", list(BUILDER.DASHBOARDS))
def test_queries_select_the_device(name):
    board = BUILDER.DASHBOARDS[name]()
    exporter_wide = ("fritz_scrapes_timeouts_total", "fritz_scrapes_failed_total")
    for expr in _queries(board):
        if any(metric in expr for metric in exporter_wide):
            continue
        for selector in re.findall(r"fritz_[A-Za-z0-9_]+(\{[^}]*\})?", expr):
            assert 'friendly_name="$device"' in selector, expr


@pytest.mark.parametrize("name", list(BUILDER.DASHBOARDS))
def test_queries_use_labels_and_values_the_exporter_sends(name):
    # A label moved to an _info metric, or a value spelt differently, leaves a
    # panel empty without any error from Prometheus or Grafana.
    board = BUILDER.DASHBOARDS[name]()
    for panel in _panels(board["panels"]):
        for target in panel.get("targets", []):
            expr = target["expr"]
            known = set(TARGET_LABELS)
            for metric, matchers in re.findall(r"(fritz_[A-Za-z0-9_]+)(?:\{([^}]*)\})?", expr):
                assert metric in DEMO_LABELS, f"{panel['title']}: no metric {metric}"
                sent = DEMO_LABELS[metric]
                known |= set(sent)
                for label, op, value in re.findall(r'(\w+)\s*(=~|!=|!~|=)\s*"([^"]*)"', matchers):
                    if label == "friendly_name":
                        continue
                    assert label in sent, f"{panel['title']}: {metric} has no label {label}"
                    if op == "=":
                        assert value in sent[label], f"{panel['title']}: {metric} {label}={value!r}"
            known |= set(re.findall(r'"(\w+)",\s*"[^"]*",\s*"\w+",\s*"[^"]*"\)', expr))
            used = [
                label.strip()
                for group in re.findall(r"\b(?:on|by|ignoring|group_left|without)\s*\(([^)]*)\)", expr)
                for label in group.split(",")
            ]
            used += re.findall(r"\{\{\s*(\w+)\s*\}\}", target.get("legendFormat", ""))
            unknown = sorted(set(used) - known)
            assert unknown == [], f"{panel['title']}: labels not on its metrics: {unknown}"


@pytest.mark.parametrize("name", list(BUILDER.DASHBOARDS))
def test_rate_panels_work_with_a_60s_scrape_interval(name):
    # Without a min interval, $__rate_interval follows the data source's scrape
    # interval (15 s by default): a 1m window, one sample of a 60 s scrape.
    board = BUILDER.DASHBOARDS[name]()
    for panel in _panels(board["panels"]):
        if any("$__rate_interval" in t["expr"] for t in panel.get("targets", [])):
            assert panel.get("interval") == "1m", panel["title"]


@pytest.mark.parametrize("name", list(BUILDER.DASHBOARDS))
def test_panels_do_not_overlap(name):
    board = BUILDER.DASHBOARDS[name]()

    def cells(panels):
        taken = {}
        for panel in panels:
            pos = panel["gridPos"]
            for x in range(pos["x"], pos["x"] + pos["w"]):
                for y in range(pos["y"], pos["y"] + pos["h"]):
                    assert (x, y) not in taken, f"{panel['title']} overlaps {taken[(x, y)]}"
                    taken[(x, y)] = panel["title"]
            assert pos["x"] + pos["w"] <= 24, panel["title"]

    top = board["panels"]
    cells(top)
    for row in (p for p in top if p["type"] == "row"):
        cells(row["panels"])


def test_dashboards_are_plain_json():
    for name in BUILDER.DASHBOARDS:
        board = json.loads((GRAFANA / name).read_text())
        assert board["uid"].startswith("fritz-exporter-")
        assert {v["name"] for v in board["templating"]["list"]} == {"datasource", "device"}
