"""
Synthetic vision-inspection-station event generator.

Emits the kind of record an automated optical / vision inspection station writes
on a production line: one row per inspection event, with a critical-to-quality
(CTQ) dimensional measurement, a pass/fail vision result, and a defect code on
failure. Realistic quality signals are baked in so the downstream scorecard, SPC,
and data-quality checks have something real to find:

  * Station S5 : step increase in defect rate after day 40  (process shift -> p-chart)
  * Station S3 : gradual dimensional drift toward the USL    (Cpk erosion)
  * ~1% missing serials, duplicate event_ids, sensor glitches, and
    PASS-with-defect-code contradictions                     (data-quality faults)

Deterministic: fixed seed + fixed start date, so every run is identical and the
tests can assert on known outcomes. Run:  python -m src.generate_data
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START_DATE = "2026-04-01"
N_DAYS = 60
STATIONS = ["S1", "S2", "S3", "S4", "S5", "S6"]
LINE_OF = {"S1": "LINE-A", "S2": "LINE-A", "S3": "LINE-A",
           "S4": "LINE-B", "S5": "LINE-B", "S6": "LINE-B"}
PART_NUMBER = "PN-1000"
NOMINAL, LSL, USL, SIGMA = 25.00, 24.80, 25.20, 0.045  # baseline Cpk ~1.48 (in control)
DEFECT_CODES = ["SLD-BRDG", "CMP-MISS", "SRF-SCR", "LBL-ERR"]  # non-dimensional
DEFECT_WEIGHTS = [0.45, 0.28, 0.18, 0.09]                      # Pareto shape

# Baked-in anomaly parameters (tests assert against these).
S5_SHIFT_DAY = 40
S5_SHIFT_MAGNITUDE = 0.045
S3_DRIFT_TOTAL_MM = 0.15

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "inspection_events.csv"


def _station_defect_rate(station: str, day: int) -> float:
    base = {"S1": 0.020, "S2": 0.025, "S3": 0.022,
            "S4": 0.028, "S5": 0.024, "S6": 0.021}[station]
    if station == "S5" and day >= S5_SHIFT_DAY:
        base += S5_SHIFT_MAGNITUDE
    return base


def _station_dim_mean(station: str, day: int) -> float:
    if station == "S3":
        return NOMINAL + (day / N_DAYS) * S3_DRIFT_TOTAL_MM
    return NOMINAL


def generate() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    dates = pd.date_range(START_DATE, periods=N_DAYS, freq="D")
    rows = []
    eid = 0
    for di, day_ts in enumerate(dates):
        for station in STATIONS:
            n = int(rng.integers(150, 210))
            dim_mean = _station_dim_mean(station, di)
            drate = _station_defect_rate(station, di)
            for u in range(n):
                eid += 1
                dim = float(rng.normal(dim_mean, SIGMA))
                if dim < LSL or dim > USL:
                    result, code = "FAIL", "DIM-OOS"
                elif rng.random() < drate:
                    result = "FAIL"
                    code = str(rng.choice(DEFECT_CODES, p=DEFECT_WEIGHTS))
                else:
                    result, code = "PASS", None
                ts = day_ts + pd.Timedelta(minutes=int(rng.integers(0, 1440)))
                rows.append(dict(
                    event_id=f"EVT-{eid:07d}", ts=ts, line_id=LINE_OF[station],
                    station_id=station, part_number=PART_NUMBER,
                    serial_number=f"SN-{station}-{di:02d}-{u:04d}",
                    feature_dim_mm=round(dim, 4), vision_result=result,
                    defect_code=code, cycle_time_s=round(float(rng.normal(42, 6)), 1),
                    inspection_pass=1,
                ))
    df = pd.DataFrame(rows)

    # --- Rework: ~70% of first-pass fails get re-inspected next day, mostly pass.
    fails = df[df.vision_result == "FAIL"]
    rework = fails.sample(frac=0.70, random_state=SEED).copy()
    rework["inspection_pass"] = 2
    rework["ts"] = rework["ts"] + pd.Timedelta(days=1)
    passed = rng.random(len(rework)) < 0.85
    rework["feature_dim_mm"] = np.round(rng.normal(NOMINAL, SIGMA, len(rework)), 4)
    rework["vision_result"] = np.where(passed, "PASS", "FAIL")
    rework["defect_code"] = np.where(passed, None, rework["defect_code"].values)
    rework["event_id"] = [f"EVT-{eid + 1 + i:07d}" for i in range(len(rework))]
    df = pd.concat([df, rework], ignore_index=True)

    # --- Inject data-quality faults so validation has real defects to catch.
    df = df.sample(frac=1.0, random_state=SEED).reset_index(drop=True)
    n = len(df)
    miss = rng.choice(n, size=int(0.010 * n), replace=False)
    df.loc[miss, "serial_number"] = None
    glitch = rng.choice(n, size=int(0.005 * n), replace=False)
    df.loc[glitch, "feature_dim_mm"] = -999.0
    contra = rng.choice(n, size=int(0.004 * n), replace=False)
    df.loc[contra, "vision_result"] = "PASS"
    df.loc[contra, "defect_code"] = "SRF-SCR"
    dup = df.sample(n=int(0.003 * n), random_state=SEED).copy()  # duplicate event_ids
    df = pd.concat([df, dup], ignore_index=True)

    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    RAW.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(RAW, index=False)
    return df


def main() -> None:
    df = generate()
    fp = df[df.inspection_pass == 1]
    print(f"Wrote {len(df):,} events -> {RAW}")
    print(f"  first-pass events : {len(fp):,}")
    print(f"  stations          : {sorted(df.station_id.unique())}")
    print(f"  date range        : {df.ts.min()}  ->  {df.ts.max()}")
    print(f"  overall first-pass yield : {(fp.vision_result == 'PASS').mean():.3f}")


if __name__ == "__main__":
    main()
