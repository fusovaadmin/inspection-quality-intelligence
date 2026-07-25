-- Snowflake parity — p-chart with limits frozen on the first-21-day baseline,
-- matching src/metrics.py and sql/spc_p_chart.sql. Same answer, Snowflake engine.
WITH daily AS (
    SELECT station_id,
           CAST(ts AS DATE) AS d,
           COUNT(*)         AS n,
           SUM(CASE WHEN vision_result = 'FAIL' THEN 1 ELSE 0 END) AS fails
    FROM inspection_events
    WHERE inspection_pass = 1
    GROUP BY station_id, CAST(ts AS DATE)
),
ranked AS (
    SELECT station_id, d, n, fails,
           CAST(fails AS FLOAT) / n AS p,
           ROW_NUMBER() OVER (PARTITION BY station_id ORDER BY d) AS day_rank
    FROM daily
),
baseline AS (
    SELECT station_id, CAST(SUM(fails) AS FLOAT) / SUM(n) AS pbar
    FROM ranked
    WHERE day_rank <= 21
    GROUP BY station_id
)
SELECT r.station_id,
       r.d,
       r.n,
       r.p,
       b.pbar,
       b.pbar + 3 * SQRT(b.pbar * (1 - b.pbar) / r.n)              AS ucl,
       GREATEST(b.pbar - 3 * SQRT(b.pbar * (1 - b.pbar) / r.n), 0) AS lcl,
       (r.p > b.pbar + 3 * SQRT(b.pbar * (1 - b.pbar) / r.n)
        OR r.p < b.pbar - 3 * SQRT(b.pbar * (1 - b.pbar) / r.n))   AS out_of_control
FROM ranked r
JOIN baseline b ON r.station_id = b.station_id
ORDER BY r.station_id, r.d;
