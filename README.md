# Inspection Quality Intelligence

[![CI](https://github.com/fusovaadmin/inspection-quality-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/fusovaadmin/inspection-quality-intelligence/actions/workflows/ci.yml)

> **In plain terms (for a non-technical reviewer):** a working demo of this exact job —
> it ingests data from factory **vision-inspection stations**, automatically flags the
> stations that are **drifting out of spec or have suddenly shifted**, and gives a
> plain-English **likely root cause and recommended fix** for each — with the same logic
> **executed on Databricks** (its output drives the published pages), **built as a live
> Palantir Foundry slice**, and **written as Snowflake SQL** for portability.
> Built on synthetic sample data.
>
> **See it running:** [scorecard](https://fusovaadmin.github.io/inspection-quality-intelligence/output/scorecard.html)
> · [interactive dashboard](https://fusovaadmin.github.io/inspection-quality-intelligence/output/dashboard.html)

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
- **Heterogeneous system joins** — the stations are only one source. **ERP** work
  orders, an **MES** shift log and **QMS** nonconformance reports are landed as
  their own systems wrote them (different key formats, different date formats,
  different grain, different defect taxonomy) and reconciled through a versioned
  **crosswalk** (`config/system_crosswalk.yaml`, `src/integrate.py`) using three
  different join strategies: a **non-equi range join**, an **interval join**, and a
  **normalized-key join**. Everything that fails to join is reported, not dropped.
- **Advanced SQL** — window functions (rolling yield), window aggregates for SPC
  center lines, a **three-system join in one query** (`sql/heterogeneous_join.sql`),
  and a real **before/after query tuning** with a proof of equivalence
  (`sql/README.md`).
- **Data-quality engineering** — a machine-readable **control plan**
  (`config/control_plan.yaml`) drives automated validation: required fields,
  unique keys, physical ranges, and result/defect-code consistency, so the data
  feeding the metrics is provably trustworthy.
- **Quality methods, in code** — first-pass yield, **frozen-baseline p-chart SPC**
  (Phase I → II), and **process capability (Cpk)**.
- **Inspection coverage audit** (`src/coverage.py`) — is the control plan looking
  for what the floor actually produces? One failure mode is detected at all nine
  stations and assigned to none, so it has no spec and no reaction plan. Another is
  on three stations' lists and has never once been found, which is reported as an
  **open question** rather than counted as coverage: the data cannot say whether it
  never happens or the station cannot see it.
- **Position-dependent operating point** (`config/cost_model.yaml`) — a false
  reject costs more the further down the line it happens; an escape costs whatever
  it takes to catch it wherever it is caught next. Costs are **relative value
  units, never currency**, and every conclusion is **swept across the assumption
  ranges** before it is reported. One does not survive: the intuitive rule that
  stations should get less aggressive downstream holds in 6 of 81 combinations,
  and **a test asserts that claim fails** so tuning the assumptions until the story
  is tidy turns the suite red instead of quiet.
- **Cell self-test** (`config/station_selftest.yaml`) — a robot checks its home
  position before trusting its coordinates; before judging any part, each station
  measures a **known reference coupon** and inspects its **own tooling**. Because
  the answer is known in advance, bias and repeatability are measured rather than
  inferred. Which step fails decides which inspections are lost, and they point
  opposite ways: failed imaging costs the visual checks and spares the
  measurement; failed measurement or tooling does the reverse. **Fixture and
  end-of-arm tooling are separate sets**, because both push on the same reading and
  only inspecting them separately says which one moved.
- **Look across — extent of condition** (`src/look_across.py`) — a finding at one
  station is half an answer. What propagates a fault is usually **not the station**:
  a worn gripper travels with the **robot**, so every station on that arm is exposed
  even without a finding of its own. A gap present at every station is reported
  **systemic** — one problem with the plan, not nine local ones.
- **Loop closure** — every finding ends in probable causes, containment, corrective
  actions and an owner, looked up from reviewed config. Attribution is
  **deterministic**; no model chooses a cause.
- **Software rigor on data code** — unit + SQL-equivalence + triage + coverage +
  self-test tests and a GitHub Actions pipeline that regenerates data, runs the
  pipeline, and gates on tests. Several tests exist to enforce honesty rather than
  correctness: one asserts no currency appears in the cost model, one scans every
  finding for failure-prediction language, and one asserts this stage never writes
  to the Databricks-produced marts.
- **Interactive operator dashboard** (`output/dashboard.html`) — line-flow tabs
  (LINE-A/B/C), per-station drill-down, an AI triage panel, and a live "run
  inspection test" simulator that drops a batch onto the control chart.
- **AI root-cause triage** (`src/ai_triage.py`) — classifies each flagged station
  (drift vs. shift vs. false alarm) and emits a hypothesis + recommended actions.
- **Cross-stack portability** — the same logic is **executed on Databricks**
  (`src/pipeline_pyspark.py`, and its Delta output drives the published pages),
  **built as a live Palantir Foundry slice** (`foundry/` — pipeline, ontology,
  Workshop app), and **written as Snowflake SQL** (`snowflake/` — a portability
  proof, not executed); see `docs/ARCHITECTURE.md`.

## The scenario (and the baked-in findings the pipeline auto-detects)

9 vision-inspection stations across 3 lines, 60 days, ~98k events. Injected:

| Signal | Where | Detected by |
|--------|-------|-------------|
| Gradual dimensional **drift** toward the USL | station **S3** | Cpk collapses 1.48 → ~0.5; capability alert |
| Step **process shift** in defect rate (~day 40) | station **S5** | p-chart rule-1 out-of-control run |
| ~1% missing serials, dup event_ids, sensor glitches, PASS-with-defect | across all | control-plan validation (**2,144 offending events of 98,401** → DQ score 0.9782) |

Plus four **cross-system** breaks that no single system can see on its own — the
kind you only find once ERP, MES and QMS are joined to the floor:

| Cross-system break | Where | Caught by |
|--------------------|-------|-----------|
| Work order **closed two days early** while the line kept running | LINE-C, Apr 27–28 | **1,080** first-pass inspections with no open work order |
| MES **logger outage** (3 shifts) | WC-06 / S6, shift B | **172** events with no shift context — invisible if you drive the join from MES |
| MES kept **booking to a work order ERP had closed** | LINE-C | **18** shift records contradicting ERP |
| QMS **NCRs against serials that were never inspected**, and NCR categories that **contradict** the station's defect code | across all | **15** orphan NCRs · **10** taxonomy conflicts |

Referential-integrity score: **0.9771**. The MES production day is not the
calendar day (shift C runs 22:00→06:00), so joining on `CAST(ts AS DATE)`
mis-assigns **24.9% of events** — quantified in `data/quality/integration_report.json`.

## Architecture

```
data/raw/inspection_events.csv            (generate_data.py — deterministic, seeded)
        │
        ▼
control-plan validation  ──► data/quality/{dq_report.json, violations.csv}
        │  (config/control_plan.yaml)
        ├──────────────────────────────────────────────┐
        ▼                                              ▼
metrics: first-pass yield → rolling FPY (window)   heterogeneous integration (integrate.py)
         → p-chart SPC → Cpk        (metrics.py)   ERP  range join  ┐
        │                                          MES  interval    ├─ config/system_crosswalk.yaml
        ▼                                          QMS  serial key  ┘
data/marts/{daily_fpy, station_scorecard,               │
            pareto, findings}                           ▼
        │                             data/marts/{wo_station_day, mes_shift_reconciliation}
        ▼                             data/quality/{integration_report.json, orphans.csv}
output/scorecard.html   (self-contained dashboard: KPIs, status table, 5 charts)

sources: data/raw/systems/{erp_work_orders, mes_shift_log, qms_ncr}.csv
ports:   src/pipeline_pyspark.py (Databricks)   ·   sql/*.sql (Spark SQL / DuckDB)
```

## Run it

```bash
pip install -r requirements.txt
python -m src.generate_data      # write the synthetic dataset
python -m src.generate_systems   # write the ERP / MES / QMS extracts
python -m src.pipeline_pandas    # validate + build the marts (prints the scorecard)
python -m src.integrate          # join ERP + MES + QMS to the floor (prints the breaks)
python -m src.ai_triage          # root-cause triage -> data/marts/triage.json
python -m src.scorecard          # render output/scorecard.html (static)
python -m src.dashboard          # render output/dashboard.html (interactive)
pytest -q                        # 93 tests: metrics, validation, integration, SQL parity,
                                 #           triage, coverage, self-test, look-across
```

Open `output/scorecard.html` in any browser.

## Layout

```
config/control_plan.yaml     machine-readable control plan (specs + DQ rules)
config/system_crosswalk.yaml versioned key/grain/taxonomy crosswalk across 4 systems
src/generate_data.py         seeded synthetic vision-inspection generator (3 lines)
src/generate_systems.py      seeded ERP / MES / QMS extracts, each in its own dialect
src/metrics.py               FPY, rolling FPY, frozen-baseline p-chart, Cpk (tested core)
src/validation.py            control-plan-driven data-quality validation
src/integrate.py             land -> validate -> conform -> join -> reconcile (ERP/MES/QMS)
src/pipeline_pandas.py       reference end-to-end pipeline
src/pipeline_pyspark.py      Databricks port of the same logic
src/ai_triage.py             root-cause triage (drift / shift / false alarm)
src/scorecard.py             static HTML scorecard
src/dashboard.py             interactive line-flow dashboard + run-test simulator
sql/                         window-function SQL, 3-system joins, before/after tuning (+ README)
snowflake/                   Snowflake parity SQL (same logic, warehouse engine)
foundry/                     Foundry ontology + Workshop build spec + AIP Logic spec
docs/                        ARCHITECTURE.md (stack diagram + same-logic table)
tests/                       unit + SQL-equivalence + triage tests
runbooks/                    stand it up free on Databricks / Snowflake / Foundry
.github/workflows/ci.yml     regenerate → run → triage → test on every push
```

All data is **synthetic and deterministic** (fixed seed) — every run reproduces
the same numbers, which is what lets the tests assert on the baked-in anomalies.
