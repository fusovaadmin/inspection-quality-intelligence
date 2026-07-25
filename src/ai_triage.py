"""
AI-assisted root-cause triage over the pipeline's findings.

For each flagged station it classifies the *signal pattern* (gradual dimensional
drift vs. discrete process shift vs. scattered false alarm) and emits a
quality-engineering root-cause hypothesis + recommended actions, plus a
platform-level data-quality triage. This is the deterministic triage engine —
grounded in SPC/RCCA logic, so it runs anywhere with no API key — and its
structured output is exactly what you hand to Claude to enrich the narrative
("compress hours of manual triage into minutes", straight from the JD).

Run:  python -m src.ai_triage   (after src.pipeline_pandas)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from src.validation import load_control_plan

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "inspection_events.csv"
MARTS = ROOT / "data" / "marts"
RECENT_DAYS = 14
BASELINE_DAYS = 21


def _dim_trend(raw: pd.DataFrame, station: str, lsl: float, usl: float) -> dict:
    fp = raw[(raw.inspection_pass == 1) & (raw.station_id == station)].copy()
    fp = fp[fp.feature_dim_mm.between(lsl - 5, usl + 5)]
    fp["date"] = fp.ts.dt.floor("D")
    days = sorted(fp["date"].unique())
    early = fp[fp["date"].isin(days[:RECENT_DAYS])]["feature_dim_mm"].mean()
    late = fp[fp["date"].isin(days[-RECENT_DAYS:])]["feature_dim_mm"].mean()
    toward = "USL" if (usl - late) < (late - lsl) else "LSL"
    return {"early_mean": round(float(early), 4), "late_mean": round(float(late), 4),
            "drift": round(float(late - early), 4), "toward": toward}


def _defect_trend(daily: pd.DataFrame, station: str) -> dict:
    g = daily[daily.station_id == station].sort_values("date")
    baseline = g.head(BASELINE_DAYS)["defect_rate"].mean()
    recent = g.tail(RECENT_DAYS)["defect_rate"].mean()
    return {"baseline_rate": round(float(baseline), 4), "recent_rate": round(float(recent), 4),
            "delta": round(float(recent - baseline), 4)}


def _triage_station(r, dim, dfc, onset, feat) -> dict:
    lsl, usl, cpk_min = feat["lsl_mm"], feat["usl_mm"], feat["cpk_min"]
    losing_capability = pd.notna(r.cpk_recent) and r.cpk_recent < cpk_min
    drifting = abs(dim["drift"]) >= 0.03
    sustained_spc = r.spc_violations >= 3

    if losing_capability and drifting:
        pattern, severity = "dimensional_drift", "high"
        hypothesis = (
            f"Progressive dimensional drift on {feat['name']}: the mean moved "
            f"{dim['early_mean']}→{dim['late_mean']} mm toward the {dim['toward']} "
            f"({usl if dim['toward']=='USL' else lsl} mm), and Cpk fell "
            f"{r.cpk_overall}→{r.cpk_recent} (below the {cpk_min} plan minimum). "
            f"A slow, monotonic move points to a changing process input, not a random event."
        )
        causes = ["Tool / insert wear or a slowly loosening fixture on the station",
                  "Thermal growth or machine warm-up drift",
                  "Vision-gauge drift / lost calibration (measurement, not the part)",
                  "Gradual lot-to-lot material property change"]
        actions = [f"Contain units back to the drift onset; measure tool-wear offset and re-zero the fixture",
                   "Run a gauge check (MSA / Gage R&R) to rule out measurement drift",
                   "Add drift compensation or shorten the PM interval",
                   "Open an 8D/RCCA tied to the control-plan reaction plan"]
    elif sustained_spc:
        pattern, severity = "process_shift", "high"
        hypothesis = (
            f"Step change in first-pass defect rate around {onset or 'the flagged date'}: "
            f"baseline ~{dfc['baseline_rate']:.1%} jumped to ~{dfc['recent_rate']:.1%}, tripping the "
            f"p-chart on {int(r.spc_violations)} points. A sudden shift points to a discrete change "
            f"event, not gradual wear."
        )
        causes = ["Incoming material lot change",
                  "Setup / changeover or a fixture swap",
                  "Operator / shift change or a work-instruction revision",
                  "Inspection-program or vision-threshold update"]
        actions = [f"Pull lot & changeover traceability bracketing {onset or 'the onset'}; compare pre/post lots",
                   "Review the station change log and recent program revisions",
                   "Re-verify the vision threshold against a known-good master part"]
    elif r.spc_violations >= 1:
        pattern, severity = "scattered_false_alarm", "low"
        hypothesis = ("One or two isolated points beyond the limits with capability intact — "
                      "consistent with the expected 0.27% false-alarm rate, not a special cause.")
        causes = ["Random variation at the 3-sigma limit"]
        actions = ["Monitor; apply Western Electric run rules before escalating",
                   "No containment indicated yet"]
    else:
        pattern, severity = "in_control", "none"
        hypothesis = "In statistical control and capable; no action indicated."
        causes, actions = [], ["Continue routine monitoring"]

    return {"station": r.station_id, "line": r.line_id, "severity": severity,
            "pattern": pattern, "fpy": r.fpy_overall, "cpk_recent": r.cpk_recent,
            "spc_violations": int(r.spc_violations), "hypothesis": hypothesis,
            "likely_causes": causes, "recommended_actions": actions,
            "evidence": {"dim_trend": dim, "defect_trend": dfc, "onset": onset}}


def run() -> dict:
    plan = load_control_plan()
    feat = plan["features"][0]
    lsl, usl = feat["lsl_mm"], feat["usl_mm"]

    raw = pd.read_csv(RAW, parse_dates=["ts"])
    daily = pd.read_csv(MARTS / "daily_fpy.csv", parse_dates=["date"])
    sc = pd.read_csv(MARTS / "station_scorecard.csv")
    findings = json.loads((MARTS / "findings.json").read_text())
    onset_of = {a["station"]: a["first_violation"] for a in findings["spc_alerts"]}

    stations = [
        _triage_station(r, _dim_trend(raw, r.station_id, lsl, usl),
                        _defect_trend(daily, r.station_id),
                        onset_of.get(r.station_id), feat)
        for r in sc.itertuples()
    ]

    dq = findings["data_quality"]
    by_rule = ", ".join(f"{k}={v}" for k, v in dq["by_rule"].items())
    platform = {
        "severity": "medium" if dq["dq_score"] < 0.99 else "low",
        "hypothesis": (
            f"{dq['offending_events']:,} of {dq['total_events']:,} events failed control-plan "
            f"validation ({by_rule}). Missing serials + duplicate IDs point to a scanner/ingestion "
            f"integration issue; out-of-range values to a sensor/PLC fault; PASS-with-defect-code to a "
            f"result-mapping bug."),
        "recommended_actions": [
            "Audit the station data path: scanner → PLC → historian → pipeline",
            "Enforce idempotent event IDs and source-side range checks",
            "Keep quarantining failing rows from metrics (already enforced by the pipeline)"],
        "dq_score": dq["dq_score"],
    }

    triage = {"stations": stations, "data_quality": platform}
    (MARTS / "triage.json").write_text(json.dumps(triage, indent=2, default=str), encoding="utf-8")
    return triage


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    except Exception:
        pass
    t = run()
    print("AI root-cause triage\n" + "=" * 60)
    for s in t["stations"]:
        if s["severity"] in ("high", "medium"):
            print(f"\n[{s['severity'].upper()}] {s['station']} — {s['pattern']}")
            print(f"  {s['hypothesis']}")
            print(f"  → {s['recommended_actions'][0]}")
    print(f"\n[DATA QUALITY] {t['data_quality']['hypothesis']}")


if __name__ == "__main__":
    main()
