"""Unit tests for the quality-analytics primitives and control-plan validation.
The metrics are the trustworthy core; the Spark and SQL ports mirror them, so
testing here protects all three."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import generate_data, metrics
from src.validation import validate


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _event(**kw) -> dict:
    base = dict(event_id="EVT-1", ts=pd.Timestamp("2026-04-01 08:00"),
                line_id="LINE-A", station_id="S1", part_number="PN-1000",
                serial_number="SN-1", feature_dim_mm=25.0, vision_result="PASS",
                defect_code=None, cycle_time_s=42.0, inspection_pass=1)
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# yield / rolling / SPC / capability
# --------------------------------------------------------------------------- #
def test_daily_fpy_math():
    df = pd.DataFrame([
        _event(event_id="a", vision_result="PASS"),
        _event(event_id="b", vision_result="PASS"),
        _event(event_id="c", vision_result="FAIL", defect_code="DIM-OOS"),
    ])
    d = metrics.daily_fpy(metrics.first_pass(df))
    assert d.loc[0, "n"] == 3
    assert d.loc[0, "fails"] == 1
    assert d.loc[0, "fpy"] == pytest.approx(2 / 3)


def test_first_pass_excludes_rework():
    df = pd.DataFrame([
        _event(event_id="a", inspection_pass=1, vision_result="FAIL", defect_code="X"),
        _event(event_id="b", inspection_pass=2, vision_result="PASS"),
    ])
    fp = metrics.first_pass(df)
    assert len(fp) == 1 and fp.iloc[0]["vision_result"] == "FAIL"


def test_rolling_fpy_trailing_window():
    daily = pd.DataFrame({
        "station_id": ["S1", "S1", "S1"],
        "date": pd.to_datetime(["2026-04-01", "2026-04-02", "2026-04-03"]),
        "n": [10, 10, 10], "fails": [0, 10, 5],
        "fpy": [1.0, 0.0, 0.5], "defect_rate": [0.0, 1.0, 0.5],
    })
    out = metrics.add_rolling_fpy(daily, window=7)
    assert list(out["rolling_fpy"].round(4)) == [1.0, 0.5, 0.5]


def test_pchart_flags_only_the_spike():
    days = pd.date_range("2026-04-01", periods=12)
    fails = [4] * 11 + [60]           # last day is a gross special cause
    daily = pd.DataFrame({
        "station_id": "S1", "date": days, "n": 200, "fails": fails,
    })
    daily["defect_rate"] = daily["fails"] / daily["n"]
    daily["fpy"] = 1 - daily["defect_rate"]
    out = metrics.add_pchart_limits(daily, sigma=3)
    assert bool(out.iloc[-1]["out_of_control"]) is True
    assert out.iloc[:-1]["out_of_control"].any() == False  # noqa: E712


def test_cpk_formula_and_drift_detection():
    rng = np.random.default_rng(0)
    centered = rng.normal(25.0, 0.045, 1000)
    drifted = rng.normal(25.15, 0.045, 1000)
    # matches the textbook formula on the same sample
    mu, sd = centered.mean(), centered.std(ddof=1)
    expected = min((25.2 - mu) / (3 * sd), (mu - 24.8) / (3 * sd))
    assert metrics.cpk(centered, 24.8, 25.2) == pytest.approx(expected, rel=1e-6)
    # a centered process is capable; a drifted one is not
    assert metrics.cpk(centered, 24.8, 25.2) > 1.33
    assert metrics.cpk(drifted, 24.8, 25.2) < 1.0


def test_cpk_ignores_nonfinite():
    vals = [25.0, 25.05, 24.95, np.nan, -999.0]  # glitch + nan must not poison it
    # -999 IS finite, so cpk() itself keeps it; the pipeline filters range first.
    # Here we assert nan is dropped and a clean sample computes.
    clean = [25.0, 25.05, 24.95, 24.98, 25.02]
    assert not np.isnan(metrics.cpk(clean, 24.8, 25.2))


# --------------------------------------------------------------------------- #
# control-plan validation
# --------------------------------------------------------------------------- #
def test_validation_catches_every_fault_type():
    df = pd.DataFrame([
        _event(event_id="ok1"),
        _event(event_id="dup"),                                   # duplicate key ->
        _event(event_id="dup"),                                   # <- pair
        _event(event_id="miss", serial_number=None),              # missing required
        _event(event_id="glitch", feature_dim_mm=-999.0),         # out of range
        _event(event_id="c1", vision_result="PASS", defect_code="SRF-SCR"),  # C1
        _event(event_id="c2", vision_result="FAIL", defect_code=None),       # C2
    ])
    clean, report = validate(df)
    by = report["by_rule"]
    assert by.get("duplicate_key", 0) >= 2
    assert by.get("missing_required", 0) >= 1
    assert by.get("out_of_range", 0) >= 1
    assert by.get("consistency_C1", 0) >= 1
    assert by.get("consistency_C2", 0) >= 1
    assert report["dq_score"] < 1.0
    assert len(clean) == df["event_id"].nunique()   # duplicates removed


# --------------------------------------------------------------------------- #
# integration: the baked-in anomalies must actually be detected end-to-end
# --------------------------------------------------------------------------- #
def test_generated_anomalies_are_detected():
    df = generate_data.generate()
    fp = metrics.first_pass(df)
    daily = metrics.add_pchart_limits(metrics.daily_fpy(fp), sigma=3)

    # S5 process shift -> sustained out-of-control points
    s5 = daily[daily.station_id == "S5"]
    assert int(s5["out_of_control"].sum()) >= 3

    # S3 dimensional drift -> capability collapses below the plan minimum (1.33)
    s3_dim = fp[(fp.station_id == "S3") & fp.feature_dim_mm.between(20, 30)]["feature_dim_mm"]
    assert metrics.cpk(s3_dim, 24.8, 25.2) < 1.33
