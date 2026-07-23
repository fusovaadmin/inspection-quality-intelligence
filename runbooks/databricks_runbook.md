# Runbook — run this pipeline on Databricks Free Edition

Goal: turn "I haven't run Databricks in production" into "I stood up Free Edition
and ran my pipeline on it as a real Spark job." ~30–45 minutes.

## Facts (verified July 2026)
- **Free Edition is free** — it replaced the retired Community Edition in 2025,
  and per Databricks requires **no cloud account and no credit card**.
  Source: <https://www.databricks.com/learn/free-edition>,
  <https://www.databricks.com/blog/learn-experiment-and-build-databricks-free-edition>
- It includes **notebooks, Python + SQL, PySpark, Delta tables, dashboards**, plus
  the Databricks Assistant and Genie. Source: <https://www.databricks.com/signup/free-edition>

## Steps
1. **Sign up** at <https://www.databricks.com/signup/free-edition> — CTA is
   "Sign up for Free Edition." Use email or SSO; land in a workspace.
2. **Generate the dataset locally** and grab the CSV:
   `python -m src.generate_data` → `data/raw/inspection_events.csv`.
3. **Create a volume, then upload.** Left sidebar **Catalog** → open the
   **`workspace`** catalog → the **`default`** schema → **Create → Volume**,
   name it `inspection`. Open the volume → **Upload to this volume** → drop
   `data/raw/inspection_events.csv`. Resulting path:
   `/Volumes/workspace/default/inspection/inspection_events.csv`.
   (Heads-up: `information_schema.volumes` in the catalog tree is just a system
   view that *lists* volumes — it is not where you create one. Simplest
   alternative: **+ New → Add or upload data → Create or modify table** makes a
   Delta table directly, then read `spark.table("workspace.default.inspection_events")`.)
4. **Create a notebook**, attach **Serverless** compute, paste
   `src/pipeline_pyspark.py`, and set `RAW_PATH` to your uploaded path.
5. **Run all.** The cell renders two tables **inline directly under it** — the
   station scorecard (find S3 & S5) and the daily marts — then, if possible,
   writes Delta tables under `workspace.quality`. If nothing renders, scroll to
   the bottom of the cell for a red error and run the diagnostic in
   *Troubleshooting* below.
6. **Sanity-check parity.** The Spark `station_scorecard` (n_first_pass, defects,
   fpy_overall, spc_violations per station) should match the local pandas run in
   `data/marts/station_scorecard.csv`. Same logic, same numbers.
7. *(Optional, strong finish)* Rebuild one chart as a **Databricks dashboard** on
   `quality.daily_fpy`, or ask **Genie** a plain-English question against the
   table — that's the "AI-assisted analytics" bullet, live.

## What you can now say (honestly)
> "I ran my inspection-quality pipeline on Databricks Free Edition — read the
> events into Spark, computed first-pass yield, a 7-day rolling yield with a
> window function, and p-chart control limits, and wrote Delta tables. It's the
> same logic as my tested pandas reference, so I could diff the outputs. Foundry
> and production Databricks are a tooling ramp on top of that."

## Troubleshooting

**"Can't attach serverless compute."** Free Edition is serverless-only and gates
it behind identity verification — click **Verify identity** (top-right) and verify
with LinkedIn, allow pop-ups for databricks.com, hard-refresh, reattach. Capacity
is also flaky; a different browser/incognito or trying later often works.

**Nothing renders after Run all.** Run this **in a new cell** to see where it broke:

```python
# 1) is the file actually in the volume, at this exact path?
display(dbutils.fs.ls("/Volumes/workspace/default/inspection/"))

# 2) can Spark read it, and how many rows?
df = spark.read.option("header", True).option("inferSchema", True) \
        .csv("/Volumes/workspace/default/inspection/inspection_events.csv")
print("rows:", df.count())
display(df.limit(5))
```

- Empty listing / `Path does not exist` → the CSV never uploaded to that volume
  (re-upload, or fix `RAW_PATH` to the real path shown by step 1).
- `rows: 0` → uploaded but unreadable (re-upload the file; don't rename it).
- `rows: ~66000` and a table shows → the data is fine; the main cell errored
  *after* the read — scroll to the bottom of that cell for the red traceback.

