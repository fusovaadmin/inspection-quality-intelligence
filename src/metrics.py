"""
Quality-analytics primitives: first-pass yield, rolling FPY (window-function
analog), p-chart SPC control limits, and process capability (Cpk).

Pure pandas/numpy so the exact same logic is unit-tested here AND mirrored 1:1 by
the PySpark version (src/pipeline_pyspark.py) that runs on Databricks. Keeping the
math in one tested place is the point — the Spark job and the SQL are ports of it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def first_pass(df: pd.DataFrame) -> pd.DataFrame:
    """First-pass inspection attempts only (inspection_pass == 1). First-pass yield
    deliberately ignores rework, because rework masks the true process yield."""
    return df[df["inspection_pass"] == 1].copy()


def daily_fpy(fp: pd.DataFrame) -> pd.DataFrame:
    """Per station per day: units inspected, first-pass fails, FPY, defect rate."""
    g = fp.assign(date=fp["ts"].dt.floor("D"),
                  is_fail=(fp["vision_result"] == "FAIL").astype(int))
    out = (g.groupby(["station_id", "date"])
             .agg(n=("event_id", "size"), fails=("is_fail", "sum"))
             .reset_index())
    out["defect_rate"] = out["fails"] / out["n"]
    out["fpy"] = 1.0 - out["defect_rate"]
    return out.sort_values(["station_id", "date"]).reset_index(drop=True)


def add_rolling_fpy(daily: pd.DataFrame, window: int = 7) -> pd.DataFrame:
    """Trailing-window mean FPY per station — the pandas analog of
    AVG(fpy) OVER (PARTITION BY station_id ORDER BY date ROWS BETWEEN
    (window-1) PRECEDING AND CURRENT ROW)."""
    daily = daily.sort_values(["station_id", "date"]).copy()
    daily["rolling_fpy"] = (
        daily.groupby("station_id")["fpy"]
        .transform(lambda s: s.rolling(window, min_periods=1).mean())
    )
    return daily


def add_pchart_limits(daily: pd.DataFrame, sigma: int = 3) -> pd.DataFrame:
    """p-chart control limits with variable subgroup size n_i.

    center  pbar = sum(fails)/sum(n) per station
    limits  pbar +/- sigma*sqrt(pbar*(1-pbar)/n_i)   (Shewhart, per-subgroup n)
    rule-1  flag any point beyond the control limits.
    """
    daily = daily.copy()
    tot_fail = daily.groupby("station_id")["fails"].transform("sum")
    tot_n = daily.groupby("station_id")["n"].transform("sum")
    pbar = tot_fail / tot_n
    se = np.sqrt(pbar * (1.0 - pbar) / daily["n"])
    daily["pbar"] = pbar
    daily["ucl"] = (pbar + sigma * se).clip(upper=1.0)
    daily["lcl"] = (pbar - sigma * se).clip(lower=0.0)
    daily["out_of_control"] = (
        (daily["defect_rate"] > daily["ucl"]) | (daily["defect_rate"] < daily["lcl"])
    )
    return daily


def cpk(values, lsl: float, usl: float) -> float:
    """Process capability index Cpk = min((USL-mu)/3s, (mu-LSL)/3s).
    Non-finite values are dropped (sensor glitches must not poison capability)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 2:
        return float("nan")
    mu, sd = v.mean(), v.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(min((usl - mu) / (3 * sd), (mu - lsl) / (3 * sd)))
