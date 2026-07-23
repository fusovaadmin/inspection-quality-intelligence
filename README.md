# Inspection Quality Intelligence

A small, end-to-end **manufacturing quality data platform** over automated
vision-inspection data: raw inspection events → control-plan validation → SPC and
process-capability analytics → an operator-facing scorecard, with a PySpark port
for a cloud lakehouse and a CI-gated test suite.

Built to mirror a **Manufacturing Quality Intelligence** data-engineering role
whose three pillars are **Analytics, Manufacturing AI, and Vision Inspection**.
The synthetic source is deliberately shaped like vision-inspection-station output,
and real quality problems are baked in so the analytics have something to catch.

---

## What it demonstrates

- **Pipelines in a cloud lakehouse** — the transform ships as tested pandas
  (`src/pipeline_pandas.py`) **and** as an idiomatic PySpark job for Databricks
  (`src/pipeline_pyspark.py`), plus Spark-SQL/DuckDB queries in `sql/`.
- **Advanced SQL** — window functions (rolling yield), window aggregates for SPC
  center lines, and a real **before/after query tuning** with a proof of
  equivalence (`sql/README.md`).
- **Data-quality engineering** — a machine-readable **control plan**
  (`config/control_plan.yaml`) drives automated validation: required fields,
  unique keys, physical ranges, and result/defect-code consistency, so the data
  feeding the metrics is provably trustworthy.
- **Quality methods, in code** — first-pass yield, **p-chart SPC** with 3-sigma
  limits and rule-1/run triage, and **process capability (Cpk)**.
- **Software rigor on data code** — unit + SQL-equivalence tests and a GitHub
  Actions pipeline that regenerates data, runs the pipeline, and gates on tests.

## The scenario (and the baked-in findings the pipeline auto-detects)

6 vision-inspection stations across 2 lines, 60 days, ~66k events. Injected:

| Signal | Where | Detected by |
|--------|-------|-------------|
| Gradual dimensional **drift** toward the USL | station **S3** | Cpk collapses 1.48 → ~0.5; capability alert |
| Step **process shift** in defect rate (~day 40) | station **S5** | p-chart rule-1 out-of-control run |
| ~1% missing serials, dup event_ids, sensor glitches, PASS-with-defect | across all | control-plan validation (~1,400 events quarantined) |

## Architecture

```
data/raw/inspection_events.csv            (generate_data.py — deterministic, seeded)
        │
        ▼
control-plan validation  ──► data/quality/{dq_report.json, violations.csv}
        │  (config/control_plan.yaml)
        ▼
metrics: first-pass yield → rolling FPY (window) → p-chart SPC → Cpk   (metrics.py)
        │
        ▼
data/marts/{daily_fpy, station_scorecard, pareto, findings}
        │
        ▼
output/scorecard.html   (self-contained dashboard: KPIs, status table, 5 charts)

ports:  src/pipeline_pyspark.py (Databricks)   ·   sql/*.sql (Spark SQL / DuckDB)
```

## Run it

```bash
pip install -r requirements.txt
python -m src.generate_data      # write the synthetic dataset
python -m src.pipeline_pandas    # validate + build the marts (prints the scorecard)
python -m src.scorecard          # render output/scorecard.html
pytest -q                        # 11 tests: metrics, validation, SQL equivalence
```

Open `output/scorecard.html` in any browser.

## Layout

```
config/control_plan.yaml     machine-readable control plan (specs + DQ rules)
src/generate_data.py         seeded synthetic vision-inspection generator
src/metrics.py               FPY, rolling FPY, p-chart limits, Cpk (the tested core)
src/validation.py            control-plan-driven data-quality validation
src/pipeline_pandas.py       reference end-to-end pipeline
src/pipeline_pyspark.py      Databricks port of the same logic
src/scorecard.py             self-contained HTML dashboard
sql/                         window-function SQL + before/after tuning (+ README)
tests/                       unit tests + SQL before/after equivalence
runbooks/                    stand it up on Databricks Free Edition & Foundry Dev Tier
.github/workflows/ci.yml     regenerate → run → test on every push
```

All data is **synthetic and deterministic** (fixed seed) — every run reproduces
the same numbers, which is what lets the tests assert on the baked-in anomalies.
