# SQL — window functions + a real before/after tuning

These are written for **Databricks / Spark SQL** and also run in **DuckDB** (which
is how `tests/test_sql.py` exercises them in CI).

| File | Skill demonstrated |
|------|--------------------|
| `rolling_fpy.sql` | Trailing **window function** — `AVG(fpy) OVER (PARTITION BY station ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)` over a two-level aggregation |
| `spc_p_chart.sql` | Window aggregate for the per-station center line; computed **3-sigma control limits** with variable subgroup size; rule-1 flagging |
| `tuned_query_before.sql` | The **slow** version (correlated subquery) |
| `tuned_query_after.sql` | The **fast** version (window aggregate, single pass) |

## The tuning story (before → after)

**Before.** The per-station average defect rate is attached to every daily row
with a **correlated subquery**:

```sql
(SELECT SUM(CASE WHEN e.vision_result='FAIL' THEN 1 ELSE 0 END)*1.0 / COUNT(*)
 FROM inspection_events e
 WHERE e.inspection_pass = 1 AND e.station_id = d.station_id)
```

That subquery re-scans `inspection_events` **once for every output row**. On a real
MES feed it's the query that runs fine on 10k rows in dev and falls over at 10M in
production — roughly **O(rows × table)**.

**After.** Compute the same per-station average as a **window aggregate** in the
same pass as the daily rollup:

```sql
SUM(fails) OVER (PARTITION BY station_id) * 1.0 / SUM(n) OVER (PARTITION BY station_id)
```

`inspection_events` is now scanned **once** — roughly **O(rows)**. Same answer,
far less work.

## Why it's provably the same answer

`tests/test_sql.py::test_tuned_query_matches_before` runs both queries over the
generated dataset and asserts the two result sets are **identical** (`assert_frame_equal`).
The optimization changes the *plan*, not the *output*.

To see the plan difference yourself, prefix either query with `EXPLAIN` (Spark SQL)
or `EXPLAIN ANALYZE` (DuckDB): the *before* plan shows the repeated scan / nested
dependency; the *after* plan shows a single scan with a window operator.

> Interview note: the honest calibration is "capable applied tuner, not an
> execution-plan specialist." This is exactly that — recognizing a re-scan
> anti-pattern and replacing it with a windowed single pass, then proving
> equivalence with a test.
