-- p-chart control limits with variable subgroup size, then flag rule-1 points.
-- Skills shown: two-level aggregation, a window aggregate for the per-station
-- center line (p-bar), and a computed 3-sigma limit that varies with subgroup n.
-- Written for Databricks / Spark SQL (also runs in DuckDB).
WITH daily AS (
    SELECT station_id,
           CAST(ts AS DATE) AS d,
           COUNT(*)         AS n,
           SUM(CASE WHEN vision_result = 'FAIL' THEN 1 ELSE 0 END) AS fails
    FROM inspection_events
    WHERE inspection_pass = 1
    GROUP BY station_id, CAST(ts AS DATE)
),
with_center AS (
    SELECT d,
           station_id,
           n,
           fails,
           fails * 1.0 / n AS p,
           SUM(fails) OVER (PARTITION BY station_id) * 1.0
               / SUM(n) OVER (PARTITION BY station_id) AS pbar
    FROM daily
)
SELECT d,
       station_id,
       n,
       p,
       pbar,
       pbar + 3 * SQRT(pbar * (1 - pbar) / n)              AS ucl,
       GREATEST(pbar - 3 * SQRT(pbar * (1 - pbar) / n), 0) AS lcl,
       (p > pbar + 3 * SQRT(pbar * (1 - pbar) / n)
        OR p < pbar - 3 * SQRT(pbar * (1 - pbar) / n))     AS out_of_control
FROM with_center
ORDER BY station_id, d;
