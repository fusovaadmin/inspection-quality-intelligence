"""
Proves the SQL artifacts are correct, not just plausible:
  * the tuned 'after' query returns results identical to the 'before' query
  * the window-function and p-chart queries run and produce the right columns

Uses DuckDB as a local Spark-SQL-compatible engine over the generated CSV, so the
same SQL that runs on Databricks is exercised in CI.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src import generate_data

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "sql"
RAW = ROOT / "data" / "raw" / "inspection_events.csv"


@pytest.fixture(scope="module")
def con():
    generate_data.generate()  # ensure a fresh, deterministic dataset exists
    c = duckdb.connect()
    c.execute(
        f"CREATE VIEW inspection_events AS "
        f"SELECT * FROM read_csv_auto('{RAW.as_posix()}', header=true)"
    )
    yield c
    c.close()


def _run(con, name: str) -> pd.DataFrame:
    return con.execute((SQL / name).read_text(encoding="utf-8")).df()


def test_tuned_query_matches_before(con):
    before = _run(con, "tuned_query_before.sql")
    after = _run(con, "tuned_query_after.sql")
    keys = ["station_id", "d"]
    b = before.sort_values(keys).reset_index(drop=True).round(9)
    a = after.sort_values(keys).reset_index(drop=True).round(9)
    # identical result set — the optimization changes speed, not answers
    pd.testing.assert_frame_equal(a[b.columns], b)


def test_rolling_fpy_query_runs(con):
    r = _run(con, "rolling_fpy.sql")
    assert {"station_id", "d", "fpy", "rolling_fpy_7d"} <= set(r.columns)
    assert len(r) > 0
    assert r["rolling_fpy_7d"].between(0, 1).all()


def test_p_chart_query_flags_anomalies(con):
    p = _run(con, "spc_p_chart.sql")
    assert {"ucl", "lcl", "out_of_control"} <= set(p.columns)
    assert bool(p["out_of_control"].any())   # S3/S5 special causes must surface
