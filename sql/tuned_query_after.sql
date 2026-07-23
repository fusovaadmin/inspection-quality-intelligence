-- AFTER (fast): identical result, ONE pass over the data. The per-station average
-- is computed as a WINDOW AGGREGATE alongside the daily rollup, so
-- inspection_events is scanned once instead of once-per-row.
-- Correlated subquery  O(rows * table)   ->   windowed single scan  O(rows).
-- Verify equivalence: the two result sets are identical (see tests / README).
WITH daily AS (
    SELECT station_id,
           CAST(ts AS DATE) AS d,
           COUNT(*)         AS n,
           SUM(CASE WHEN vision_result = 'FAIL' THEN 1 ELSE 0 END) AS fails
    FROM inspection_events
    WHERE inspection_pass = 1
    GROUP BY station_id, CAST(ts AS DATE)
)
SELECT station_id,
       d,
       fails * 1.0 / n AS p,
       SUM(fails) OVER (PARTITION BY station_id) * 1.0
           / SUM(n) OVER (PARTITION BY station_id) AS station_avg_defect_rate
FROM daily
ORDER BY station_id, d;
