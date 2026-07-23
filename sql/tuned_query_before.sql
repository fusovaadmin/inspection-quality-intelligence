-- BEFORE (slow): the per-station average defect rate is attached to every daily
-- row with a CORRELATED SUBQUERY. That subquery re-scans inspection_events once
-- for every output row -> roughly O(rows * table). This is the query that "works
-- fine on 10k rows in dev and falls over at 10M rows on the real MES feed."
-- See sql/README.md for the before/after write-up.
SELECT d.station_id,
       d.d,
       d.p,
       (SELECT SUM(CASE WHEN e.vision_result = 'FAIL' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
        FROM inspection_events e
        WHERE e.inspection_pass = 1
          AND e.station_id = d.station_id) AS station_avg_defect_rate
FROM (
    SELECT station_id,
           CAST(ts AS DATE) AS d,
           AVG(CASE WHEN vision_result = 'FAIL' THEN 1.0 ELSE 0.0 END) AS p
    FROM inspection_events
    WHERE inspection_pass = 1
    GROUP BY station_id, CAST(ts AS DATE)
) d
ORDER BY d.station_id, d.d;
