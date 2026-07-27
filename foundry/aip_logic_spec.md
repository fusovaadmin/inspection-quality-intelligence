# AIP Logic — `narrateStationTriage` (build spec)

The Foundry-native counterpart to `src/ai_triage.py`, built the same way the local
engine is built: **the classification is deterministic; the LLM only writes the
words.** The model is never allowed to decide whether a station is drifting.

This is the JD's bullet 7 — *"Build AI-Assisted Workflows: develop small applications
and automated workflows in Foundry Workshop / AIP that reduce repetitive analyst work
by 10x"* — expressed as one small, honest function.

> **Status: designed, not yet built.** The Foundry slice that IS built is the
> Pipeline Builder pipeline, the two-object ontology, the Workshop scorecard, the
> 4 KPI tiles, and the four charts. Say "designed" until the Publish button is hit.

---

## 1. The two-function split (this is the whole point)

| Function | Type | Decides anything? | Why |
|---|---|---|---|
| `classifyStationPattern` | plain Function (no LLM) | **Yes** — returns the pattern | Deterministic SPC/RCCA thresholds. Auditable, testable, reproducible. |
| `narrateStationTriage` | **AIP Logic** (LLM) | **No** — receives the pattern | Turns a fixed classification into an operator-readable paragraph. |

If an interviewer asks *"how do you stop the LLM introducing errors?"* — this diagram
is the answer. The LLM cannot misclassify a station because it never classifies one.

---

## 2. `classifyStationPattern` — the deterministic block

Input: an `InspectionStation` object. Output: a struct `{pattern, severity}`.

```
cpk_min = 1.33                      # from config/control_plan.yaml: features[0].cpk_min

if   station.cpkRecent  < cpk_min      -> ("dimensional_drift",     "high")
elif station.spcViolations >= 3        -> ("process_shift",         "high")
elif station.spcViolations >= 1        -> ("scattered_false_alarm", "low")
else                                   -> ("in_control",            "none")
```

**Honesty note on the simplification.** `src/ai_triage.py:57` requires *both*
`cpk_recent < 1.33` **and** a dimensional drift of ≥ 0.03 mm before it calls
`dimensional_drift`. The 0.03 mm drift is computed from raw `feature_dim_mm` and is
not currently a property on the ontology, so the Foundry version tests capability
only. On this dataset the two conditions co-fire on S3, so the branch above
reproduces the local engine's output on **all nine stations**:

| Station | cpk_recent | SPC viol | Foundry pattern | `ai_triage.py` pattern | Match |
|---|---|---|---|---|---|
| S3 | 0.514 | 11 | dimensional_drift | dimensional_drift | ✅ |
| S5 | 1.477 | 13 | process_shift | process_shift | ✅ |
| S2 | 1.512 | 1 | scattered_false_alarm | scattered_false_alarm | ✅ |
| S7 | 1.503 | 1 | scattered_false_alarm | scattered_false_alarm | ✅ |
| S1, S4, S6, S8, S9 | ~1.47–1.53 | 0 | in_control | in_control | ✅ |

To make it exact rather than equivalent, add `dim_drift_mm` as a `DailyStationMetric`
or `InspectionStation` property in Pipeline Builder and restore the `AND` condition.
**Say "equivalent on this dataset," not "identical logic" — the difference is real.**

---

## 3. `narrateStationTriage` — the AIP Logic function

**Input:** `station` — object, type `InspectionStation`.
**Output:** `narrative` — string (markdown).

Blocks, in order:

1. **Execute function** → `classifyStationPattern(station)` → variable `triage`.
   *(Deterministic. This block, not the LLM, sets the pattern.)*
2. **Create variable** → `evidence`, a string assembled from object properties:
   `station.title`, `station.lineId`, `station.fpyOverall`, `station.cpkOverall`,
   `station.cpkRecent`, `station.spcViolations`, `station.spcFirstViolation`,
   `station.defects`, `station.nFirstPass`.
3. **Use LLM** → the narration block. Prompt below.
4. **Output** → the LLM block's text as `narrative`.

### The Use LLM prompt (paste verbatim)

```
You are writing a shop-floor quality finding for a manufacturing engineer.

A deterministic SPC engine has ALREADY classified this station. Its verdict is
final and you must not change it, question it, or hedge it:

  pattern  = {{triage.pattern}}
  severity = {{triage.severity}}

Evidence (do not invent any number that is not listed here):
{{evidence}}

Control plan: part PN-1000 rev C, mounting_bore_diameter, LSL 24.80 mm,
USL 25.20 mm, minimum acceptable Cpk 1.33. Reaction plan on breach: quarantine
affected units, notify Manufacturing Engineering, open an RCCA within 24 hours.

Write, in this order and nothing else:
1. FINDING — two sentences stating what the data shows, citing the specific
   numbers above.
2. LIKELY CAUSES — 3 to 4 bullets, most probable first, appropriate to the
   pattern. For dimensional_drift consider tool/insert wear, fixture loosening,
   thermal growth, gauge drift, lot-to-lot material change. For process_shift
   consider incoming lot change, setup/changeover, operator or shift change,
   inspection-program revision.
3. RECOMMENDED ACTIONS — 3 bullets, each one something a person can do today.

Rules: plain English, no jargon a production supervisor would not use. No
preamble, no apology, no "as an AI". Do not recommend action for an in_control
station beyond routine monitoring. If a number you need is missing, write
"not available" — never estimate it.
```

The three prohibitions at the end (`don't reclassify`, `don't invent numbers`,
`say "not available"`) are the guardrails worth naming out loud in the interview —
they are the same rules the local engine enforces structurally.

---

## 4. Build steps in the Foundry UI

Verified against Palantir's current AIP Logic docs (July 2026 — see Sources):

1. Open **AIP Logic** from the workspace navigation bar, or `CTRL + J` / `CMD + J`;
   alternatively **Files → +New → AIP Logic**.
   *Logic files must be saved in a **project folder**, not your home folder.*
2. In the **Inputs** block (left panel), add input `station`, type = object,
   object type `InspectionStation`.
3. Add the blocks from §3. AIP Logic supports **create variable**, **execute
   function**, **apply action**, and **use LLM** blocks; a block's output is
   available to later blocks.
4. Select **Run** in the right sidebar to test. The **Debugger** shows the LLM's
   chain of thought — screenshot this, it is good demo material. Save a unit test
   from the run panel while you are there.
5. Select **Publish**.
6. In **Workshop**, add a **Markdown** or text widget to the station drill-down
   and bind it to the published function, passing the selected `InspectionStation`.

*No Ontology write is involved — this function returns a string, so you do **not**
need the "Apply actions tool" / Action-backed path the docs describe for edits.*

---

## 5. What to say about it

- **The mechanism:** "The classification is deterministic — SPC and capability
  thresholds in a plain function. AIP only narrates the result. The model can't
  introduce a classification error because it never makes the classification."
- **The 10x:** "Reading a control chart, pulling the capability study, and writing
  the finding is fifteen or twenty minutes of an engineer's morning per station.
  This is one click, and it says the same thing every time."
- **The honesty:** "The Foundry classifier tests capability only; my Python version
  also requires a 0.03 mm dimensional drift. They agree on all nine stations here,
  but they're equivalent on this data, not identical in logic."

---

## Sources

- [AIP Logic — Getting started](https://www.palantir.com/docs/foundry/logic/getting-started)
- [AIP Logic — Overview](https://www.palantir.com/docs/foundry/logic/overview)
- [AIP Logic — Blocks](https://www.palantir.com/docs/foundry/logic/blocks)
- [AIP Logic — Core concepts](https://www.palantir.com/docs/foundry/logic/core-concepts)
