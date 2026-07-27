# Foundry build spec — the ontology + Workshop app to reproduce here

Concrete blueprint for the free **Developer Tier** (see
`runbooks/palantir_foundry_runbook.md` for signup + the 60-min Speedrun first).
It maps 1:1 to what you already have in `src/`, expressed in Foundry's primitives.

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
- Two functions, deliberately split so the LLM never classifies:
  **`classifyStationPattern(station)`** — a plain (non-LLM) function that returns
  drift / shift / false-alarm / in-control from SPC + capability thresholds — and
  **`narrateStationTriage(station)`** — an **AIP Logic** function whose Use LLM
  block only turns that fixed verdict into an operator-readable finding. Surface
  the narrative as a markdown panel on the station drill-down.
- Full build spec, prompt text, parity table, and UI steps:
  **`foundry/aip_logic_spec.md`**.

Keep it small and real: one ingest → ontology → one Workshop page → one AIP
function is a complete, credible slice.

## Build status (2026-07-27)
| Piece | State |
|---|---|
| Pipeline Builder pipeline `daily_station_metric_pipeline` | ✅ built |
| Ontology `InspectionStation` (9) + `DailyStationMetric` (540) + 1→∗ link | ✅ built |
| Workshop object table + status conditional formatting | ✅ built |
| 4 KPI tiles (97.4% · 2 · 97.8% · 0.51) | ✅ built, parity-verified |
| SPC p-chart w/ out-of-control scatter on the shared left axis | ✅ built (axis fixed) |
| Cpk-by-station bars · rolling-FPY 9-line · Defect Pareto | ✅ built, parity-verified |
| `classifyStationPattern` + `narrateStationTriage` (AIP) | ⬜ **designed, not built** |
