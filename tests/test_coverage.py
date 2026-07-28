"""
Tests for the inspection coverage audit and the positional cost model.

Three separate things are proved here:

  1. The COVERAGE AUDIT finds what is actually in the data — a failure mode the
     floor reports that the control plan assigns to nobody, and a declared mode
     that never fires anywhere. Both are read out of the events, not asserted.
  2. The COST MODEL walks the line correctly — an escape is costed at the next
     point that can actually catch the mode, which may be a downstream station,
     final test, or the customer. Getting that wrong is the whole ballgame.
  3. The SENSITIVITY result is locked down, INCLUDING the inconvenient half. The
     claim that false-reject cost rises with position survives every assumption
     combination; the claim that the exchange rate falls with position does not.
     A test asserts the second one FAILS, so that if someone later tunes the cost
     assumptions until the tidy story holds, the suite goes red instead of quiet.

The cost figures are assumptions from config/cost_model.yaml, so the numeric
assertions below are assertions about the MODEL, not about any real plant.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src import coverage
from src.validation import load_control_plan

ROOT = Path(__file__).resolve().parents[1]
MARTS = ROOT / "data" / "marts"

# Marts produced by the Databricks job and parity-verified against the tested
# pandas reference. The coverage module must never write to them.
PROTECTED_MARTS = ["daily_fpy.csv", "station_scorecard.csv"]

# Expected findings — every one is read out of the data, not injected here.
EXPECTED_UNASSIGNED_MODE = "LBL-ERR"      # reported by all 9 stations, assigned to none
EXPECTED_SILENT_MODE = "SEAL-GAP"         # declared at 3 stations, detected nowhere
EXPECTED_STATIONS = 9
EXPECTED_LINES = 3
SENSITIVITY_COMBOS = 81                   # 3^4 assumption combinations


@pytest.fixture(scope="module")
def cfg():
    plan = load_control_plan()
    cost = coverage.load_yaml(coverage.COST_MODEL)
    return plan, cost


@pytest.fixture(scope="module")
def report():
    return coverage.run()


# --------------------------------------------------------------------------- #
# 1. line topology
# --------------------------------------------------------------------------- #
def test_station_positions_cover_every_station_exactly_once(cfg):
    _, cost = cfg
    pos = coverage.station_positions(cost)
    assert len(pos) == EXPECTED_STATIONS
    assert len({line for line, _ in pos.values()}) == EXPECTED_LINES
    for line, stations in cost["line_sequence"].items():
        assert [pos[s][1] for s in stations] == list(range(1, len(stations) + 1))


def test_position_is_not_duplicated_in_the_control_plan(cfg):
    """Sequence position has exactly one source of truth: the cost model.

    If it were also declared in the control plan the two could disagree, and a
    coverage audit that disagrees with itself is worse than none.
    """
    plan, _ = cfg
    for spec in plan["inspection_coverage"]["stations"].values():
        assert "position" not in spec


# --------------------------------------------------------------------------- #
# 2. cost model walks the line correctly
# --------------------------------------------------------------------------- #
def test_false_reject_cost_rises_with_position(cfg):
    _, cost = cfg
    costs = [coverage.false_reject_cost(p, cost) for p in (1, 2, 3)]
    assert costs == sorted(costs) and len(set(costs)) == 3


def test_escape_from_upstream_is_caught_at_the_next_station_that_sees_it(cfg):
    plan, cost = cfg
    kind, where = coverage.discovery_point("DIM-OOS", "LINE-A", 1, plan, cost)
    assert (kind, where) == ("station", "S2")


def test_escape_from_the_last_station_falls_through_to_final_test(cfg):
    plan, cost = cfg
    kind, where = coverage.discovery_point("DIM-OOS", "LINE-A", 3, plan, cost)
    assert (kind, where) == ("final_test", "FINAL-TEST")


def test_mode_final_test_cannot_see_reaches_the_customer(cfg):
    """SRF-SCR is not in final_test_detects, so position 3 is the last chance."""
    plan, cost = cfg
    kind, where = coverage.discovery_point("SRF-SCR", "LINE-A", 3, plan, cost)
    assert (kind, where) == ("customer", "CUSTOMER")
    c_esc, kind2, _ = coverage.escape_cost("SRF-SCR", "LINE-A", 3, plan, cost)
    expected = (cost["value_at_final_test"] * cost["escape"]["teardown_multiplier"]
                + cost["escape"]["customer_penalty_vu"])
    assert kind2 == "customer"
    assert c_esc == pytest.approx(expected)


def test_a_mode_with_no_backstop_costs_more_to_miss_than_one_with_a_backstop(cfg):
    """Same station, same position — only the downstream safety net differs.

    This is why one operating point per station is not enough: the correct
    threshold depends on the mode as well as the position.
    """
    plan, cost = cfg
    backstopped, _, _ = coverage.escape_cost("DIM-OOS", "LINE-A", 3, plan, cost)
    exposed, _, _ = coverage.escape_cost("SRF-SCR", "LINE-A", 3, plan, cost)
    assert exposed > backstopped


# --------------------------------------------------------------------------- #
# 3. the coverage audit finds what is in the data
# --------------------------------------------------------------------------- #
def test_a_mode_the_floor_reports_is_assigned_to_nobody(report):
    """The headline finding, and it is computed rather than declared."""
    assert report["modes_observed_not_declared"] == [EXPECTED_UNASSIGNED_MODE]
    f = [x for x in report["findings"] if x["id"] == "UNASSIGNED_MODE"]
    assert len(f) == 1 and f[0]["mode"] == EXPECTED_UNASSIGNED_MODE


def test_unassigned_mode_is_reported_by_every_station(cfg):
    plan, cost = cfg
    covdf = coverage.build_coverage(
        plan, cost,
        coverage.observed_detections(pd.read_csv(coverage.RAW, parse_dates=["ts"])))
    rows = covdf[covdf["mode"] == EXPECTED_UNASSIGNED_MODE]
    assert (rows["state"] == "UNDECLARED_DETECTED").sum() == EXPECTED_STATIONS


def test_declared_mode_that_never_fires_is_an_open_question_not_a_pass(report):
    assert report["modes_declared_not_observed"] == [EXPECTED_SILENT_MODE]
    f = [x for x in report["findings"] if x["id"] == "DECLARED_SILENT"]
    assert len(f) == 1
    assert f[0]["mode"] == EXPECTED_SILENT_MODE
    assert f[0]["severity"] == "open_question"


def test_a_quiet_station_on_a_capable_process_is_not_flagged(report):
    """DIM-OOS is declared at all nine stations and only S3 drifts.

    The eight quiet stations are quiet because the process is capable. Flagging
    them would be an alarm with no defect behind it — which is precisely how a
    tool trains people to stop opening it.
    """
    silent = [f["mode"] for f in report["findings"] if f["id"] == "DECLARED_SILENT"]
    assert "DIM-OOS" not in silent


def test_only_one_mode_has_a_spec_and_the_rest_are_detection_without_teeth(report):
    """A declared mode with no feature_ref has no spec window and no reaction plan.

    Detecting it produces an event and nothing else — a quality gate, not a
    quality system. The audit names them rather than counting them as covered.
    """
    assert report["modes_specified"] == ["DIM-OOS"]
    assert EXPECTED_UNASSIGNED_MODE not in report["declared_without_spec"]
    assert len(report["declared_without_spec"]) == len(report["modes_declared"]) - 1


def test_coverage_matrix_is_the_full_cross_product(cfg):
    plan, cost = cfg
    covdf = coverage.build_coverage(
        plan, cost,
        coverage.observed_detections(pd.read_csv(coverage.RAW, parse_dates=["ts"])))
    n_modes = covdf["mode"].nunique()
    assert len(covdf) == EXPECTED_STATIONS * n_modes
    assert not covdf.duplicated(subset=["station_id", "mode"]).any()
    assert set(covdf["state"]) <= {"COVERED", "UNDECLARED_DETECTED",
                                   "DECLARED_SILENT", "NOT_APPLICABLE"}


# --------------------------------------------------------------------------- #
# 4. sensitivity — including the half that does not go our way
# --------------------------------------------------------------------------- #
def test_false_reject_claim_survives_every_assumption_combination(report):
    s = report["sensitivity"]["C1_false_reject_cost_increases_with_position"]
    assert s["of"] == SENSITIVITY_COMBOS
    assert s["holds_in"] == SENSITIVITY_COMBOS and s["robust"]


def test_the_tidy_story_does_not_survive_and_the_suite_says_so(report):
    """The intuitive claim is that the exchange rate falls as you move downstream.

    It does not, and this test exists to keep it that way. A last station sitting
    immediately before an expensive gate has a HIGHER exchange rate than the
    station before it, because what sets the operating point is the distance to
    the next thing that can catch the mode — not the position on its own.

    If someone later tunes the cost assumptions until the tidy story holds, this
    goes red rather than quietly agreeing with a conclusion that was never earned.
    """
    s = report["sensitivity"]["C2_exchange_rate_decreases_with_position"]
    assert not s["robust"]
    assert 0 < s["holds_in"] < s["of"]
    assert any(f["id"] == "POSITION_IS_NOT_THE_DRIVER" for f in report["findings"])


def test_the_last_station_beats_the_middle_one_on_exchange_rate(cfg):
    """The concrete case behind the sensitivity result, on one line."""
    plan, cost = cfg
    econ = coverage.build_economics(plan, cost)
    a = econ[(econ.line_id == "LINE-A") & (econ["mode"] == coverage.COMMON_MODE)]
    by_pos = a.set_index("position")["exchange_rate"].to_dict()
    assert by_pos[2] < by_pos[1]      # falls from position 1 to 2, as expected
    assert by_pos[3] > by_pos[2]      # and rises again at the gate, which is the point


# --------------------------------------------------------------------------- #
# 5. determinism and blast radius
# --------------------------------------------------------------------------- #
def test_run_is_deterministic(report):
    first = {n: (MARTS / n).read_bytes()
             for n in ("inspection_coverage.csv", "station_economics.csv")}
    coverage.run()
    for name, blob in first.items():
        assert (MARTS / name).read_bytes() == blob


def test_coverage_never_writes_to_the_parity_verified_marts():
    """Guardrail with teeth.

    data/marts/daily_fpy.csv and station_scorecard.csv are the files Databricks
    produced and that the published scorecard and dashboard render. They were
    diffed cell-for-cell against the tested reference. Nothing in this module may
    touch them, and this test fails loudly if that ever changes.
    """
    before = {n: (MARTS / n).read_bytes() for n in PROTECTED_MARTS
              if (MARTS / n).exists()}
    assert before, "expected the Databricks marts to be present"
    coverage.run()
    for name, blob in before.items():
        assert (MARTS / name).read_bytes() == blob, f"coverage.run() modified {name}"


def test_cost_model_carries_a_basis_for_every_assumption(cfg):
    """Every number in the cost model must say where it came from.

    They are all placeholders today. The point is that the file cannot grow a
    number that has no stated basis without this going red.
    """
    _, cost = cfg
    for key in ("value_at_position", "false_reject", "escape"):
        assert f"basis_{key}" in cost, f"{key} has no basis field"
        assert cost[f"basis_{key}"].strip()


