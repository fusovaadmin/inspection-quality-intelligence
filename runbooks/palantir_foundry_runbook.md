# Runbook — rebuild a slice of this in Palantir Foundry (free Developer Tier)

Goal: build a small end-to-end workflow on the free Foundry Developer Tier — a
pipeline, an Ontology object, and a Workshop app. ~1–2 hours (mostly the Speedrun).

## Facts (verified July 2026)
- The **Developer Tier is free**: a Palantir community moderator states
  "Developer Tier is a free tier of Foundry / AIP and you won't be charged," with
  limited compute/storage capacity enforced automatically (you hit limits rather
  than get billed). Source: <https://community.palantir.com/t/developer-tier-billing-and-usage/1074>
- Entry point for individuals: **<https://www.palantir.com/developers/>** and the
  learning site **<https://learn.palantir.com/>**.
- Foundry's app builders are **Pipeline Builder** (pipelines), the **Ontology**
  (mapping raw data onto real objects), and **Workshop** (operator-facing apps) —
  the JD names "Foundry Workshop / AIP" specifically.

> Honesty note: I verified the Developer Tier is free and the entry URLs above.
> Palantir's exact signup click-path and course catalog can change and I could not
> re-verify every UI step, so treat the numbered UI steps as a guide, not gospel —
> follow the current on-screen flow.

## Steps
1. **Sign up** for the free **Developer Tier** from <https://www.palantir.com/developers/>.
2. **Do the guided Speedrun** in Palantir Learn — "Speedrun: Your First End-to-End
   Workflow" (build a notional pipeline → ontology → app in ~60 min). This alone
   makes you conversant in Pipeline Builder + Ontology + Workshop.
3. **Then rebuild a slice of THIS project** so it's yours, not just the tutorial:
   - Upload `data/raw/inspection_events.csv` as a dataset.
   - In **Pipeline Builder**, filter to first-pass events, aggregate daily
     `n` / `fails` / `fpy` per station (mirrors `sql/rolling_fpy.sql`).
   - Model an **Ontology** object `InspectionStation` (properties: station_id,
     line, rolling_fpy, cpk, status) — this is the "map messy operational data
     onto real business objects" step, and it's the direct analog of the quality
     data model you've built for 20 years.
   - Build a one-page **Workshop** app: a station table + a yield trend, filtered
     by line. That's the operator-facing scorecard.
4. **Add the AIP layer** — a deterministic classifier function plus one **AIP Logic**
   function that only narrates its verdict. Create it from the workspace nav bar or
   `CTRL + J` / `CMD + J`, or **Files → +New → AIP Logic** (Logic files must live in a
   *project* folder, not your home folder); add the input, blocks, and Use LLM prompt;
   **Run** to test in the Debugger; **Publish**; then bind it to a markdown widget in
   Workshop. Full spec + verbatim prompt: **`foundry/aip_logic_spec.md`**.
   Source: <https://www.palantir.com/docs/foundry/logic/getting-started>

## Result
An end-to-end Foundry workflow: a Pipeline Builder pipeline, an `InspectionStation`
Ontology object, and a Workshop app on top — the same station model as the local
pipeline, expressed in Foundry's semantic layer.
