"""
Synthetic ERP / MES / QMS extracts — three source systems that disagree.

The inspection stations are only one of the systems a manufacturing-quality
platform has to pull from. This module emits the other three, each shaped like a
real export from its own system and each deliberately *incompatible* with the
inspection feed in a different way:

  * ERP  (work orders)   — UPPER_SNAKE columns, MM/DD/YYYY dates, no timestamps.
                           Keyed on ITEM_NO ("1000-C"), not part_number ("PN-1000").
                           Covers a date RANGE, so the join is non-equi.
  * MES  (shift log)     — different GRAIN: one row per work center per shift,
                           not per event. Work centers are "WC-03", not "S3".
                           Shift C crosses midnight, so the MES production day
                           is not the calendar day. Work-order numbers are stored
                           without the "WO-" prefix.
  * QMS  (NCRs)          — sparse (only some failures raise one). Serials are
                           lower-cased with the "SN-" prefix stripped. Defect
                           categories are a human taxonomy ("SOLDER"), not the
                           machine codes the stations emit ("SLD-BRDG").

Faults are injected on purpose so the referential-integrity checks in
src/integrate.py have real breaks to catch:

  1. ERP work order WO-100012 (LINE-C) is CLOSED two days early -> ~1k inspection
     events produced against no open work order.
  2. MES logger outage on WC-06, shift B, three consecutive days -> inspection
     events with no shift context.
  3. MES keeps booking shifts to the work order ERP already closed -> a direct
     cross-system contradiction.
  4. Hand-keyed digit transpositions in MES units_completed -> quantity variance.
  5. QMS NCRs against serials that never existed -> orphan NCRs.
  6. QMS defect categories that contradict the station's defect code.
  7. ERP work orders still on the superseded revision 1000-B -> unmapped item key.

Deterministic: fixed seed, and every value is derived from the (already seeded)
inspection dataset, so the extracts reproduce byte-for-byte on every run.

Run:  python -m src.generate_data && python -m src.generate_systems
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "inspection_events.csv"
SYSDIR = ROOT / "data" / "raw" / "systems"

SEED = 4242
WO_BLOCK_DAYS = 7          # a work order covers one week of one line
QMS_NCR_RATE = 0.12        # fraction of first-pass failures that raise an NCR

# --- injected faults (tests assert against these) ---------------------------
ERP_GAP_LINE = "LINE-C"
ERP_GAP_DAYS = (26, 27)            # day offsets left uncovered by any open work order
ERP_STALE_REV_WO_INDEX = 3         # this work order stays on superseded rev 1000-B
MES_OUTAGE_WC = "WC-06"
MES_OUTAGE_SHIFT = "B"
MES_OUTAGE_DAYS = (33, 34, 35)     # day offsets with no MES record at all
MES_TRANSPOSED_SHIFTS = 10         # hand-keyed digit transpositions in units_completed
QMS_ORPHAN_NCRS = 15               # NCRs against serials with no inspection record
QMS_TAXONOMY_CONFLICTS = 10        # NCR category contradicts the station's defect code
QMS_UNMAPPED_CATEGORY = 5          # category not in the crosswalk at all

LINE_TO_ERP = {"LINE-A": "LN_A", "LINE-B": "LN_B", "LINE-C": "LN_C"}
STATION_TO_WC = {f"S{i}": f"WC-{i:02d}" for i in range(1, 10)}
CODE_TO_CATEGORY = {"SLD-BRDG": "SOLDER", "CMP-MISS": "MISSING COMPONENT",
                    "SRF-SCR": "SURFACE FINISH", "DIM-OOS": "DIMENSIONAL",
                    "LBL-ERR": "LABELING"}
SHIFTS = [("A", 6), ("B", 14), ("C", 22)]   # code, start hour (8h each)


def _production_day(ts: pd.Series) -> pd.Series:
    """The MES production day: the calendar date the shift STARTED on.
    Shift C runs 22:00 -> 06:00, so anything before 06:00 belongs to yesterday."""
    return (ts - pd.Timedelta(hours=6)).dt.floor("D")


def _shift_code(ts: pd.Series) -> pd.Series:
    h = ts.dt.hour
    return np.where(h < 6, "C", np.where(h < 14, "A", np.where(h < 22, "B", "C")))


# ---------------------------------------------------------------------------
# ERP — work orders
# ---------------------------------------------------------------------------
def build_erp(fp: pd.DataFrame) -> pd.DataFrame:
    day0 = fp["ts"].dt.floor("D").min()
    last_day = fp["ts"].dt.floor("D").max()
    n_days = int((last_day - day0).days) + 1

    blocks = [(s, min(s + WO_BLOCK_DAYS - 1, n_days - 1))
              for s in range(0, n_days, WO_BLOCK_DAYS)]

    rows, wo_seq = [], 0
    for line in sorted(fp["line_id"].unique()):
        for bi, (s_off, e_off) in enumerate(blocks):
            wo_seq += 1
            start = day0 + pd.Timedelta(days=s_off)
            end = day0 + pd.Timedelta(days=e_off)
            status = "CLOSED"

            # Fault 1: this work order is closed two days early, but the line
            # kept running. Those units have no open work order in ERP.
            if line == ERP_GAP_LINE and s_off <= ERP_GAP_DAYS[0] <= e_off:
                end = day0 + pd.Timedelta(days=ERP_GAP_DAYS[0] - 1)

            planned = fp[(fp.line_id == line)
                         & (fp.ts >= start)
                         & (fp.ts < end + pd.Timedelta(days=1))]
            # Fault 7: ERP still carries the superseded revision on one work order.
            item = "1000-B" if wo_seq == ERP_STALE_REV_WO_INDEX else "1000-C"

            rows.append({
                "WORK_ORDER_NO": f"WO-{100000 + wo_seq}",
                "ITEM_NO": item,
                "ITEM_REV": item.split("-")[1],
                "PROD_LINE": LINE_TO_ERP[line],
                "PLANT_CD": "ATL1",
                "WO_STATUS": status,
                "SCHED_START_DT": start.strftime("%m/%d/%Y"),
                "SCHED_END_DT": end.strftime("%m/%d/%Y"),
                "QTY_ORDERED": int(round(len(planned), -1)),
                "UOM": "EA",
                "PLANNER_CD": f"PL{(bi % 3) + 1:02d}",
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# MES — per-shift production log
# ---------------------------------------------------------------------------
def build_mes(fp: pd.DataFrame, erp: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    ev = fp.assign(prod_day=_production_day(fp["ts"]), shift_code=_shift_code(fp["ts"]))
    counts = (ev.groupby(["station_id", "prod_day", "shift_code"])
                .agg(n=("event_id", "size"))
                .reset_index())

    # ERP work order in force for a line on a given calendar date (MES books to
    # a work order number regardless of whether ERP still has it open).
    erp_span = erp.copy()
    erp_span["line_id"] = erp_span["PROD_LINE"].map({v: k for k, v in LINE_TO_ERP.items()})
    erp_span["start"] = pd.to_datetime(erp_span["SCHED_START_DT"], format="%m/%d/%Y")
    # MES books to the work order it was told to run — the *scheduled block*, not
    # the possibly-early ERP close date. That is what creates fault 3.
    erp_span["block_end"] = erp_span["start"] + pd.Timedelta(days=WO_BLOCK_DAYS - 1)

    def _wo_for(line: str, day: pd.Timestamp) -> str | None:
        m = erp_span[(erp_span.line_id == line) & (erp_span.start <= day)
                     & (erp_span.block_end >= day)]
        return m["WORK_ORDER_NO"].iloc[0] if len(m) else None

    line_of = fp.groupby("station_id")["line_id"].first().to_dict()
    start_hour = dict(SHIFTS)

    rows = []
    for r in counts.itertuples():
        # Fault 2: MES logger outage — no record written at all.
        day_off = int((r.prod_day - fp["ts"].dt.floor("D").min()).days)
        wc = STATION_TO_WC[r.station_id]
        if wc == MES_OUTAGE_WC and r.shift_code == MES_OUTAGE_SHIFT and day_off in MES_OUTAGE_DAYS:
            continue

        s_ts = r.prod_day + pd.Timedelta(hours=start_hour[r.shift_code])
        e_ts = s_ts + pd.Timedelta(hours=8)
        line = line_of[r.station_id]
        wo = _wo_for(line, r.prod_day)
        rows.append({
            "shift_id": f"{r.prod_day.date().isoformat()}-{r.shift_code}",
            "work_center": wc,
            "shift_code": r.shift_code,
            "shift_start_ts": s_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "shift_end_ts": e_ts.strftime("%Y-%m-%d %H:%M:%S"),
            # Work-order number WITHOUT the "WO-" prefix — MES stores it numeric.
            "wo_no": wo.replace("WO-", "") if wo else "",
            "units_started": int(r.n + rng.integers(0, 3)),
            # MES counts what the operator dispositioned; the stations count
            # inspection events. On a full shift they agree to within a unit or
            # two — small, honest variance that stays inside tolerance.
            "units_completed": int(r.n + (rng.integers(-1, 2) if r.n >= 40 else 0)),
            "downtime_min": int(rng.integers(0, 46)),
            "operator_badge": f"OP-{1000 + int(rng.integers(0, 40)):04d}",
        })

    mes = pd.DataFrame(rows).sort_values(["shift_id", "work_center"]).reset_index(drop=True)

    # Fault 4: hand-keyed digit transpositions in units_completed (63 -> 36).
    # Only applied where the transposition actually moves the number outside
    # tolerance — a palindrome (66 -> 66) is a typo nobody can detect, and the
    # demo should not claim to catch what it cannot.
    applied = 0
    for i in rng.permutation(len(mes)):
        if applied >= MES_TRANSPOSED_SHIFTS:
            break
        v = int(mes.at[i, "units_completed"])
        t = int(str(v)[::-1].lstrip("0") or "0")
        if v and abs(t - v) / v * 100 > 10.0:   # margin over the 5% tolerance
            mes.at[i, "units_completed"] = t
            applied += 1
    return mes


# ---------------------------------------------------------------------------
# QMS — nonconformance reports
# ---------------------------------------------------------------------------
def build_qms(fp: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    fails = fp[(fp.vision_result == "FAIL") & fp.defect_code.notna()
               & fp.serial_number.notna()].copy()
    ncr = fails.sample(frac=QMS_NCR_RATE, random_state=SEED).sort_values("ts").reset_index(drop=True)

    sev = np.where(ncr.defect_code == "DIM-OOS", "MAJOR",
                   np.where(rng.random(len(ncr)) < 0.08, "MAJOR", "MINOR"))
    disp = np.where(sev == "MAJOR",
                    rng.choice(["REWORK", "SCRAP"], size=len(ncr), p=[0.65, 0.35]),
                    rng.choice(["REWORK", "USE AS IS"], size=len(ncr), p=[0.8, 0.2]))
    opened = ncr["ts"].dt.floor("D")
    closed = opened + pd.to_timedelta(rng.integers(1, 21, len(ncr)), unit="D")

    out = pd.DataFrame({
        "NCR_NO": [f"NCR-2026-{i + 1:04d}" for i in range(len(ncr))],
        # Serial as QMS stores it: lower-cased, "SN-" prefix stripped.
        "SERIAL_NO": ncr["serial_number"].str.replace("^SN-", "", regex=True).str.lower(),
        "DEFECT_CATEGORY": ncr["defect_code"].map(CODE_TO_CATEGORY),
        "SEVERITY": sev,
        "DISPOSITION": disp,
        "OPENED_DT": opened.dt.date.astype(str),
        "CLOSED_DT": closed.dt.date.astype(str),
        "CAPA_NO": np.where(sev == "MAJOR",
                            [f"CAPA-{2000 + i}" for i in range(len(ncr))], ""),
        "ORIGINATOR": [f"QE-{100 + int(v)}" for v in rng.integers(0, 12, len(ncr))],
    })

    # Disjoint index slices so the three injected QMS faults never overlap.
    pool = rng.permutation(len(out))
    a = pool[:QMS_ORPHAN_NCRS]
    b = pool[QMS_ORPHAN_NCRS:QMS_ORPHAN_NCRS + QMS_TAXONOMY_CONFLICTS]
    c = pool[QMS_ORPHAN_NCRS + QMS_TAXONOMY_CONFLICTS:
             QMS_ORPHAN_NCRS + QMS_TAXONOMY_CONFLICTS + QMS_UNMAPPED_CATEGORY]

    # Fault 5: serial transposed on entry — no such unit was ever inspected.
    out.loc[a, "SERIAL_NO"] = out.loc[a, "SERIAL_NO"].str.replace(
        r"-\d{4}$", "-9999", regex=True)

    # Fault 6: the QMS category contradicts the code the station recorded.
    cats = list(CODE_TO_CATEGORY.values())
    for j, i in enumerate(b):
        cur = out.at[i, "DEFECT_CATEGORY"]
        out.at[i, "DEFECT_CATEGORY"] = [c_ for c_ in cats if c_ != cur][j % (len(cats) - 1)]

    # Fault 7 (QMS side): a category that is not in the crosswalk at all.
    out.loc[c, "DEFECT_CATEGORY"] = "OTHER"
    return out


def generate() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    df = pd.read_csv(RAW, parse_dates=["ts"])
    df = df.drop_duplicates(subset=["event_id"], keep="first")
    fp = df[df.inspection_pass == 1].copy()

    erp = build_erp(fp)
    mes = build_mes(fp, erp, rng)
    qms = build_qms(fp, rng)

    SYSDIR.mkdir(parents=True, exist_ok=True)
    erp.to_csv(SYSDIR / "erp_work_orders.csv", index=False)
    mes.to_csv(SYSDIR / "mes_shift_log.csv", index=False)
    qms.to_csv(SYSDIR / "qms_ncr.csv", index=False)
    return {"erp": erp, "mes": mes, "qms": qms}


def main() -> None:
    out = generate()
    print(f"Wrote three source-system extracts -> {SYSDIR}")
    for name, df in out.items():
        print(f"  {name:<4} {len(df):>6,} rows   cols: {list(df.columns)[:4]} ...")


if __name__ == "__main__":
    main()
