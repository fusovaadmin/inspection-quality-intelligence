"""
Heterogeneous system integration: inspection + ERP + MES + QMS -> one model.

Four systems, four grains, four dialects, no shared key. This is the join the
job description is actually describing:

    "Pull from diverse hardware production systems (ERP, MES, QMS, inventory)"
    "heterogeneous system joins, window functions, query tuning"

The shape of the pipeline is deliberate, and it is the same shape on any engine:

    LAND raw           each extract read exactly as the source system wrote it
      -> VALIDATE      the control-plan DQ gate runs BEFORE anything conforms
      -> CONFORM       crosswalk config maps each system's keys to canonical ones
      -> JOIN          three different join strategies, because the grains differ:
                         ERP : non-equi RANGE join   (line + date inside [start, end])
                         MES : INTERVAL join         (station + ts inside [start, end))
                         QMS : normalized-key join   (serial, after canonicalization)
      -> RECONCILE     referential integrity + quantity variance, reported not hidden
      -> MARTS         two marts, each at the grain of the system it reconciles

Nothing that fails to join is silently dropped. An orphan row is a finding for a
human, not garbage to discard — which is the whole point of the Data-Trust
pattern: check the DATA, not just the part.

Run:  python -m src.integrate
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src import metrics
from src.validation import load_control_plan, validate

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "inspection_events.csv"
SYSDIR = ROOT / "data" / "raw" / "systems"
MARTS = ROOT / "data" / "marts"
QDIR = ROOT / "data" / "quality"
CROSSWALK = ROOT / "config" / "system_crosswalk.yaml"


def load_crosswalk(path: Path = CROSSWALK) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# CONFORM — each system's dialect translated to the canonical model
# ---------------------------------------------------------------------------
def canonical_serial(s: pd.Series) -> pd.Series:
    """SN-S3-00-0142 (stations) and s3-00-0142 (QMS) are the same unit."""
    return s.astype("string").str.upper().str.replace("^SN-", "", regex=True).str.strip()


def conform_erp(erp: pd.DataFrame, xw: dict) -> pd.DataFrame:
    out = pd.DataFrame({
        "work_order_no": erp["WORK_ORDER_NO"],
        "item_no": erp["ITEM_NO"],
        "part_number": erp["ITEM_NO"].map(xw["item"]),          # unmapped -> NaN, reported
        "line_id": erp["PROD_LINE"].map(xw["line"]),
        "wo_status": erp["WO_STATUS"],
        # ERP writes dates as MM/DD/YYYY strings and has no timestamps at all.
        "sched_start": pd.to_datetime(erp["SCHED_START_DT"], format="%m/%d/%Y"),
        "sched_end": pd.to_datetime(erp["SCHED_END_DT"], format="%m/%d/%Y"),
        "qty_ordered": erp["QTY_ORDERED"].astype(int),
    })
    return out.sort_values(["line_id", "sched_start"]).reset_index(drop=True)


def conform_mes(mes: pd.DataFrame, xw: dict) -> pd.DataFrame:
    out = pd.DataFrame({
        "shift_id": mes["shift_id"],
        "work_center": mes["work_center"],
        "station_id": mes["work_center"].map(xw["work_center"]),
        "shift_code": mes["shift_code"],
        "shift_start_ts": pd.to_datetime(mes["shift_start_ts"]),
        "shift_end_ts": pd.to_datetime(mes["shift_end_ts"]),
        # MES stores the work order without the "WO-" prefix ERP uses.
        "work_order_no": mes["wo_no"].apply(
            lambda v: f"WO-{int(v)}" if str(v).strip() not in ("", "nan") else None),
        "units_started": mes["units_started"].astype(int),
        "units_completed": mes["units_completed"].astype(int),
        "downtime_min": mes["downtime_min"].astype(int),
    })
    out["prod_day"] = out["shift_start_ts"].dt.floor("D")
    return out.sort_values(["station_id", "shift_start_ts"]).reset_index(drop=True)


def conform_qms(qms: pd.DataFrame, xw: dict) -> pd.DataFrame:
    out = pd.DataFrame({
        "ncr_no": qms["NCR_NO"],
        "serial_key": canonical_serial(qms["SERIAL_NO"]),
        "defect_category": qms["DEFECT_CATEGORY"],
        # QMS speaks a human taxonomy; the stations speak machine codes.
        "defect_code_qms": qms["DEFECT_CATEGORY"].map(xw["defect_category"]),
        "severity": qms["SEVERITY"],
        "disposition": qms["DISPOSITION"],
        "opened_dt": pd.to_datetime(qms["OPENED_DT"]),
    })
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# JOIN — three strategies, because three grains
# ---------------------------------------------------------------------------
def sql_round(x, nd: int = 3):
    """Round half AWAY FROM ZERO, the way SQL engines do.

    numpy/pandas round half to EVEN, so 1.5625 comes out 1.562 in pandas and
    1.563 in DuckDB, Spark and Snowflake. That is a real one-digit divergence
    between the reference implementation and every engine it gets ported to, and
    it only shows up on exact ties — which is exactly how it survives into
    production and then gets written off as a flaky test. The reference matches
    the engines here, not the other way round.
    """
    f = 10 ** nd
    return np.sign(x) * np.floor(np.abs(x) * f + 0.5) / f


def production_day(ts: pd.Series) -> pd.Series:
    """The MES production day: the date the shift STARTED on. Shift C runs
    22:00 -> 06:00, so events before 06:00 belong to the previous day."""
    return (ts - pd.Timedelta(hours=6)).dt.floor("D")


def shift_code(ts: pd.Series) -> pd.Series:
    h = ts.dt.hour
    return pd.Series(np.where(h < 6, "C", np.where(h < 14, "A", np.where(h < 22, "B", "C"))),
                     index=ts.index)


def join_all(fp: pd.DataFrame, erp: pd.DataFrame, mes: pd.DataFrame,
             qms: pd.DataFrame) -> pd.DataFrame:
    """Event-grain fact with ERP, MES and QMS context attached."""
    ev = fp.copy()
    ev["date"] = ev["ts"].dt.floor("D")
    ev["prod_day"] = production_day(ev["ts"])
    ev["shift_code"] = shift_code(ev["ts"])
    ev["shift_id"] = (ev["prod_day"].dt.date.astype(str) + "-" + ev["shift_code"])
    ev["serial_key"] = canonical_serial(ev["serial_number"])

    # --- ERP: non-equi RANGE join. Work orders are contiguous and disjoint per
    # line, so merge_asof finds the latest one that started on or before the
    # event date; the explicit end-date test is what turns an early-closed work
    # order into an orphan instead of a silent wrong answer.
    ev = ev.sort_values("date", kind="stable")
    e = erp.sort_values("sched_start", kind="stable")
    ev = pd.merge_asof(ev, e[["line_id", "sched_start", "sched_end", "work_order_no",
                              "item_no", "part_number", "wo_status"]],
                       left_on="date", right_on="sched_start", by="line_id",
                       direction="backward")
    outside = ev["sched_end"].isna() | (ev["date"] > ev["sched_end"])
    for col in ["work_order_no", "item_no", "part_number", "wo_status"]:
        ev.loc[outside, col] = None
    ev["has_work_order"] = ~outside

    # --- MES: the shift key is DERIVED from the timestamp, then joined. The
    # equivalent SQL (sql/shift_interval_join.sql) uses a true interval
    # predicate instead; tests/test_integration.py asserts they agree.
    ev = ev.merge(mes[["station_id", "shift_id", "units_completed", "downtime_min",
                       "work_order_no"]].rename(columns={"work_order_no": "mes_work_order_no",
                                                         "units_completed": "mes_units_completed"}),
                  on=["station_id", "shift_id"], how="left")
    ev["has_mes_shift"] = ev["mes_units_completed"].notna()

    # --- QMS: sparse left join on the canonicalized serial.
    ev = ev.merge(qms[["serial_key", "ncr_no", "defect_code_qms", "severity", "disposition"]],
                  on="serial_key", how="left")
    return ev


# ---------------------------------------------------------------------------
# RECONCILE + MARTS
# ---------------------------------------------------------------------------
def build_marts(ev: pd.DataFrame, mes: pd.DataFrame, tol_pct: float):
    """Two marts, each at the grain of the system it reconciles."""
    # Mart 1 — station x CALENDAR date (the grain ERP and daily_fpy both use).
    wo = (ev.groupby(["station_id", "date"], dropna=False)
            .agg(line_id=("line_id", "first"),
                 n_first_pass=("event_id", "size"),
                 n_fail=("vision_result", lambda s: int((s == "FAIL").sum())),
                 work_order_no=("work_order_no", "first"),
                 item_no=("item_no", "first"),
                 ncr_count=("ncr_no", "count"),
                 ncr_scrap=("disposition", lambda s: int((s == "SCRAP").sum())))
            .reset_index())
    wo["has_work_order"] = wo["work_order_no"].notna()

    # Window functions over the joined result: how far into the work order this
    # station is, and units inspected against it to date. The pandas analog of
    #   ROW_NUMBER() / SUM(...) OVER (PARTITION BY work_order_no, station_id
    #                                 ORDER BY date)
    # Rows with no work order get NULL, not a phantom partition of their own.
    wo = wo.sort_values(["work_order_no", "station_id", "date"]).reset_index(drop=True)
    g = wo.groupby(["work_order_no", "station_id"], dropna=True)
    wo["wo_day_seq"] = (g.cumcount() + 1).astype("Int64")
    wo["wo_cum_units"] = g["n_first_pass"].cumsum().astype("Int64")
    wo.loc[~wo["has_work_order"], ["wo_day_seq", "wo_cum_units"]] = pd.NA

    wo = wo[["line_id", "station_id", "date", "work_order_no", "item_no",
             "n_first_pass", "n_fail", "ncr_count", "ncr_scrap", "has_work_order",
             "wo_day_seq", "wo_cum_units"]]
    wo = wo.sort_values(["station_id", "date"]).reset_index(drop=True)

    # Mart 2 — station x PRODUCTION day x shift (the grain MES uses). Every
    # shift the MES logged, plus every shift the stations produced into,
    # so an outage shows up as a row with events and no MES record.
    ins = (ev.groupby(["station_id", "prod_day", "shift_code", "shift_id"])
             .agg(inspection_events=("event_id", "size")).reset_index())
    rec = ins.merge(mes[["station_id", "shift_id", "units_completed", "downtime_min",
                         "work_order_no"]],
                    on=["station_id", "shift_id"], how="outer")
    rec["shift_present"] = rec["units_completed"].notna()
    rec["inspection_events"] = rec["inspection_events"].fillna(0).astype(int)
    # Same association order as the SQL port — (diff * 100) / n, not
    # (diff / n) * 100 — so the two agree to the last bit, not just to eyeball.
    raw_var = np.where(
        rec["shift_present"] & (rec["inspection_events"] > 0),
        (rec["units_completed"] - rec["inspection_events"]) * 100.0 / rec["inspection_events"],
        np.nan)
    rec["qty_variance_pct"] = sql_round(raw_var, 3)
    # Flag on the UNROUNDED value, as the SQL does. Flagging on the rounded one
    # would disagree with the engine for anything sitting on the tolerance.
    rec["variance_flag"] = np.abs(np.nan_to_num(raw_var)) > tol_pct
    rec = rec.rename(columns={"units_completed": "mes_units_completed"})
    for c in ["mes_units_completed", "downtime_min"]:   # nullable ints, not 41.0
        rec[c] = rec[c].astype("Int64")
    rec = rec[["station_id", "prod_day", "shift_code", "shift_id", "work_order_no",
               "inspection_events", "mes_units_completed", "qty_variance_pct",
               "shift_present", "variance_flag", "downtime_min"]]
    rec = rec.sort_values(["station_id", "shift_id"]).reset_index(drop=True)
    return wo, rec


def build_report(ev: pd.DataFrame, erp: pd.DataFrame, mes: pd.DataFrame, qms: pd.DataFrame,
                 rec: pd.DataFrame, xw: dict) -> dict:
    matched_serials = set(ev["serial_key"].dropna())
    orphan_ncr = qms[~qms["serial_key"].isin(matched_serials)]

    linked = ev[ev["ncr_no"].notna()]
    taxonomy_conflict = linked[linked["defect_code_qms"].notna()
                               & (linked["defect_code_qms"] != linked["defect_code"])]

    # MES booked shifts to a work order on dates ERP shows it already closed.
    erp_end = erp.set_index("work_order_no")["sched_end"].to_dict()
    m = mes.dropna(subset=["work_order_no"]).copy()
    m["erp_end"] = m["work_order_no"].map(erp_end)
    booked_after_close = m[m["erp_end"].notna() & (m["prod_day"] > m["erp_end"])]

    grain_mismatch = int((ev["date"] != ev["prod_day"]).sum())
    broken = (~ev["has_work_order"]) | (~ev["has_mes_shift"]) | ev["serial_key"].isna()

    return {
        "sources": {
            "inspection_events_first_pass": int(len(ev)),
            "erp_work_orders": int(len(erp)),
            "mes_shift_records": int(len(mes)),
            "qms_ncrs": int(len(qms)),
        },
        "crosswalk_coverage": {
            "line": {"mapped": int(erp["line_id"].notna().sum()),
                     "unmapped": int(erp["line_id"].isna().sum())},
            "work_center": {"mapped": int(mes["station_id"].notna().sum()),
                            "unmapped": int(mes["station_id"].isna().sum())},
            "item_no": {"mapped": int(erp["part_number"].notna().sum()),
                        "unmapped": int(erp["part_number"].isna().sum()),
                        "unmapped_values": sorted(erp.loc[erp["part_number"].isna(),
                                                          "item_no"].unique().tolist())},
            "defect_category": {"mapped": int(qms["defect_code_qms"].notna().sum()),
                                "unmapped": int(qms["defect_code_qms"].isna().sum()),
                                "unmapped_values": sorted(
                                    qms.loc[qms["defect_code_qms"].isna(),
                                            "defect_category"].unique().tolist())},
        },
        "referential_integrity": {
            "events_without_work_order": int((~ev["has_work_order"]).sum()),
            "events_without_mes_shift": int((~ev["has_mes_shift"]).sum()),
            "events_unlinkable_no_serial": int(ev["serial_key"].isna().sum()),
            "ncrs_without_inspection_event": int(len(orphan_ncr)),
            "ncr_taxonomy_conflicts": int(len(taxonomy_conflict)),
            "mes_shifts_missing": int((~rec["shift_present"]).sum()),
            "mes_shifts_booked_to_closed_wo": int(len(booked_after_close)),
            "mes_qty_variance_shifts": int(rec["variance_flag"].sum()),
        },
        "grain_mismatch": {
            "rule": xw["shifts"]["production_day_rule"],
            "events_on_a_different_production_day": grain_mismatch,
            "pct_of_events": round(grain_mismatch / len(ev) * 100, 2),
            "note": ("joining MES to inspection data on CAST(ts AS DATE) instead of the "
                     "production day mis-assigns this share of events"),
        },
        "integrity_score": round(1.0 - int(broken.sum()) / len(ev), 4),
    }


def run() -> dict:
    xw = load_crosswalk()
    tol = float(xw["integration_rules"]["qty_variance_tolerance_pct"])

    # LAND
    events = pd.read_csv(RAW, parse_dates=["ts"])
    erp_raw = pd.read_csv(SYSDIR / "erp_work_orders.csv", dtype=str)
    mes_raw = pd.read_csv(SYSDIR / "mes_shift_log.csv")
    qms_raw = pd.read_csv(SYSDIR / "qms_ncr.csv", dtype=str)

    # VALIDATE at the boundary — the same control-plan gate the metrics use.
    clean, _dq = validate(events, load_control_plan(), persist=False)
    fp = metrics.first_pass(clean)

    # CONFORM
    erp, mes, qms = conform_erp(erp_raw, xw), conform_mes(mes_raw, xw), conform_qms(qms_raw, xw)

    # JOIN + RECONCILE
    ev = join_all(fp, erp, mes, qms)
    wo, rec = build_marts(ev, mes, tol)
    report = build_report(ev, erp, mes, qms, rec, xw)

    # Orphan register — every row that failed to join, kept for a human.
    orphans = pd.concat([
        pd.DataFrame({"kind": "event_without_work_order",
                      "ref": ev.loc[~ev["has_work_order"], "event_id"]}),
        pd.DataFrame({"kind": "event_without_mes_shift",
                      "ref": ev.loc[~ev["has_mes_shift"], "event_id"]}),
        pd.DataFrame({"kind": "event_no_serial",
                      "ref": ev.loc[ev["serial_key"].isna(), "event_id"]}),
        pd.DataFrame({"kind": "ncr_without_inspection_event",
                      "ref": qms.loc[~qms["serial_key"].isin(set(ev["serial_key"].dropna())),
                                     "ncr_no"]}),
    ], ignore_index=True)

    MARTS.mkdir(parents=True, exist_ok=True)
    QDIR.mkdir(parents=True, exist_ok=True)
    wo.to_csv(MARTS / "wo_station_day.csv", index=False)
    rec.to_csv(MARTS / "mes_shift_reconciliation.csv", index=False)
    orphans.to_csv(QDIR / "orphans.csv", index=False)
    with open(QDIR / "integration_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return {"events": ev, "wo_station_day": wo, "shift_reconciliation": rec,
            "report": report, "erp": erp, "mes": mes, "qms": qms}


def main() -> None:
    out = run()
    r = out["report"]
    print("Sources landed:")
    for k, v in r["sources"].items():
        print(f"  {k:<34} {v:>8,}")
    print("\nCrosswalk coverage:")
    for k, v in r["crosswalk_coverage"].items():
        extra = f"  unmapped={v.get('unmapped_values')}" if v.get("unmapped") else ""
        print(f"  {k:<18} mapped={v['mapped']:>6,}  unmapped={v['unmapped']:>4,}{extra}")
    print("\nReferential integrity (nothing dropped — every break is reported):")
    for k, v in r["referential_integrity"].items():
        print(f"  {k:<36} {v:>8,}")
    g = r["grain_mismatch"]
    print(f"\nGrain mismatch: {g['events_on_a_different_production_day']:,} events "
          f"({g['pct_of_events']}%) fall on a different MES production day than calendar day.")
    print(f"Referential-integrity score: {r['integrity_score']}")


if __name__ == "__main__":
    main()
