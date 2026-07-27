# SQL — window functions + a real before/after tuning

These are written for **Databricks / Spark SQL** and also run in **DuckDB** (which
is how `tests/test_sql.py` exercises them in CI).

| File | Skill demonstrated |
|------|--------------------|
| `rolling_fpy.sql` | Trailing **window function** — `AVG(fpy) OVER (PARTITION BY station ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)` over a two-level aggregation |
| `spc_p_chart.sql` | Window aggregate for the per-station center line; computed **3-sigma control limits** with variable subgroup size; rule-1 flagging |
| `heterogeneous_join.sql` | **Three systems in one query** — a non-equi **range join** to ERP work orders, a **normalized-key join** to QMS serials, crosswalks as inline reference data, `QUALIFY` de-duplication, and two **window functions over the joined result** |
| `shift_interval_join.sql` | **Interval join** across two grains — `ts >= shift_start AND ts < shift_end`, half-open so the 22:00 shift boundary is not double-counted |
| `tuned_query_before.sql` | The **slow** version (correlated subquery) |
| `tuned_query_after.sql` | The **fast** version (window aggregate, single pass) |

## The heterogeneous join (what makes it hard)

Four systems, four grains, no shared key:

| System | Grain | Key it uses | Join strategy |
|--------|-------|-------------|---------------|
| Vision stations | one row per **event** | `station_id`, `SN-S3-00-0142` | — (the spine) |
| ERP | one row per **work order**, covering a date **range** | `PROD_LINE` = `LN_C`, `ITEM_NO` = `1000-C` | **non-equi range join** on line + date within `[start, end]` |
| MES | one row per work center per **shift** | `work_center` = `WC-03`, `wo_no` = `100019` | **interval join** on `[shift_start, shift_end)` |
| QMS | one row per **NCR** (sparse) | `serial_no` = `s3-00-0142` | **normalized-key join** after canonicalizing the serial |

Three things that are easy to get wrong, and what they cost:

1. **The production day is not the calendar day.** Shift C runs 22:00 → 06:00, so
   everything it makes belongs to the day it *started*. Joining MES on
   `CAST(ts AS DATE)` silently mis-assigns **24.9%** of events. That is not a
   crash — it is a dashboard that is quietly wrong for a quarter of the night.
2. **Join direction decides what you can see.** `shift_interval_join.sql` is driven
   from the MES side, so a shift MES never logged **cannot appear in the result** —
   the 3-shift logger outage is structurally invisible to it. `src/integrate.py`
   drives from what was actually produced and keeps those rows. The parity test
   asserts both halves: identical where MES has a record, and exactly 3 rows that
   only the production-side join can see.
3. **A range join is where row counts silently fan out.** Work orders are disjoint
   per line, so at most one matches — and `tests/test_integration.py` asserts the
   row count in equals the row count out rather than trusting that.

**Cost note.** An interval join has no equality to hash on, so an engine drifts
toward a nested loop. The equality on `station_id` carries most of the
selectivity here; at real volume the version that ships is the one in
`src/integrate.py`, which derives the shift key from the timestamp and joins on
equality. The SQL is the proof that the cheap form gives the same answer.

### One cross-engine divergence worth knowing about

pandas rounds half to **even**; DuckDB, Spark and Snowflake round half **away from
zero**. `1.5625` → `1.562` in pandas, `1.563` in every engine it gets ported to.
It only appears on exact ties, which is precisely how it survives review and then
gets dismissed as a flaky test. `src/integrate.py::sql_round` matches the engines
instead, and the association order in the Python (`diff * 100 / n`) is written to
match the SQL so the two agree to the last bit.

> Honest note on the crosswalks: they live in `config/system_crosswalk.yaml` and
> are *also* inlined as `VALUES` CTEs in the SQL, so there are two copies. The
> parity tests are what keep them honest — if one drifts, the cell-for-cell diff
> against the Python mart goes red.

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
