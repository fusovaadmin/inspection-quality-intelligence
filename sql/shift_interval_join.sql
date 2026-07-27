-- MES shift reconciliation: an INTERVAL join across two different grains.
--
-- The stations write one row per inspection event. MES writes one row per work
-- center per SHIFT. There is no shared key — only overlapping time. So the join
-- predicate is the interval itself:
--
--     e.ts >= s.shift_start_ts AND e.ts < s.shift_end_ts        (half-open)
--
-- Half-open matters: shift B ends at exactly 22:00 and shift C starts at exactly
-- 22:00. A BETWEEN would double-count every event landing on the boundary.
--
-- src/integrate.py takes the other route — it DERIVES the shift key from the
-- timestamp (production day + shift code) and joins on equality, which is far
-- cheaper at scale. tests/test_integration.py asserts the two agree exactly, so
-- the cheap version is provably the same answer as the honest one.
--
-- Query-tuning note: an interval join has no equality to hash on, so an engine
-- falls back toward a nested loop. Two things keep it sane here: the equality on
-- station_id/work_center carries most of the selectivity, and the derived-key
-- form in Python is the version that would actually ship at volume.
--
-- Parity: tests/test_integration.py::test_sql_interval_join_matches_python

WITH
xw_wc AS (
    SELECT * FROM (VALUES ('WC-01','S1'), ('WC-02','S2'), ('WC-03','S3'),
                          ('WC-04','S4'), ('WC-05','S5'), ('WC-06','S6'),
                          ('WC-07','S7'), ('WC-08','S8'), ('WC-09','S9'))
        AS t(work_center, station_id)
),

shifts AS (
    SELECT m.shift_id,
           x.station_id,
           m.shift_code,
           CAST(m.shift_start_ts AS TIMESTAMP)                AS shift_start_ts,
           CAST(m.shift_end_ts   AS TIMESTAMP)                AS shift_end_ts,
           -- MES stores the work order without the "WO-" prefix ERP uses.
           CASE WHEN m.wo_no IS NULL OR m.wo_no = '' THEN NULL
                ELSE 'WO-' || m.wo_no END                     AS work_order_no,
           CAST(m.units_completed AS BIGINT)                  AS mes_units_completed
    FROM mes_shift_log m
    JOIN xw_wc x ON x.work_center = m.work_center
),

ev AS (
    SELECT event_id, station_id, ts
    FROM inspection_events
    WHERE inspection_pass = 1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY ts) = 1
),

matched AS (
    SELECT s.station_id,
           s.shift_id,
           s.shift_code,
           CAST(s.shift_start_ts AS DATE)  AS prod_day,
           s.work_order_no,
           s.mes_units_completed,
           COUNT(e.event_id)               AS inspection_events
    FROM shifts s
    LEFT JOIN ev e
           ON e.station_id = s.station_id
          AND e.ts >= s.shift_start_ts     -- interval predicate, half-open
          AND e.ts <  s.shift_end_ts
    GROUP BY s.station_id, s.shift_id, s.shift_code, s.shift_start_ts,
             s.work_order_no, s.mes_units_completed
)

SELECT station_id,
       prod_day,
       shift_code,
       shift_id,
       work_order_no,
       inspection_events,
       mes_units_completed,
       CASE WHEN inspection_events > 0
            THEN ROUND((mes_units_completed - inspection_events) * 100.0
                       / inspection_events, 3) END            AS qty_variance_pct,
       -- Tolerance mirrors config/system_crosswalk.yaml (integration_rules).
       CASE WHEN inspection_events > 0
            THEN ABS((mes_units_completed - inspection_events) * 100.0
                     / inspection_events) > 5.0
            ELSE FALSE END                                    AS variance_flag
FROM matched
ORDER BY station_id, shift_id;
