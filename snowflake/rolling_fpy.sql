-- Snowflake parity — IDENTICAL logic to sql/rolling_fpy.sql.
-- The whole point: the model is portable. The same window-function SQL runs on
-- Databricks, DuckDB, and Snowflake because I model the data, not the engine.
WITH first_pass AS (
    SELECT station_id,
           CAST(ts AS DATE) AS d,
           CASE WHEN vision_result = 'FAIL' THEN 1 ELSE 0 END AS is_fail
    FROM inspection_events
    WHERE inspection_pass = 1
),
daily AS (
    SELECT station_id,
           d,
           COUNT(*)                          AS n,
           SUM(is_fail)                       AS fails,
           1 - CAST(SUM(is_fail) AS FLOAT) / COUNT(*) AS fpy
    FROM first_pass
    GROUP BY station_id, d
)
SELECT station_id,
       d,
       n,
       fails,
       fpy,
       AVG(fpy) OVER (
           PARTITION BY station_id
           ORDER BY d
           ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
       ) AS rolling_fpy_7d
FROM daily
ORDER BY station_id, d;
