"""
Tests for look-across — extent of condition.

A finding at one station is half an answer. Three things are proved here:

  1. The fault travels with the SHARED ASSET, not the station. S8's worn gripper
     is a property of robot ROB-C1, so S7 is exposed even though S7 has no
     finding of its own. That station is invisible on any per-station view.
  2. A gap present at EVERY station is one systemic problem, not nine local ones.
  3. Every condition finding carries a containment scope — roughly how many units
     went through while the condition existed. Without it somebody still has to
     work out which units are suspect, and that is the part that decides whether
     anything gets held.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src import coverage, look_across


@pytest.fixture(scope="module")
def rows():
    coverage.run()
    return look_across.run()


@pytest.fixture(scope="module")
def by_key(rows):
    return {(r["finding_id"], r["station"]): r for r in rows}


@pytest.fixture(scope="module")
def cfg():
    return coverage.load_yaml(coverage.ENV_MODEL)


def test_a_worn_gripper_exposes_every_station_on_the_same_robot(by_key):
    """The one that matters. S7 has no finding of its own and is still exposed,
    because the tool-centre-point error belongs to the arm."""
    r = by_key[("CONDITION_COVERAGE_LOST", "S8")]
    assert r["verdict"] == "SHARED_ASSET"
    assert r["propagates_with"] == "robot" and r["asset_id"] == "ROB-C1"
    assert r["peer_stations"] == "S7"
    assert "travels with the robot" in r["summary"]


def test_a_dim_illuminator_exposes_every_station_with_the_same_model(by_key):
    r = by_key[("CONDITION_COVERAGE_LOST", "S9")]
    assert r["propagates_with"] == "illuminator_model" and r["asset_id"] == "ILL-220"
    assert set(r["peer_stations"].split(";")) == {"S3", "S6"}


def test_a_gap_at_every_station_is_one_problem_not_nine(by_key):
    """LBL-ERR is unassigned everywhere. Fixing that station by station is the
    expensive way to never finish."""
    r = by_key[("UNASSIGNED_MODE", "-")]
    assert r["verdict"] == "SYSTEMIC"
    assert r["n_affected"] == r["n_stations"] == 9
    assert "not 9 local ones" in r["summary"]


def test_a_partial_gap_is_not_called_systemic(by_key):
    """SEAL-GAP is declared at 3 of 9. That is an inconsistent plan, which is a
    different problem from a uniformly wrong one."""
    r = by_key[("DECLARED_SILENT", "-")]
    assert r["verdict"] != "SYSTEMIC"
    assert r["n_affected"] < r["n_stations"]


def test_every_condition_finding_carries_a_containment_scope(rows):
    cond = [r for r in rows if r["finding_id"] == "CONDITION_COVERAGE_LOST"]
    assert cond
    for r in cond:
        assert r["containment_from"] and r["containment_to"]
        assert r["units_in_window"] and r["units_in_window"] > 0
        assert "Containment scope" in r["summary"]


def test_propagation_uses_the_right_asset_for_the_right_failure(cfg):
    """A worn gripper travels with the robot; a worn fixture travels with the
    fixture design; dim imaging travels with the illuminator model. Getting this
    mapping wrong sends people to inspect the wrong machines."""
    m = cfg["asset_topology"]["propagates_with"]
    assert m["tooling_check__eoat"] == "robot"
    assert m["tooling_check__fixture"] == "fixture_design"
    assert m["imaging_check"] == "illuminator_model"
    assert look_across.check_for("measurement + tooling/eoat") == "tooling_check__eoat"
    assert look_across.check_for("imaging") == "imaging_check"
    assert look_across.check_for("measurement") == "measurement_check"


def test_peers_are_ranked_even_when_none_have_tripped(cfg):
    """"Who is next" is the question a per-station view cannot answer."""
    telem = pd.read_csv(coverage.ENV_TELEMETRY)
    latest = telem.sort_values("production_day").groupby("station_id").tail(1)
    ranked = look_across.rank_peers(latest, "S8", "gripper_pad_wear_mm", 0.25, True, 0.6)
    assert len(ranked) == 8
    assert {r["state"] for r in ranked} <= {"BREACHED", "APPROACHING", "OK"}
    fr = [r["fraction_of_limit"] for r in ranked]
    assert fr == sorted(fr, reverse=True) or len(set(fr)) == 1


def test_look_across_is_folded_back_into_the_report(rows):
    rep = json.loads(coverage.REPORT.read_text(encoding="utf-8"))
    assert "look_across" in rep
    tagged = [f for f in rep["findings"] if f.get("look_across")]
    assert len(tagged) == len(rows)
    for f in tagged:
        assert f["look_across"]["verdict"] in ("LOCAL", "SHARED_ASSET", "SYSTEMIC")


def test_the_topology_covers_every_station(cfg):
    """A station missing from the map would silently look-across to nothing."""
    stations = cfg["asset_topology"]["stations"]
    assert len(stations) == 9
    for st, a in stations.items():
        assert {"robot", "illuminator_model", "fixture_design"} <= set(a), st
