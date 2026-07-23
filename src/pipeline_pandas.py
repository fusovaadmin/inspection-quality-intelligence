"""
End-to-end pipeline: raw inspection events -> validated -> station quality marts.

    raw CSV  ->  control-plan validation  ->  first-pass yield + rolling FPY
             ->  p-chart SPC limits/flags  ->  Cpk per station  ->  Pareto
             ->  findings.json + scorecard marts

Run:  python -m src.pipeline_pandas
This is the reference implementation; src/pipeline_pyspark.py is the Databricks port.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src import metrics
from src.validation import load_control_plan, validate

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "inspection_events.csv"
MARTS = ROOT / "data" / "marts"
RECENT_DAYS = 14


def run() -> dict:
    plan = load_control_plan()
    feat = plan["features"][0]
    lsl, usl, cpk_min = feat["lsl_mm"], feat["usl_mm"], feat["cpk_min"]
    window = plan["spc"]["rolling_window_days"]
    sigma = plan["spc"]["sigma"]

    df = pd.read_csv(RAW, parse_dates=["ts"])
    clean, dq = validate(df, plan, persist=True)

    # --- yield / SPC on first-pass events
    fp = metrics.first_pass(clean)
    daily = metrics.daily_fpy(fp)
    daily = metrics.add_rolling_fpy(daily, window=window)
    daily = metrics.add_pchart_limits(daily, sigma=sigma)

    # --- capability on valid (in physical range) first-pass measurements
    valid_dim = fp[fp["feature_dim_mm"].between(lsl - 5, usl + 5)]
    cutoff = fp["ts"].max().floor("D") - pd.Timedelta(days=RECENT_DAYS)

    rows = []
    for st, g in daily.groupby("station_id"):
        dim_all = valid_dim[valid_dim.station_id == st]["feature_dim_mm"]
        dim_recent = valid_dim[(valid_dim.station_id == st)
                               & (valid_dim.ts >= cutoff)]["feature_dim_mm"]
        viol = g[g["out_of_control"]]
        rows.append({
            "station_id": st,
            "line_id": fp[fp.station_id == st]["line_id"].iloc[0],
            "n_first_pass": int(g["n"].sum()),
            "fpy_overall": round(1 - g["fails"].sum() / g["n"].sum(), 4),
            "rolling_fpy_latest": round(float(g.sort_values("date")["rolling_fpy"].iloc[-1]), 4),
            "cpk_overall": round(metrics.cpk(dim_all, lsl, usl), 3),
            "cpk_recent": round(metrics.cpk(dim_recent, lsl, usl), 3),
            "defects": int(g["fails"].sum()),
            "spc_violations": int(viol.shape[0]),
            "spc_first_violation": (viol.sort_values("date")["date"].iloc[0].date().isoformat()
                                    if not viol.empty else None),
        })
    scorecard = pd.DataFrame(rows)

    def _status(r) -> str:
        # A quality engineer triages a control chart by persistence, not by any
        # single point. Lost capability, or a sustained run of out-of-control
        # points (>=3, well beyond the 3-sigma 0.27% false-alarm rate), is an
        # ALERT. One or two scattered points is a WATCH, not a fire drill.
        # (Production would layer full Western Electric run rules on top.)
        if (pd.notna(r.cpk_recent) and r.cpk_recent < cpk_min) or r.spc_violations >= 3:
            return "ALERT"
        if r.spc_violations >= 1:
            return "WATCH"
        return "OK"

    scorecard["status"] = [_status(r) for r in scorecard.itertuples()]

    # --- defect Pareto (first-pass fails)
    pareto = (fp[fp.vision_result == "FAIL"]["defect_code"].value_counts()
              .rename_axis("defect_code").reset_index(name="count"))
    pareto["cum_pct"] = (pareto["count"].cumsum() / pareto["count"].sum() * 100).round(1)

    # --- findings
    findings = {
        "spc_alerts": [
            {"station": r.station_id, "first_violation": r.spc_first_violation,
             "n_violations": r.spc_violations}
            for r in scorecard.itertuples() if r.spc_violations > 0
        ],
        "capability_alerts": [
            {"station": r.station_id, "cpk_overall": r.cpk_overall,
             "cpk_recent": r.cpk_recent, "cpk_min": cpk_min}
            for r in scorecard.itertuples()
            if pd.notna(r.cpk_recent) and r.cpk_recent < cpk_min
        ],
        "worst_station_by_fpy": scorecard.sort_values("fpy_overall")
            .iloc[0][["station_id", "fpy_overall"]].to_dict(),
        "top_defects": pareto.head(5).to_dict(orient="records"),
        "data_quality": dq,
    }

    MARTS.mkdir(parents=True, exist_ok=True)
    daily.to_csv(MARTS / "daily_fpy.csv", index=False)
    scorecard.to_csv(MARTS / "station_scorecard.csv", index=False)
    pareto.to_csv(MARTS / "pareto.csv", index=False)
    with open(MARTS / "findings.json", "w", encoding="utf-8") as f:
        json.dump(findings, f, indent=2, default=str)

    return {"scorecard": scorecard, "daily": daily, "pareto": pareto,
            "findings": findings, "dq": dq}


def main() -> None:
    out = run()
    sc, f = out["scorecard"], out["findings"]
    print("Station scorecard:")
    print(sc.to_string(index=False))
    print(f"\nData-quality score : {out['dq']['dq_score']}  "
          f"({out['dq']['offending_events']:,} offending events)")
    print(f"DQ by rule         : {out['dq']['by_rule']}")
    print(f"SPC alerts         : {f['spc_alerts']}")
    print(f"Capability alerts  : {f['capability_alerts']}")


if __name__ == "__main__":
    main()
