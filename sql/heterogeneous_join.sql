-- Heterogeneous system join: inspection stations + ERP + QMS -> one daily fact.
--
-- Skills shown: a non-equi RANGE join (ERP work orders cover a date span, not a
-- date), key crosswalks as versioned reference data, a normalized-key join across
-- two different serial-number formats, de-duplication with QUALIFY, and two
-- WINDOW functions computed over the joined result.
--
-- The three extracts are landed as RAW TEXT — a bronze landing zone — so the
-- casting happens here, in the open, which is where dialect differences actually
-- live. Two lines below are DuckDB-specific; the Databricks/Spark form is marked
-- inline. Everything else is portable.
--
-- Parity: tests/test_integration.py::test_sql_heterogeneous_join_matches_python
-- asserts this returns data/marts/wo_station_day.csv cell-for-cell.

WITH
-- ---------------------------------------------------------------- crosswalks
-- Versioned in config/system_crosswalk.yaml; inlined here so the query is
-- self-contained. The parity test is what keeps the two copies honest.
xw_line AS (
    SELECT * FROM (VALUES ('LN_A', 'LINE-A'), ('LN_B', 'LINE-B'), ('LN_C', 'LINE-C'))
        AS t(erp_line, line_id)
),

-- ------------------------------------------------------------- conform: ERP
erp AS (
    SELECT w.WORK_ORDER_NO                        AS work_order_no,
           w.ITEM_NO                              AS item_no,
           x.line_id                              AS line_id,
           -- DuckDB: strptime.  Databricks/Spark: to_date(w.SCHED_START_DT, 'MM/dd/yyyy')
           CAST(strptime(w.SCHED_START_DT, '%m/%d/%Y') AS DATE) AS sched_start,
           CAST(strptime(w.SCHED_END_DT,   '%m/%d/%Y') AS DATE) AS sched_end
    FROM erp_work_orders w
    JOIN xw_line x ON x.erp_line = w.PROD_LINE
),

-- ------------------------------------------------------------- conform: QMS
-- QMS stores serials lower-cased with the "SN-" prefix stripped; the stations
-- write SN-S3-00-0142. Neither is wrong; they just have to meet in the middle.
qms AS (
    SELECT NCR_NO                                        AS ncr_no,
           regexp_replace(UPPER(SERIAL_NO), '^SN-', '')   AS serial_key,
           DISPOSITION                                   AS disposition
    FROM qms_ncr
),

-- ------------------------------------------ conform: inspection events (gate)
-- Same gate the metrics run behind: drop duplicate event_ids, first pass only.
ev AS (
    SELECT event_id,
           line_id,
           station_id,
           CAST(ts AS DATE)                                       AS d,
           vision_result,
           regexp_replace(UPPER(serial_number), '^SN-', '')       AS serial_key
    FROM inspection_events
    WHERE inspection_pass = 1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY ts) = 1
),

-- ------------------------------------------------------------- the join itself
-- ERP is a RANGE join: the work order covers [sched_start, sched_end] on a line.
-- An event outside every window keeps its row with a NULL work order — that is
-- how a work order closed two days early becomes a visible finding instead of a
-- silently wrong number. QMS is a sparse left join on the normalized serial.
joined AS (
    SELECT e.line_id,
           e.station_id,
           e.d,
           w.work_order_no,
           w.item_no,
           e.event_id,
           e.vision_result,
           q.ncr_no,
           q.disposition
    FROM ev e
    LEFT JOIN erp w
           ON w.line_id = e.line_id
          AND e.d >= w.sched_start
          AND e.d <= w.sched_end        -- non-equi range predicate
    LEFT JOIN qms q
           ON q.serial_key = e.serial_key
),

daily AS (
    SELECT line_id,
           station_id,
           d,
           work_order_no,
           item_no,
           COUNT(*)                                                   AS n_first_pass,
           SUM(CASE WHEN vision_result = 'FAIL'  THEN 1 ELSE 0 END)    AS n_fail,
           COUNT(ncr_no)                                              AS ncr_count,
           SUM(CASE WHEN disposition = 'SCRAP'   THEN 1 ELSE 0 END)    AS ncr_scrap
    FROM joined
    GROUP BY line_id, station_id, d, work_order_no, item_no
)

-- ------------------------------------------------------ window over the join
-- How far into this work order the station is, and units inspected against it
-- to date. Rows with no work order get NULL rather than a phantom partition.
SELECT line_id,
       station_id,
       d                                          AS date,
       work_order_no,
       item_no,
       n_first_pass,
       n_fail,
       ncr_count,
       ncr_scrap,
       work_order_no IS NOT NULL                  AS has_work_order,
       CASE WHEN work_order_no IS NULL THEN NULL ELSE
           ROW_NUMBER() OVER (PARTITION BY work_order_no, station_id ORDER BY d)
       END                                        AS wo_day_seq,
       CASE WHEN work_order_no IS NULL THEN NULL ELSE
           SUM(n_first_pass) OVER (PARTITION BY work_order_no, station_id ORDER BY d
                                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
       END                                        AS wo_cum_units
FROM daily
ORDER BY station_id, d;
