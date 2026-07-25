-- Snowflake setup + load. Run in a Snowsight worksheet on the free trial
-- (30 days, $400 credits, no credit card). See runbooks/snowflake_runbook.md.

CREATE DATABASE IF NOT EXISTS quality_demo;
CREATE SCHEMA   IF NOT EXISTS quality_demo.inspection;
USE SCHEMA quality_demo.inspection;

CREATE OR REPLACE TABLE inspection_events (
    event_id        STRING,
    ts              TIMESTAMP_NTZ,
    line_id         STRING,
    station_id      STRING,
    part_number     STRING,
    serial_number   STRING,
    feature_dim_mm  FLOAT,
    vision_result   STRING,
    defect_code     STRING,
    cycle_time_s    FLOAT,
    inspection_pass INT
);

CREATE OR REPLACE FILE FORMAT csv_ff
    TYPE = CSV
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    SKIP_HEADER = 1
    NULL_IF = ('', 'NULL');

-- LOAD THE DATA — two options:
-- (A) Snowsight UI:  Data > Databases > QUALITY_DEMO > INSPECTION > INSPECTION_EVENTS
--     > Load Data > pick data/raw/inspection_events.csv > file format csv_ff.
-- (B) SnowSQL CLI:
--     PUT file://<path>/inspection_events.csv @%inspection_events;
--     COPY INTO inspection_events FILE_FORMAT = csv_ff ON_ERROR = 'CONTINUE';

-- sanity check
SELECT COUNT(*) AS rows, COUNT(DISTINCT station_id) AS stations FROM inspection_events;
