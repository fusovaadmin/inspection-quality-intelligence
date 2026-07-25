# Runbook — run the same quality logic on Snowflake (free trial)

Goal: turn the recruiter's "have you worked with Snowflake?" (you said no — good,
honest) into "…and I stood the same pipeline up on a Snowflake trial to prove the
model is portable." ~30 minutes.

## Facts (verified July 2026)
- **30-day free trial, $400 in credits, no credit card.** Sign up at
  <https://signup.snowflake.com/>. Sources:
  <https://www.snowflake.com/en/snowflake-trial/>,
  <https://docs.snowflake.com/en/user-guide/admin-trial-account>.
- The account suspends at 30 days / $400; reactivating needs a card (don't add one).

## Steps
1. **Sign up** at <https://signup.snowflake.com/> (Standard edition, any cloud/region is fine).
2. Open **Snowsight** → **Worksheets** → new worksheet.
3. Run **`snowflake/setup_and_load.sql`** — creates `quality_demo.inspection.inspection_events`
   and a CSV file format.
4. **Load the CSV**: Snowsight → **Data → Databases → QUALITY_DEMO → INSPECTION →
   INSPECTION_EVENTS → Load Data** → pick `data/raw/inspection_events.csv`, file
   format `csv_ff`. (Or `PUT` + `COPY INTO` via SnowSQL — see the SQL comments.)
5. Run **`snowflake/rolling_fpy.sql`** and **`snowflake/spc_p_chart.sql`**. Confirm
   S3/S5 surface as the out-of-control stations — same result as Databricks/DuckDB.

## What you can now say (honestly)
> "I hadn't used Snowflake, so I loaded the same inspection dataset onto a Snowflake
> trial and ran the identical window-function SQL — rolling yield and a frozen-baseline
> p-chart. Same answers as Databricks and DuckDB. That's the point: I model the data,
> so the logic is portable across the lakehouse/warehouse layer."
