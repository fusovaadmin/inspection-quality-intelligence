"""
Tests for the heterogeneous ERP / MES / QMS integration.

Two things are being proved here, and they are different things:

  1. The join is CORRECT — one work order per event and never two, the MES
     production day is not the calendar day, serials in two formats are the same
     unit, and every row that fails to join is reported rather than dropped.
  2. The SQL port and the Python reference return the SAME ANSWER. Both marts are
     recomputed in DuckDB (Spark-SQL-compatible) with a different join strategy —
     an interval predicate instead of a derived key — and diffed cell-for-cell.

Every asserted number comes from a deliberately injected fault in
src/generate_systems.py, so a silent change to the join shows up as a red test.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src import generate_data, generate_systems, integrate

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "sql"
RAW = ROOT / "data" / "raw" / "inspection_events.csv"
SYSDIR = ROOT / "data" / "raw" / "systems"

# Injected faults (src/generate_systems.py) — the expected findings.
EXPECTED_ORPHAN_EVENTS = 1080        # LINE-C, 2 days, 3 stations: work order closed early
EXPECTED_EVENTS_NO_SHIFT = 172       # WC-06 shift B logger outage, 3 days
EXPECTED_MISSING_SHIFTS = 3
EXPECTED_BOOKED_TO_CLOSED_WO = 18    # 3 stations x 3 shifts x 2 days
EXPECTED_ORPHAN_NCRS = 15
EXPECTED_TAXONOMY_CONFLICTS = 10
EXPECTED_QTY_VARIANCE_SHIFTS = 10


@pytest.fixture(scope="module")
def built():
    generate_data.generate()
    generate_systems.generate()
    return integrate.run()


@pytest.fixture(scope="module")
def con():
    """DuckDB standing in for Spark SQL. The three extracts are landed as RAW
    TEXT, exactly as a bronze layer would, so the query does its own casting."""
    c = duckdb.connect()
    c.execute("CREATE VIEW inspection_events AS "
              f"SELECT * FROM read_csv_auto('{RAW.as_posix()}', header=true)")
    for view, fname in [("erp_work_orders", "erp_work_orders.csv"),
                        ("mes_shift_log", "mes_shift_log.csv"),
                        ("qms_ncr", "qms_ncr.csv")]:
        c.execute(f"CREATE VIEW {view} AS SELECT * FROM read_csv("
                  f"'{(SYSDIR / fname).as_posix()}', header=true, all_varchar=true)")
    yield c
    c.close()


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    """Render every cell as a canonical string so two engines can be compared
    cell-for-cell without dtype noise (Int64 vs BIGINT, NaN vs NULL vs None)."""
    out = pd.DataFrame(index=range(len(df)))
    for c in df.columns:
        s = df[c].reset_index(drop=True)
        if pd.api.types.is_bool_dtype(s):
            out[c] = s.map(lambda v: "" if pd.isna(v) else str(bool(v)))
        elif pd.api.types.is_numeric_dtype(s):
            out[c] = s.map(lambda v: "" if pd.isna(v) else f"{float(v):.6f}")
        elif pd.api.types.is_datetime64_any_dtype(s):
            out[c] = s.map(lambda v: "" if pd.isna(v) else str(pd.Timestamp(v).date()))
        else:
            out[c] = s.map(lambda v: "" if v is None or pd.isna(v) else str(v))
    return out


# ---------------------------------------------------------------------------
# The extracts really are heterogeneous
# ---------------------------------------------------------------------------
def test_extracts_share_no_join_key_with_the_stations(built):
    """If the systems shared a key there would be nothing to demonstrate."""
    erp = pd.read_csv(SYSDIR / "erp_work_orders.csv", dtype=str)
    mes = pd.read_csv(SYSDIR / "mes_shift_log.csv")
    qms = pd.read_csv(SYSDIR / "qms_ncr.csv", dtype=str)
    events = pd.read_csv(RAW, nrows=5)

    assert not (set(erp.columns) & set(events.columns))       # ERP: UPPER_SNAKE, no overlap
    assert not (set(qms.columns) & set(events.columns))
    assert set(mes.columns) & set(events.columns) == set()    # MES: shift grain, no event key
    # Same physical things, different names:
    assert set(erp["PROD_LINE"]) == {"LN_A", "LN_B", "LN_C"}
    assert set(events["line_id"]) <= {"LINE-A", "LINE-B", "LINE-C"}
    assert mes["work_center"].str.startswith("WC-").all()
    assert qms["SERIAL_NO"].str.islower().all()               # QMS lower-cases, stations don't


def test_crosswalk_covers_every_line_and_work_center(built):
    cov = built["report"]["crosswalk_coverage"]
    assert cov["line"]["unmapped"] == 0
    assert cov["work_center"]["unmapped"] == 0


def test_unmapped_item_is_reported_not_dropped(built):
    """ERP still carries one work order on the superseded revision. An unmapped
    key is a finding for a human — it must not silently delete production."""
    cov = built["report"]["crosswalk_coverage"]["item_no"]
    assert cov["unmapped"] == 1
    assert cov["unmapped_values"] == ["1000-B"]

    erp = built["erp"]
    stale = erp[erp["part_number"].isna()]["work_order_no"].iloc[0]
    ev = built["events"]
    assert (ev["work_order_no"] == stale).sum() > 0            # its events survived the join


# ---------------------------------------------------------------------------
# ERP — the non-equi range join
# ---------------------------------------------------------------------------
def test_range_join_assigns_at_most_one_work_order_per_event(built):
    """A range join is the classic place to silently fan out. Row count in must
    equal row count out."""
    ev = built["events"]
    assert len(ev) == built["report"]["sources"]["inspection_events_first_pass"]
    assert ev["event_id"].is_unique


def test_early_closed_work_order_leaves_orphan_events(built):
    ev = built["events"]
    orphans = ev[~ev["has_work_order"]]
    assert len(orphans) == EXPECTED_ORPHAN_EVENTS
    assert set(orphans["line_id"]) == {"LINE-C"}
    assert sorted({str(pd.Timestamp(d).date()) for d in orphans["date"].unique()}) == \
        ["2026-04-27", "2026-04-28"]


def test_events_inside_a_work_order_window_are_all_matched(built):
    ev = built["events"]
    matched = ev[ev["has_work_order"]]
    assert matched["work_order_no"].str.startswith("WO-").all()
    # Every matched event's date really is inside its work order's window.
    erp = built["erp"].set_index("work_order_no")
    starts = matched["work_order_no"].map(erp["sched_start"])
    ends = matched["work_order_no"].map(erp["sched_end"])
    assert (matched["date"] >= starts).all()
    assert (matched["date"] <= ends).all()


# ---------------------------------------------------------------------------
# MES — different grain, and a production day that is not the calendar day
# ---------------------------------------------------------------------------
def test_production_day_is_not_the_calendar_day():
    """Shift C runs 22:00 -> 06:00. An event at 02:00 belongs to YESTERDAY's
    production day — the single most expensive assumption in this join."""
    ts = pd.Series(pd.to_datetime(["2026-04-10 02:00:00", "2026-04-10 07:00:00",
                                   "2026-04-10 15:00:00", "2026-04-10 23:30:00"]))
    assert list(integrate.production_day(ts).dt.date.astype(str)) == [
        "2026-04-09", "2026-04-10", "2026-04-10", "2026-04-10"]
    assert list(integrate.shift_code(ts)) == ["C", "A", "B", "C"]


def test_grain_mismatch_is_quantified(built):
    g = built["report"]["grain_mismatch"]
    # Events run around the clock, so the 00:00-06:00 slice is ~a quarter of them.
    assert 20.0 < g["pct_of_events"] < 30.0
    assert g["events_on_a_different_production_day"] > 20_000


def test_mes_outage_is_visible_from_the_production_side(built):
    rec = built["shift_reconciliation"]
    missing = rec[~rec["shift_present"]]
    assert len(missing) == EXPECTED_MISSING_SHIFTS
    assert set(missing["station_id"]) == {"S6"}
    assert set(missing["shift_code"]) == {"B"}
    assert missing["inspection_events"].gt(0).all()   # production happened; MES lost it
    assert built["report"]["referential_integrity"]["events_without_mes_shift"] == \
        EXPECTED_EVENTS_NO_SHIFT


def test_mes_booked_shifts_to_a_work_order_erp_had_closed(built):
    """A cross-system contradiction neither system can see on its own."""
    assert built["report"]["referential_integrity"]["mes_shifts_booked_to_closed_wo"] == \
        EXPECTED_BOOKED_TO_CLOSED_WO


def test_quantity_variance_flags_only_the_keying_errors(built):
    rec = built["shift_reconciliation"]
    flagged = rec[rec["variance_flag"]]
    assert len(flagged) == EXPECTED_QTY_VARIANCE_SHIFTS
    # Everything else agrees to within the tolerance in the crosswalk config.
    ok = rec[rec["shift_present"] & ~rec["variance_flag"] & (rec["inspection_events"] > 0)]
    assert ok["qty_variance_pct"].abs().max() <= 5.0


# ---------------------------------------------------------------------------
# QMS — normalized-key join on two serial formats
# ---------------------------------------------------------------------------
def test_serial_canonicalization_bridges_two_formats():
    s = pd.Series(["SN-S3-00-0142", "s3-00-0142", "sn-s5-12-0007"])
    assert list(integrate.canonical_serial(s)) == [
        "S3-00-0142", "S3-00-0142", "S5-12-0007"]


def test_ncrs_link_to_inspection_events(built):
    ev, qms = built["events"], built["qms"]
    linked = ev["ncr_no"].notna().sum()
    assert linked == len(qms) - EXPECTED_ORPHAN_NCRS
    assert built["report"]["referential_integrity"]["ncrs_without_inspection_event"] == \
        EXPECTED_ORPHAN_NCRS


def test_taxonomy_conflicts_between_qms_and_the_stations(built):
    assert built["report"]["referential_integrity"]["ncr_taxonomy_conflicts"] == \
        EXPECTED_TAXONOMY_CONFLICTS


def test_nothing_that_fails_to_join_is_silently_dropped(built):
    ri = built["report"]["referential_integrity"]
    orphans = pd.read_csv(ROOT / "data" / "quality" / "orphans.csv")
    assert len(orphans) == (ri["events_without_work_order"]
                            + ri["events_without_mes_shift"]
                            + ri["events_unlinkable_no_serial"]
                            + ri["ncrs_without_inspection_event"])
    assert 0.90 < built["report"]["integrity_score"] < 1.0


# ---------------------------------------------------------------------------
# SQL parity — same answer, different engine, different join strategy
# ---------------------------------------------------------------------------
def test_sql_heterogeneous_join_matches_python(built, con):
    sql = (SQL / "heterogeneous_join.sql").read_text(encoding="utf-8")
    got = con.execute(sql).df()
    want = built["wo_station_day"]
    assert list(got.columns) == list(want.columns)
    pd.testing.assert_frame_equal(_norm(got), _norm(want))


def test_sql_interval_join_matches_python(built, con):
    """The SQL uses a true interval predicate; Python derives the shift key and
    joins on equality. Same answer — which is what licenses the cheap version.

    It also shows the blind spot: driven from the MES side, a shift MES never
    logged cannot appear. Those are exactly the rows the production-side join
    keeps, and they are the outage."""
    sql = (SQL / "shift_interval_join.sql").read_text(encoding="utf-8")
    got = con.execute(sql).df()
    want = built["shift_reconciliation"]

    present = want[want["shift_present"]].reset_index(drop=True)
    cols = ["station_id", "prod_day", "shift_code", "shift_id", "work_order_no",
            "inspection_events", "mes_units_completed", "qty_variance_pct",
            "variance_flag"]
    pd.testing.assert_frame_equal(_norm(got[cols]), _norm(present[cols]))

    # The MES-driven join structurally cannot see the outage.
    assert len(want) - len(got) == EXPECTED_MISSING_SHIFTS
