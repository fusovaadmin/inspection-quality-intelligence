# Foundry build spec — the ontology + Workshop app to reproduce here

Concrete blueprint for the free **Developer Tier** (see
`runbooks/palantir_foundry_runbook.md` for signup + the 60-min Speedrun first).
Building this makes "I haven't done Foundry" into "I built a small ontology and a
Workshop app on it." It maps 1:1 to what you already have in `src/`.

## 1. Ingest (Pipeline Builder)
- Import `data/raw/inspection_events.csv` as a dataset.
- Filter `inspection_pass = 1`; derive `d = date(ts)`, `is_fail = vision_result == 'FAIL'`.
- Aggregate to a **DailyStationMetric** dataset: group by `station_id, d` →
  `n`, `fails`, `defect_rate`, `fpy` (mirrors `sql/rolling_fpy.sql`).
- Aggregate to a **StationScorecard** dataset: `n_first_pass`, `defects`,
  `fpy_overall`, `cpk`, `spc_violations`, `status` (mirrors `station_scorecard.csv`).

## 2. Ontology (the semantic layer — your 20-year quality data model, in Foundry terms)
- **Object type `InspectionStation`** — primary key `station_id`; properties
  `line`, `fpy_overall`, `cpk`, `spc_violations`, `status`. Backed by StationScorecard.
- **Object type `DailyStationMetric`** — pk `station_id||d`; properties `date`,
  `defect_rate`, `ucl`, `lcl`, `out_of_control`, `rolling_fpy`. Backed by DailyStationMetric.
- **Link**: `InspectionStation` 1 → * `DailyStationMetric` (on `station_id`).
- *Talking point:* "The ontology is mapping messy operational data onto real
  business objects — a station, its daily metrics. That's exactly the quality data
  model I've built for 20 years; Foundry just gives it a governed home."

## 3. Workshop app (operator-facing — mirrors output/dashboard.html)
- **Home**: an `InspectionStation` object table filtered by `line`, colored by `status`.
- **Drill-down**: select a station → a time-series chart of `DailyStationMetric.defect_rate`
  with `ucl`/`lcl` (the SPC p-chart), plus its scorecard tiles.
- Optional: a **line filter** so LINE-A / LINE-B / LINE-C each get a view (the tabs).

## 4. AIP (the AI layer — your differentiator)
- An **AIP Logic** function `triageStation(station)` that, given a station's pattern
  (drift vs. shift vs. in-control), returns the root-cause hypothesis + actions —
  the Foundry-native version of `src/ai_triage.py`. Surface its output as a text
  panel on the station drill-down.

Keep it small and real. One ingest → ontology → one Workshop page → one AIP function
is enough to speak to Foundry credibly; you're not claiming production depth.
