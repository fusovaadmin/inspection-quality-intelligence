"""
Tests for the station self-test, tooling wear trending, and the loop closure on both.

The cell proves it can still do its job before it judges parts — the same reason a
robot checks its home position. Four things are proved here:

  1. GATING — which self-test step fails decides WHICH inspections the station
     loses. Imaging failure and measurement failure cost different things, and
     they point in opposite directions. That selectivity is the reason neither
     shows up in yield.
  2. PERSISTENCE — a dip that recovered is a WATCH, not an alert.
  3. WEAR TRENDING — a rate is only projected where a trend was actually observed.
     On a healthy fixture the "rate" is measurement noise, and extrapolating noise
     produces a confident-looking replacement date built on nothing.
  4. THE LINE THAT MUST NOT BE CROSSED — trending a measurement to a SPEC LIMIT is
     arithmetic. Predicting when something will fail is prognostics, and nothing
     here supports that claim. A test scans every finding for forecast language.
"""
from __future__ import annotations

import pandas as pd
import pytest
import yaml

from src import coverage
from src.validation import load_control_plan


@pytest.fixture(scope="module")
def report():
    return coverage.run()


@pytest.fixture(scope="module")
def selftest():
    plan = load_control_plan()
    cfg = coverage.load_yaml(coverage.ENV_MODEL)
    data = pd.read_csv(coverage.ENV_TELEMETRY)
    return plan, cfg, data, coverage.build_station_condition(plan, cfg, data)


# --------------------------------------------------------------------------- #
# 1. gating — which step failing costs which inspections
# --------------------------------------------------------------------------- #
def test_selftest_records_are_deterministic():
    from src import generate_selftest
    first = coverage.ENV_TELEMETRY.read_bytes()
    generate_selftest.generate()
    assert coverage.ENV_TELEMETRY.read_bytes() == first


def test_each_step_gates_a_different_class_of_inspection(selftest):
    """The gating map is what makes a self-test more than a health light.

    "The cell is red" is not an answer. "This station can no longer be trusted to
    find these specific things" is, and that needs to know which step gates what.
    """
    _, cfg, _, _ = selftest
    assert coverage.gates_for("appearance_low_contrast", cfg) == ["imaging_check"]
    assert coverage.gates_for("appearance_high_contrast", cfg) == ["imaging_check"]
    assert coverage.gates_for("geometry", cfg) == ["measurement_check", "tooling_check"]


def test_failed_imaging_costs_visual_checks_and_spares_the_measurement(selftest):
    """S9's lamp faded. Finding an edge survives poor contrast; seeing a faint
    scratch does not. If it took everything down at once, yield would catch it."""
    _, _, _, cond = selftest
    s9 = cond[cond.station_id == "S9"]
    alert = s9[s9.condition == "ALERT"]
    assert not alert.empty
    assert alert.detection_basis.str.startswith("appearance").all()
    assert (s9.loc[s9.detection_basis == "geometry", "condition"] == "OK").all()


def test_failed_measurement_costs_size_checks_and_spares_the_visual(selftest):
    """S2's fixture wore. The exact inverse of S9 — same page, opposite failure."""
    _, _, _, cond = selftest
    s2 = cond[cond.station_id == "S2"]
    alert = s2[s2.condition == "ALERT"]
    assert not alert.empty
    assert (alert.detection_basis == "geometry").all()
    assert (s2.loc[s2.detection_basis.str.startswith("appearance"),
                   "condition"] == "OK").all()


def test_a_two_day_dip_is_a_watch_and_not_an_alert(selftest):
    """Persistence, not one reading. A tool that fires on a dip that fixed itself
    gets closed, and then it stays closed."""
    _, cfg, _, cond = selftest
    s6 = cond[cond.station_id == "S6"]
    watch = s6[s6.condition == "WATCH"]
    assert not watch.empty
    assert (s6.condition != "ALERT").all()
    assert watch.longest_run_below.max() < cfg["monitoring"]["consecutive_days_to_alert"]


def test_effective_coverage_is_less_than_declared_coverage(report):
    assert report["effective_coverage_rate"] < 1.0
    assert report["station_condition_alerts"] == ["S2", "S8", "S9"]


def test_both_selectivity_claims_survive_their_sweeps(report):
    es = report["environment_sensitivity"]
    for key in ("E1_imaging_failure_costs_visual_checks_only",
                "E2_mechanical_failure_costs_measurement_only"):
        c = es[key]
        assert c["of"] > 0 and c["holds_in"] == c["of"], key


def test_the_degraded_stations_look_fine_on_every_existing_metric(report):
    """These findings are only interesting because the scorecard disagrees."""
    for st in report["station_condition_alerts"]:
        sc = coverage.scorecard_row(st)
        assert sc is not None
        assert sc["status"] in ("OK", "WATCH")


# --------------------------------------------------------------------------- #
# 2. tooling wear trending
# --------------------------------------------------------------------------- #
def test_wear_is_only_projected_where_a_trend_was_observed(selftest):
    """One row per (station, tooling set, tracked measure) — a gripper and a
    fixture wear against different things, so a blended number would hide both."""
    _, cfg, data, _ = selftest
    w = coverage.wear_trend(cfg, data)
    assert set(w.tooling_set) == {"fixture", "eoat"}
    moved = w[w.trend_detected]
    assert set(zip(moved.station_id, moved.tooling_set)) == {
        ("S2", "fixture"), ("S5", "eoat"), ("S8", "eoat")}
    # everything else is flat, and a flat "rate" is measurement noise
    flat = w[~w.trend_detected]
    assert not flat.empty
    assert flat.projected_units_to_limit.isna().all()


def test_wear_that_is_speeding_up_is_flagged(selftest):
    """Steady wear is a maintenance schedule. Accelerating wear means something
    else changed, and the parts made during it are the ones worth looking at."""
    _, cfg, data, _ = selftest
    w = coverage.wear_trend(cfg, data)
    s2 = w[(w.station_id == "S2") & (w.measure == "locator_wear_mm")].iloc[0]
    assert s2.accelerating
    assert s2.acceleration_ratio >= cfg["wear_trending"]["acceleration"]["flag_ratio"]
    assert s2.rate_second_half > s2.rate_first_half
    # S5's tool centre point walks STEADILY — a maintenance schedule, not a
    # product question. The contrast is the point.
    s5 = w[(w.station_id == "S5") & (w.measure == "tcp_offset_mm")].iloc[0]
    assert s5.trend_detected and not s5.accelerating


def test_tooling_inside_spec_is_a_watch_not_an_alert(report):
    f = [x for x in report["findings"] if x["id"] == "TOOLING_TRENDING_TO_LIMIT"]
    assert len(f) == 2
    assert all(x["severity"] == "watch" for x in f)
    assert {(x["station"], x["tooling_set"]) for x in f} == {("S2", "fixture"),
                                                             ("S5", "eoat")}


def test_every_projection_carries_its_disclaimer(report):
    """The label travels with the number so it cannot be quoted without it."""
    label = report["wear_trending"]["label"]
    assert report["wear_trending"]["projection_is_not_a_failure_prediction"] is True
    for f in report["findings"]:
        if f["id"] == "TOOLING_TRENDING_TO_LIMIT":
            assert label in f["detail"]


# --------------------------------------------------------------------------- #
# 3. loop closure — a finding with no action is a gate, not a system
# --------------------------------------------------------------------------- #
def test_every_selftest_finding_carries_a_cause_and_an_action(report):
    """Detection without loop closure is a quality gate, not a quality system.

    The SPC side of this platform already ends in a reaction plan. The self-test
    side has to as well, or it is just a second dashboard nobody opens.
    """
    gated = [f for f in report["findings"]
             if f["id"] in ("CONDITION_COVERAGE_LOST", "TOOLING_TRENDING_TO_LIMIT")]
    assert gated
    for f in gated:
        assert f.get("probable_causes"), f"{f['id']} has no probable causes"
        assert f.get("corrective_actions"), f"{f['id']} has no corrective actions"
        assert f.get("containment"), f"{f['id']} has no containment"
        assert f.get("owner")


def test_cause_and_action_are_looked_up_not_invented(report):
    """The attribution is deterministic: a failed step maps to a reviewed list in
    config. No model chooses the cause — the same discipline as the SPC triage
    engine, where code classifies and the LLM only narrates."""
    cfg = coverage.load_yaml(coverage.ENV_MODEL)
    plans = cfg["reaction_plans"]
    for f in report["findings"]:
        if f["id"] != "CONDITION_COVERAGE_LOST":
            continue
        c = f["cause"]
        key = ("imaging_check" if "imaging" in c
               else "tooling_check__eoat" if "tooling/eoat" in c
               else "tooling_check__fixture" if "tooling/fixture" in c
               else "measurement_check")
        assert set(f["probable_causes"]) <= set(plans[key]["probable_causes"])
        assert set(f["corrective_actions"]) <= set(plans[key]["corrective_actions"])


# --------------------------------------------------------------------------- #
# 4. honesty guardrails, encoded as tests
# --------------------------------------------------------------------------- #
def test_no_finding_claims_to_predict_a_failure(report):
    """Hard boundary. Trending a measurement to a SPEC LIMIT is arithmetic. Saying
    when a component will fail is prognostics, and nothing here supports it — no
    failure history, no reliability model, no censoring.

    Forecast language is allowed in exactly one place: inside the explicit
    disclaimer, where it appears in order to be denied. It is stripped before the
    scan so a real claim cannot hide behind the disclaimer's wording.
    """
    disclaimer = report["wear_trending"]["label"]
    banned = ("predict", "forecast", "remaining useful life", "will fail",
              "time to failure", "expected failure")
    for f in report["findings"]:
        blob = f"{f['headline']} {f['detail']}".replace(disclaimer, "").lower()
        for word in banned:
            assert word not in blob, f"prognostics language '{word}' in {f['id']}"


def test_nothing_here_touches_an_image(report):
    """The self-test reads measurements the cell reports, never pixels."""
    t = pd.read_csv(coverage.ENV_TELEMETRY)
    assert set(t.columns) == {
        "production_day", "line_id", "station_id", "units_processed",
        "ref_brightness_pct", "ref_sharpness_score", "datamatrix_grade",
        "ref_bias_mm", "ref_repeatability_mm",
        "locator_wear_mm", "clamp_offset_mm",
        "gripper_pad_wear_mm", "tcp_offset_mm", "jaw_parallelism_mm"}
    assert report["scope"]["processes_images"] is False
    assert report["scope"]["contains_vision_model"] is False


def test_the_report_declares_its_own_boundaries(report):
    scope = report["scope"]
    assert scope["forecasts_failures"] is False
    assert scope["data_is_synthetic"] is True
    assert scope["costs_are_assumptions_not_measurements"] is True


def test_every_selftest_limit_carries_a_stated_basis():
    cfg = coverage.load_yaml(coverage.ENV_MODEL)
    for check in ("imaging_check", "measurement_check", "tooling_check"):
        assert cfg[check]["basis"].strip()
        assert "PLACEHOLDER" in cfg[check]["basis"]


def test_no_currency_anywhere_in_the_cost_model():
    raw = coverage.COST_MODEL.read_text(encoding="utf-8")
    assert "$" not in raw
    assert yaml.safe_load(raw)["units"]["symbol"] == "VU"


# --------------------------------------------------------------------------- #
# 5. two tooling sets — attribution instead of a coin flip
# --------------------------------------------------------------------------- #
def test_the_cell_inspects_two_separate_tooling_sets(selftest):
    """The end-of-arm tooling PLACES the part; the fixture HOLDS it.

    Both push on the same reading at step 2, so a repeatability failure on its own
    cannot say which moved. Inspecting them as separate sets is the only thing
    that turns a symptom into an answer.
    """
    _, cfg, _, _ = selftest
    sets = coverage.tooling_sets(cfg)
    assert set(sets) == {"fixture", "eoat"}
    assert "gripper_pad_wear_mm" in sets["eoat"]["measures"]
    assert "locator_wear_mm" in sets["fixture"]["measures"]
    # both gate the same class of inspection — that is exactly why they are
    # ambiguous from step 2 alone
    assert coverage.gates_for("geometry", cfg) == ["measurement_check", "tooling_check"]


def test_a_worn_gripper_is_named_as_the_cause_and_the_fixture_is_cleared(selftest, report):
    """S8: repeatability is out AND the gripper is over its limit AND the fixture
    is inside it. That is an attribution, not a guess."""
    _, cfg, data, cond = selftest
    ev = coverage.evaluate_selftest(cfg, data)
    last = ev[ev.station_id == "S8"].iloc[-1]
    assert not last.tooling_eoat_ok          # the gripper moved
    assert last.tooling_fixture_ok           # the fixture did not
    assert coverage.failing_tooling_sets(cfg, last) == ["eoat"]

    s8 = cond[(cond.station_id == "S8") & (cond.condition == "ALERT")]
    assert not s8.empty
    assert "tooling/eoat" in s8.cause.iloc[0]
    assert "tooling/fixture" not in s8.cause.iloc[0]

    f = [x for x in report["findings"]
         if x["id"] == "CONDITION_COVERAGE_LOST" and x["station"] == "S8"][0]
    assert "not the part-holding fixture" in f["headline"]


def test_each_tooling_set_has_its_own_owner_and_corrective_actions(report):
    """A worn gripper is a robotics job. A worn locator is a tooling job. Routing
    both to the same owner with the same actions would waste the attribution."""
    by_st = {f["station"]: f for f in report["findings"]
             if f["id"] == "CONDITION_COVERAGE_LOST"}
    assert by_st["S8"]["owner"] == "Controls / Robotics"
    assert any("gripper pad" in a.lower() for a in by_st["S8"]["corrective_actions"])
    assert any("centre point" in a.lower() for a in by_st["S8"]["corrective_actions"])
    # and each plan tells the reader to rule the OTHER set out
    assert any("fixture is NOT also out" in a for a in by_st["S8"]["corrective_actions"])


def test_a_symptom_with_no_tooling_cause_is_reported_as_unresolved(report):
    """S2: repeatability is out while BOTH tooling sets are still inside limits.

    That is a real result, not a gap — either something outside the two sets is
    moving the part, or the tooling limits are too loose to protect the
    measurement. Saying so beats replacing a fixture on a hunch.
    """
    f = [x for x in report["findings"]
         if x["id"] == "CONDITION_COVERAGE_LOST" and x["station"] == "S2"][0]
    assert "BOTH tooling sets are still inside their own limits" in f["detail"]
    assert "before anything is replaced" in f["detail"]
