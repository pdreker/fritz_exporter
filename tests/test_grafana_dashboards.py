"""The Grafana dashboards in misc/grafana are generated, current and complete."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from fritzexporter.fritzdevice import FritzCollector

GRAFANA = Path(__file__).parent.parent / "misc" / "grafana"


def _builder():
    spec = importlib.util.spec_from_file_location("build_dashboards", GRAFANA / "build_dashboards.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _builder()


def _panels(items: list[dict]):
    for panel in items:
        yield panel
        yield from _panels(panel.get("panels", []))


def _queries(board: dict) -> list[str]:
    return [t["expr"] for p in _panels(board["panels"]) for t in p.get("targets", [])]


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
