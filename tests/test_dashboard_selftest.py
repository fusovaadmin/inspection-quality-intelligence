"""
Tests for the cell self-test as it appears in the interactive dashboard.

The dashboard is where a quality engineer actually lives, so "can this station
still do its job?" has to sit next to the control chart rather than on a separate
page. These tests protect the payload the dashboard renders from:

  * every station carries its self-test, including the healthy ones — a panel that
    only appears when something is wrong teaches people it is an error message
  * the two failing stations fail in OPPOSITE directions, and the payload says so
  * the fleet tile counts ALERT by severity, not by alphabet
  * the wear projection travels with its disclaimer into the browser
  * the dashboard still builds when the self-test artefacts are absent
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src import dashboard

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "dashboard.html"


@pytest.fixture(scope="module")
def data():
    return dashboard._build_data()


@pytest.fixture(scope="module")
def by_id(data):
    return {s["id"]: s for s in data["stations"]}


def test_every_station_carries_a_selftest_including_the_healthy_ones(by_id):
    """A panel that only shows up when something is broken reads as an error
    message. It should be there on a good station too, saying so."""
    assert len(by_id) == 9
    for sid, s in by_id.items():
        assert s["selftest"] is not None, sid
        # four imaging/measurement traces plus every measure of both tooling sets
        assert len(s["selftest"]["series"]) == 9
        assert all(len(v) == 60 for v in s["selftest"]["series"].values())


def test_the_two_failures_point_in_opposite_directions(by_id):
    """S9 lost its visual checks and kept the measurement. S2 is the inverse.

    If a cell fault took everything down at once, yield would catch it in a day.
    Neither of these does, which is the whole reason the panel exists.
    """
    s9, s2 = by_id["S9"]["selftest"], by_id["S2"]["selftest"]
    assert s9["condition"] == "ALERT" and s9["cause"] == "imaging"
    assert s9["steps"] == {"imaging": False, "measurement": True, "tooling": True}
    assert s2["condition"] == "ALERT" and s2["cause"] == "measurement"
    assert s2["steps"] == {"imaging": True, "measurement": False, "tooling": True}
    assert len(s9["lost"]) == 4 and len(s2["lost"]) == 1


def test_condition_is_ranked_by_severity_not_alphabetically(by_id):
    """String max() ranks WATCH above OK above ALERT, which is exactly backwards
    and silently turns a red station green."""
    assert by_id["S2"]["selftest"]["condition"] == "ALERT"
    assert by_id["S6"]["selftest"]["condition"] == "WATCH"
    assert by_id["S1"]["selftest"]["condition"] == "OK"


def test_the_fleet_tile_counts_alerting_stations(data, by_id):
    expected = sum(1 for s in by_id.values() if s["selftest"]["condition"] == "ALERT")
    assert data["meta"]["nSelftest"] == expected == 3
    assert data["meta"]["hasSelftest"] is True


def test_lost_inspections_are_named_not_coded(by_id):
    """Nobody reading this for the first time knows what SRF-SCR means."""
    for m in by_id["S9"]["selftest"]["lost"]:
        assert m["label"] != m["code"]
        assert m["code"] in m["label"]


def test_only_the_station_with_a_real_trend_gets_a_wear_projection(by_id):
    """Wear is a list now — one entry per tooling set that actually moved."""
    s2 = by_id["S2"]["selftest"]["wear"]
    assert s2 and len(s2) == 1
    assert s2[0]["set"] == "Part-holding fixture" and s2[0]["accel"] is True
    s5 = by_id["S5"]["selftest"]["wear"]
    assert s5 and s5[0]["set"].startswith("End-of-arm") and s5[0]["accel"] is False
    for sid in ("S1", "S3", "S9"):
        assert by_id[sid]["selftest"]["wear"] is None, sid


def test_the_projection_disclaimer_reaches_the_browser(by_id):
    """The label travels with the number all the way to the rendered page."""
    w = by_id["S2"]["selftest"]["wear"][0]
    assert "not a failure prediction" in w["label"].lower()
    if not OUT.exists():
        return
    # Compare against the DECODED payload: the label contains an em-dash, which
    # is escaped to \\u2014 in the embedded JSON, so a raw substring match on the
    # HTML would fail even though the label is present.
    h = OUT.read_text(encoding="utf-8")
    payload = json.loads(re.search(r'id="data"[^>]*>(.*?)</script>', h, re.S).group(1))
    rendered = {s["id"]: s for s in payload["stations"]}["S2"]["selftest"]["wear"][0]
    assert rendered["label"] == w["label"]


def test_loop_closure_is_attached_to_the_failing_stations(by_id):
    """Detection without loop closure is a gate, not a system."""
    for sid in ("S2", "S9"):
        plan = by_id[sid]["selftest"]["plan"]
        assert plan is not None, sid
        assert plan["probable_causes"] and plan["corrective_actions"]
        assert plan["containment"] and plan["owner"]
    assert by_id["S1"]["selftest"]["plan"] is None


def test_the_rendered_page_embeds_the_selftest_renderers():
    if not OUT.exists():
        pytest.skip("dashboard not built")
    h = OUT.read_text(encoding="utf-8")
    for fn in ("stSpark", "stBadge", "stepStrip", "stCharts", "selftestCard"):
        assert f"function {fn}" in h, fn
    payload = json.loads(re.search(r'id="data"[^>]*>(.*?)</script>', h, re.S).group(1))
    assert payload["meta"]["hasSelftest"] is True


def test_the_dashboard_still_builds_without_the_selftest_artefacts(monkeypatch):
    """Only the SPC half of the pipeline may have run. The dashboard must not
    require the coverage stage in order to exist."""
    monkeypatch.setattr(dashboard, "_selftest_by_station", lambda: {})
    d = dashboard._build_data()
    assert d["meta"]["hasSelftest"] is False
    assert d["meta"]["nSelftest"] == 0
    assert all(s["selftest"] is None for s in d["stations"])


def test_the_dashboard_names_the_tooling_set_that_moved(by_id):
    """S8's cause has to say eoat, not just "tooling" — otherwise the drill-down
    tells an engineer something is wrong and not what to go and touch."""
    s8 = by_id["S8"]["selftest"]
    assert s8["condition"] == "ALERT"
    assert "tooling/eoat" in s8["cause"]
    assert s8["plan"]["owner"] == "Controls / Robotics"


def test_both_tooling_sets_get_their_own_traces(by_id):
    names = [v["name"] for v in by_id["S8"]["selftest"]["limits"].values()]
    assert any(n.startswith("EOAT:") for n in names)
    assert any(n.startswith("Fixture:") for n in names)
