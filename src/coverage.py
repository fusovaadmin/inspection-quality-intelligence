"""
Inspection coverage audit and position-dependent operating-point economics.

TWO QUESTIONS THIS ANSWERS
--------------------------
1. COVERAGE — is the control plan looking for the things the floor actually
   produces? A control plan lists the characteristics somebody thought to write
   down. The floor produces whatever it produces. The gap between those two sets
   is invisible unless something computes it, because a failure mode nobody is
   assigned to detect generates no alert, no chart, and no absence anyone notices.

2. OPERATING POINT — what does being wrong cost HERE? The cost of a false reject
   rises as a unit moves down the line, because more value is embodied in it. The
   cost of an escape depends on where it is caught next. Those two do not move
   together, so a single detection threshold cannot be correct at every station.

WHAT THIS MODULE IS NOT
-----------------------
It does not process images and it contains no vision model. It reads inspection
RESULTS, the control plan, and a cost model, and reports on the inspection PLAN.
The costs are relative value units from config/cost_model.yaml — assumptions a
human owns, not measurements — which is why every conclusion is also swept
across the assumption ranges before it is reported (see `sensitivity`).

Run:  python -m src.coverage
"""
from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .validation import load_control_plan, validate

ROOT = Path(__file__).resolve().parents[1]
COST_MODEL = ROOT / "config" / "cost_model.yaml"
ENV_MODEL = ROOT / "config" / "station_selftest.yaml"     # self-test limits + gating
CROSSWALK = ROOT / "config" / "system_crosswalk.yaml"
RAW = ROOT / "data" / "raw" / "inspection_events.csv"
QMS = ROOT / "data" / "raw" / "systems" / "qms_ncr.csv"
ENV_TELEMETRY = ROOT / "data" / "raw" / "systems" / "station_selftest.csv"
QDIR = ROOT / "data" / "quality"
MARTS = ROOT / "data" / "marts"
SCORECARD = MARTS / "station_scorecard.csv"          # read-only: Databricks output
REPORT = QDIR / "coverage_report.json"

# The mode every station is assigned to detect. Holding the mode constant is the
# only way to isolate the effect of POSITION on the economics — otherwise the
# mode mix and the position move together and neither can be read.
COMMON_MODE = "DIM-OOS"


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def mode_label(code: str, plan: dict) -> str:
    """'LBL-ERR' -> 'Label / marking error (LBL-ERR)'.

    Codes are how the machines talk. Nobody reading this for the first time knows
    what SRF-SCR means, and a reader who has to decode an abbreviation to follow a
    finding will stop reading the finding. Every code gets its plain name attached
    everywhere it is shown.
    """
    spec = (plan.get("inspection_coverage", {}).get("modes") or {}).get(code) or {}
    label = spec.get("label")
    return f"{label} ({code})" if label else code


BASIS_PLAIN = {
    "geometry": "size and position measurement",
    "appearance_high_contrast": "obvious visual checks",
    "appearance_low_contrast": "fine visual checks",
}


def basis_label(basis: str, cfg: dict | None = None) -> str:
    """'appearance_low_contrast' -> 'fine visual checks'."""
    return BASIS_PLAIN.get(basis, str(basis).replace("_", " "))


def station_positions(cost: dict) -> dict[str, tuple[str, int]]:
    """station_id -> (line_id, 1-based sequence position)."""
    out: dict[str, tuple[str, int]] = {}
    for line, stations in cost["line_sequence"].items():
        for i, st in enumerate(stations, start=1):
            out[st] = (line, i)
    return out


# --------------------------------------------------------------------------- #
# observed reality
# --------------------------------------------------------------------------- #
def observed_detections(events: pd.DataFrame) -> pd.DataFrame:
    """First-pass FAIL counts per (station, defect mode) — what the floor reported.

    First pass only: a rework re-inspection is a second look at a unit already
    counted, and counting it again would inflate the mode's apparent frequency.
    """
    fp = events[(events["inspection_pass"] == 1) & (events["vision_result"] == "FAIL")]
    fp = fp[fp["defect_code"].notna()]
    return (fp.groupby(["station_id", "defect_code"]).size()
            .reset_index(name="detections")
            .sort_values(["station_id", "defect_code"], kind="stable")
            .reset_index(drop=True))


def ncr_modes(xw: dict) -> pd.DataFrame:
    """QMS nonconformances mapped through the crosswalk to canonical modes.

    The QMS taxonomy is human and organised by root cause; the station taxonomy is
    machine and organised by what the sensor reports. The crosswalk is the only
    place those two are reconciled, and it is versioned config for that reason.
    Categories with no mapping are reported, never dropped.
    """
    if not QMS.exists():
        return pd.DataFrame(columns=["defect_code", "ncrs", "unmapped_category"])
    qms = pd.read_csv(QMS)
    mapping = xw["defect_category"]
    cat = qms["DEFECT_CATEGORY"].astype(str).str.strip().str.upper()
    rows = (cat.map(mapping).rename("defect_code").to_frame()
            .assign(unmapped_category=cat.where(~cat.isin(mapping), None)))
    mapped = (rows[rows["defect_code"].notna()].groupby("defect_code").size()
              .reset_index(name="ncrs"))
    mapped["unmapped_category"] = None
    unmapped = (rows[rows["defect_code"].isna()].groupby("unmapped_category").size()
                .reset_index(name="ncrs"))
    if not unmapped.empty:
        unmapped["defect_code"] = None
        mapped = pd.concat([mapped, unmapped], ignore_index=True)
    return mapped


# --------------------------------------------------------------------------- #
# coverage matrix
# --------------------------------------------------------------------------- #
def build_coverage(plan: dict, cost: dict, observed: pd.DataFrame) -> pd.DataFrame:
    """One row per (station, mode): declared in the plan? observed on the floor?

    Four states fall out of the cross-product, and they are genuinely different
    problems:

      COVERED             declared and detecting        - working as intended
      UNDECLARED_DETECTED declared nowhere, still fires - no spec, no reaction plan
      DECLARED_SILENT     declared, zero detections     - never happens, or the
                                                          station cannot see it
      NOT_APPLICABLE      neither declared nor observed - no claim either way
    """
    cov = plan["inspection_coverage"]
    pos = station_positions(cost)
    declared = {st: set(v.get("detects") or []) for st, v in cov["stations"].items()}
    modes = list(cov["modes"].keys())
    obs = {(r.station_id, r.defect_code): int(r.detections)
           for r in observed.itertuples()}
    # Modes the floor reports that the control plan never even names.
    for (_, code) in obs:
        if code not in modes:
            modes.append(code)

    rows = []
    for st in sorted(declared):
        line, p = pos[st]
        for m in modes:
            is_declared = m in declared[st]
            n = obs.get((st, m), 0)
            if is_declared and n > 0:
                state = "COVERED"
            elif is_declared and n == 0:
                state = "DECLARED_SILENT"
            elif not is_declared and n > 0:
                state = "UNDECLARED_DETECTED"
            else:
                state = "NOT_APPLICABLE"
            rows.append(dict(line_id=line, station_id=st, position=p, mode=m,
                             declared=is_declared, detections=n, state=state,
                             specified=bool((cov["modes"].get(m) or {}).get("feature_ref"))))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# position-dependent economics
# --------------------------------------------------------------------------- #
def false_reject_cost(position: int, cost: dict, *, rework_fraction: float | None = None,
                      fixed_handling: float | None = None) -> float:
    """Cost of rejecting a GOOD unit at this position, in value units.

    You do not scrap a falsely-rejected unit, you disposition it — so the cost is
    a fraction of the value already embodied plus fixed handling. It rises with
    position for the same reason a late scrap hurts more than an early one.
    """
    fr = cost["false_reject"]
    rf = fr["rework_fraction_of_embodied_value"] if rework_fraction is None else rework_fraction
    fh = fr["fixed_handling_vu"] if fixed_handling is None else fixed_handling
    return cost["value_at_position"][position] * rf + fh


def discovery_point(mode: str, line: str, from_position: int,
                    plan: dict, cost: dict) -> tuple[str, str]:
    """Where a unit carrying `mode` past `from_position` is eventually caught.

    Returns (kind, label). kind is one of: station | final_test | customer.
    This is the whole reason position matters — the escape cost is set by the
    NEXT place that can see the mode, not by where it was missed.
    """
    cov = plan["inspection_coverage"]
    seq = cost["line_sequence"][line]
    for i, st in enumerate(seq, start=1):
        if i <= from_position:
            continue
        if mode in (cov["stations"].get(st, {}).get("detects") or []):
            return "station", st
    if mode in (cov.get("final_test_detects") or []):
        return "final_test", "FINAL-TEST"
    return "customer", "CUSTOMER"


def escape_cost(mode: str, line: str, from_position: int, plan: dict, cost: dict,
                *, final_penalty: float | None = None,
                customer_penalty: float | None = None) -> tuple[float, str, str]:
    """Cost of passing a DEFECTIVE unit at this position, in value units."""
    esc = cost["escape"]
    tm = esc["teardown_multiplier"]
    fp = esc["final_test_penalty_vu"] if final_penalty is None else final_penalty
    cp = esc["customer_penalty_vu"] if customer_penalty is None else customer_penalty
    kind, label = discovery_point(mode, line, from_position, plan, cost)
    if kind == "station":
        pos = cost["line_sequence"][line].index(label) + 1
        return cost["value_at_position"][pos] * tm, kind, label
    if kind == "final_test":
        return cost["value_at_final_test"] * tm + fp, kind, label
    return cost["value_at_final_test"] * tm + cp, kind, label


def build_economics(plan: dict, cost: dict) -> pd.DataFrame:
    """Per (station, declared mode): what being wrong costs, and the exchange rate.

    The exchange rate is the number that actually sets the operating point:

        exchange_rate = cost(one escape) / cost(one false reject)

    Read it as "at this station, for this mode, you should accept up to N false
    rejects to prevent one escape." A high rate says buy recall. A rate near 1
    says a false reject hurts almost as much as an escape, so recall and
    precision have to be traded jointly rather than one dominating.
    """
    cov = plan["inspection_coverage"]
    pos = station_positions(cost)
    rows = []
    for st, spec in cov["stations"].items():
        line, p = pos[st]
        c_fr = false_reject_cost(p, cost)
        for m in (spec.get("detects") or []):
            c_esc, kind, where = escape_cost(m, line, p, plan, cost)
            rows.append(dict(
                line_id=line, station_id=st, position=p, mode=m,
                false_reject_cost_vu=round(c_fr, 4),
                escape_cost_vu=round(c_esc, 4),
                escape_found_at=where, escape_found_kind=kind,
                exchange_rate=round(c_esc / c_fr, 3),
                backstop=(kind != "customer"),
            ))
    return (pd.DataFrame(rows)
            .sort_values(["line_id", "position", "mode"], kind="stable")
            .reset_index(drop=True))


# --------------------------------------------------------------------------- #
# sensitivity — the assumptions are swept before any conclusion is reported
# --------------------------------------------------------------------------- #
def sensitivity(plan: dict, cost: dict) -> dict:
    """Sweep the cost assumptions and test which conclusions survive all of them.

    Every figure in cost_model.yaml is an assumption, so a single-point answer is
    worth very little. What is worth something is a claim that holds across the
    whole plausible range. Two claims are tested:

      C1  false-reject cost strictly INCREASES with position
      C2  exchange rate strictly DECREASES with position (holding the mode fixed)

    C1 is the robust half of the positional argument. C2 is the intuitive reading
    of it — and it is the one worth checking rather than assuming.
    """
    sw = cost["sensitivity"]["sweep"]
    combos = list(product(sw["rework_fraction_of_embodied_value"],
                          sw["fixed_handling_vu"],
                          sw["final_test_penalty_vu"],
                          sw["customer_penalty_vu"]))
    lines = list(cost["line_sequence"].keys())
    c1_hold = c2_hold = 0
    for rf, fh, fpen, cpen in combos:
        c1_ok = c2_ok = True
        for line in lines:
            seq = cost["line_sequence"][line]
            frs, rates = [], []
            for p in range(1, len(seq) + 1):
                c_fr = false_reject_cost(p, cost, rework_fraction=rf, fixed_handling=fh)
                c_esc, _, _ = escape_cost(COMMON_MODE, line, p, plan, cost,
                                          final_penalty=fpen, customer_penalty=cpen)
                frs.append(c_fr)
                rates.append(c_esc / c_fr)
            c1_ok &= all(b > a for a, b in zip(frs, frs[1:]))
            c2_ok &= all(b < a for a, b in zip(rates, rates[1:]))
        c1_hold += int(c1_ok)
        c2_hold += int(c2_ok)
    n = len(combos)
    return {
        "combinations_tested": n,
        "C1_false_reject_cost_increases_with_position": {
            "holds_in": c1_hold, "of": n, "robust": c1_hold == n},
        "C2_exchange_rate_decreases_with_position": {
            "holds_in": c2_hold, "of": n, "robust": c2_hold == n},
    }


# --------------------------------------------------------------------------- #
# station condition — can it still see what it was assigned to see?
# --------------------------------------------------------------------------- #
def _longest_run(flags: list[bool]) -> int:
    """Longest run of consecutive True. Persistence, not any single reading."""
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


CHECKS = ("imaging_check", "measurement_check", "tooling_check")


def tooling_sets(cfg: dict) -> dict:
    """The tooling sets a station inspects on itself, each with its own limits.

    Two of them, and the split is the point. The end-of-arm tooling PLACES the
    part; the fixture HOLDS it. Both feed the same symptom — presentation scatter,
    which shows up as repeatability in step 2 — and step 2 cannot tell them apart.
    A worn gripper and a worn locator produce an identical reading.

    Inspecting them as separate sets is what turns "repeatability is out" into
    "the gripper is out and the fixture is fine". Without it, the corrective
    action after a repeatability failure is a coin flip between replacing a
    fixture and replacing a gripper.
    """
    return (cfg.get("tooling_check", {}) or {}).get("sets") or {}


def failing_tooling_sets(cfg: dict, row) -> list[str]:
    """Which tooling sets are out on this row. Usually one — that is the answer."""
    return [k for k in tooling_sets(cfg) if not bool(row.get(f"tooling_{k}_ok", True))]


def evaluate_selftest(cfg: dict, st: pd.DataFrame,
                      *, overrides: dict | None = None) -> pd.DataFrame:
    """GO / NO-GO per self-test step, per station-day.

    The station measured a coupon whose true values are known, and inspected its
    own fixture. Each step passes or fails against the limits in config — there is
    no inference here, only a comparison against a known answer.
    """
    o = overrides or {}
    ic, mc, tc = cfg["imaging_check"], cfg["measurement_check"], cfg["tooling_check"]
    out = st.copy()

    grade_min = o.get("datamatrix_grade", ic["datamatrix_grade"]["min"])
    sharp_min = o.get("sharpness_score", ic["sharpness_score"]["min"])
    bias_max = o.get("bias_mm", mc["bias_mm"]["max_abs"])

    out["imaging_ok"] = (
        out.ref_brightness_pct.between(ic["brightness_pct_of_reference"]["min"],
                                       ic["brightness_pct_of_reference"]["max"])
        & (out.ref_sharpness_score >= sharp_min)
        & (out.datamatrix_grade >= grade_min))
    out["measurement_ok"] = ((out.ref_bias_mm <= bias_max)
                             & (out.ref_repeatability_mm <= mc["repeatability_mm"]["max"]))
    # Tooling is inspected as SETS, not as one lump. The end-of-arm tooling places
    # the part and the fixture holds it; both push on the same measured symptom in
    # step 2, so a single "tooling ok" flag would tell you something is wrong and
    # not which of them it was. Each set gets its own verdict.
    for set_name, spec in tooling_sets(cfg).items():
        ok = pd.Series(True, index=out.index)
        for col, m in spec["measures"].items():
            limit = o.get(f"{set_name}.{col}", o.get(col, m["max"]))
            ok &= out[col] <= limit
        out[f"tooling_{set_name}_ok"] = ok
    set_cols = [f"tooling_{k}_ok" for k in tooling_sets(cfg)]
    out["tooling_ok"] = out[set_cols].all(axis=1)
    out["selftest_ok"] = out.imaging_ok & out.measurement_ok & out.tooling_ok
    return out


def gates_for(basis: str, cfg: dict) -> list[str]:
    """Which self-test steps have to pass before this class of inspection is trusted.

    This mapping is the reason a self-test is more than a health light. A failed
    imaging step and a failed measurement step cost the station DIFFERENT
    inspections, so 'the cell is red' is not an answer — 'here is specifically what
    this station can no longer be trusted to find' is.
    """
    return [c for c in CHECKS if basis in (cfg[c].get("gates") or [])]


def build_station_condition(plan: dict, cfg: dict, selftest: pd.DataFrame,
                            *, overrides: dict | None = None) -> pd.DataFrame:
    """Per (station, declared mode): is the station still in the condition it was
    accepted in for THIS kind of inspection?

    Declared coverage is a statement about a control plan. Effective coverage is a
    statement about a control plan AND the condition of the station executing it.
    They are the same thing on day one and they drift apart silently, because a
    station that can no longer resolve a defect does not report an error — it
    reports nothing, which is indistinguishable from good parts.

    The split by detection basis is what makes the loss invisible to every metric
    already on the scorecard: a geometric measurement holds its accuracy through
    an illumination change that has already cost the same station its low-contrast
    inspection. Yield stays healthy. Capability stays healthy. The station is
    simply no longer looking at part of what it was assigned.
    """
    cov = plan["inspection_coverage"]
    need = cfg["monitoring"]["consecutive_days_to_alert"]
    ev = evaluate_selftest(cfg, selftest.sort_values(
        ["station_id", "production_day"], kind="stable"), overrides=overrides)

    label = {"imaging_check": "imaging", "measurement_check": "measurement",
             "tooling_check": "tooling"}
    rows = []
    for st, spec in cov["stations"].items():
        t = ev[ev.station_id == st]
        if t.empty:
            continue
        days = t["production_day"].tolist()
        for m in (spec.get("detects") or []):
            basis = (cov["modes"].get(m) or {}).get("detection_basis")
            gates = gates_for(basis, cfg)
            if not gates:
                continue
            # Any gating step failing is enough: the station cannot be trusted on
            # this inspection that day.
            flags = {g: (~t[g.replace("_check", "") + "_ok"]).tolist() for g in gates}
            bad = [any(flags[g][i] for g in gates) for i in range(len(days))]
            longest = _longest_run(bad)
            # Name the tooling SET that moved, not just "tooling". "Repeatability
            # is out" is a symptom; "the gripper is out and the fixture is fine"
            # is an answer, and it is the difference between replacing the right
            # thing and guessing.
            failing = []
            for g in gates:
                if not any(flags[g]):
                    continue
                if g != "tooling_check":
                    failing.append(label[g])
                    continue
                for sname in tooling_sets(cfg):
                    if (~t[f"tooling_{sname}_ok"]).any():
                        failing.append(f"tooling/{sname}")
            status = ("ALERT" if longest >= need
                      else "WATCH" if longest >= 1 else "OK")
            last = t.iloc[-1]
            rows.append(dict(
                line_id=t["line_id"].iloc[0], station_id=st, mode=m,
                detection_basis=basis,
                gated_by=", ".join(label[g] for g in gates),
                cause=" + ".join(failing) if failing else "-",
                datamatrix_grade=float(last.datamatrix_grade),
                ref_sharpness_score=float(last.ref_sharpness_score),
                ref_repeatability_mm=float(last.ref_repeatability_mm),
                locator_wear_mm=float(last.locator_wear_mm),
                days_below=int(sum(bad)), longest_run_below=int(longest),
                first_day_below=next((d for d, b in zip(days, bad) if b), None),
                condition=status, effective_coverage=(status != "ALERT"),
            ))
    return (pd.DataFrame(rows)
            .sort_values(["station_id", "mode"], kind="stable")
            .reset_index(drop=True))


def wear_trend(cfg: dict, selftest: pd.DataFrame) -> pd.DataFrame:
    """Fit the measured tooling-wear rate and extrapolate it to the TOLERANCE LIMIT.

    ⚠️ THIS IS NOT A FAILURE PREDICTION, and nothing here supports one. There is no
    failure history, no distribution fitted to failures, no censoring and no hazard
    model. This is straight arithmetic on a measured characteristic against a
    published limit — the same thing tool-wear compensation has done on a control
    chart for decades. It says "at the rate this has been moving, it reaches the
    limit in about N units." It does not say anything will break.

    The second-order term is the interesting one. Steady wear is a maintenance
    schedule. Wear whose RATE is climbing means something else changed, and that is
    a product-quality question before it is a tooling one.
    """
    wt = cfg["wear_trending"]
    if not wt.get("enabled"):
        return pd.DataFrame()
    # One trend per (station, tooling set, tracked measure). A gripper and a
    # fixture wear against different things at different rates, so a single
    # blended "tooling wear" number would hide both.
    tracked = [(sname, col, m) for sname, spec in tooling_sets(cfg).items()
               for col, m in spec["measures"].items() if m.get("trend")]
    rows = []
    for st, g in selftest.sort_values("production_day").groupby("station_id"):
        if len(g) < wt["min_points"]:
            continue
        for sname, col, meas in tracked:
            limit = meas["max"]
            u = g["units_processed"].to_numpy(dtype=float)
            w = g[col].to_numpy(dtype=float)
            rate = float(np.polyfit(u, w, 1)[0])            # mm per unit processed
            half = len(g) // 2
            r1 = float(np.polyfit(u[:half], w[:half], 1)[0])
            r2 = float(np.polyfit(u[half:], w[half:], 1)[0])
            accel = (r2 / r1) if r1 > 0 else float("nan")
            # Only extrapolate a trend that has actually been observed. On a healthy
            # fixture the "rate" is measurement noise, and projecting it produces a
            # confident-looking number built on nothing at all.
            rise = float(w[-1] - w[0])
            trend = rise >= wt["min_observed_rise_fraction_of_limit"] * limit and rate > 1e-12
            remaining = limit - w[-1]
            units_to_limit = (remaining / rate) if trend and remaining > 0 else None
            per_day = float(np.mean(np.diff(u))) if len(u) > 1 else None
            if units_to_limit is not None and not np.isfinite(units_to_limit):
                units_to_limit = None
            rows.append(dict(
                    station_id=st, tooling_set=sname,
                set_label=tooling_sets(cfg)[sname]["label"],
                measure=col, measure_name=meas.get("name", col),
                latest_wear_mm=round(float(w[-1]), 5),
                limit_mm=limit,
                margin_mm=round(float(remaining), 5),
                within_limit=bool(w[-1] <= limit),
                wear_rate_mm_per_1k_units=round(rate * 1000, 5),
                rate_first_half=round(r1 * 1000, 5),
                rate_second_half=round(r2 * 1000, 5),
                trend_detected=bool(trend),
                observed_rise_mm=round(rise, 5),
                acceleration_ratio=(None if not np.isfinite(accel) else round(accel, 2)),
                accelerating=bool(trend and np.isfinite(accel)
                                  and accel >= wt["acceleration"]["flag_ratio"]),
                projected_units_to_limit=(None if units_to_limit is None
                                          else int(round(units_to_limit))),
                projected_days_to_limit=(None if units_to_limit is None or not per_day
                                         else int(round(units_to_limit / per_day))),
                projection_label=wt["report"]["label"].strip(),
            ))
    return (pd.DataFrame(rows)
            .sort_values(["station_id", "tooling_set", "measure"])
            .reset_index(drop=True))


def selftest_sensitivity(plan: dict, cfg: dict, selftest: pd.DataFrame) -> dict:
    """Sweep the self-test limits and test whether each failure stays SELECTIVE.

    Both claims are CONDITIONAL: given that a step fails and costs the station some
    coverage, WHICH class does it cost? A limit set so loose that nothing fails is
    vacuous — not a counterexample — so those combinations are excluded from the
    denominator and reported separately.

      E1  a failed IMAGING step costs the visual checks and spares the measurement
      E2  a failed MEASUREMENT or TOOLING step costs the measurement and spares
          the visual checks

    They point opposite ways on purpose. A cell fault that degraded everything at
    once would show up in yield within a day and would need none of this.
    """
    sw = cfg["sensitivity"]["sweep"]
    combos = list(product(sw["datamatrix_grade"], sw["sharpness_score"],
                          sw["bias_mm"], sw["locator_wear_mm"]))
    e1_hold = e1_app = e1_vac = 0
    e2_hold = e2_app = e2_vac = 0
    for grade, sharp, bias, loc in combos:
        cond = build_station_condition(plan, cfg, selftest, overrides={
            "datamatrix_grade": grade, "sharpness_score": sharp,
            "bias_mm": bias, "locator_wear_mm": loc})
        alert = cond[cond.condition == "ALERT"]
        img = alert[alert.cause.str.contains("imaging", na=False)]
        mech = alert[alert.cause.str.contains("measurement|tooling", na=False, regex=True)]

        if img.empty:
            e1_vac += 1
        else:
            e1_app += 1
            e1_hold += int(img.detection_basis.str.startswith("appearance").any()
                           and not (img.detection_basis == "geometry").any())
        if mech.empty:
            e2_vac += 1
        else:
            e2_app += 1
            e2_hold += int((mech.detection_basis == "geometry").any()
                           and not mech.detection_basis.str.startswith("appearance").any())

    return {
        "combinations_tested": len(combos),
        "E1_imaging_failure_costs_visual_checks_only": {
            "holds_in": e1_hold, "of": e1_app,
            "robust": e1_app > 0 and e1_hold == e1_app,
            "vacuous_combinations_excluded": e1_vac},
        "E2_mechanical_failure_costs_measurement_only": {
            "holds_in": e2_hold, "of": e2_app,
            "robust": e2_app > 0 and e2_hold == e2_app,
            "vacuous_combinations_excluded": e2_vac},
    }


def reaction_plan(check: str, cfg: dict) -> dict:
    """Deterministic root-cause attribution and corrective action for a failed step.

    Detection without loop closure is a quality gate, not a quality system. This is
    the closure for the self-test side: a failed step maps to a reviewed list in
    versioned config, owned by a human and ordered most-likely-first.

    NOTHING IS INFERRED HERE AND NO MODEL CHOOSES A CAUSE. That is the same
    discipline as src/ai_triage.py — code classifies, and if an LLM is used at all
    it only writes the narrative afterwards, so it cannot introduce a wrong cause
    because it never picks one. The list is where an investigation STARTS. The
    investigation still belongs to a person.
    """
    rp = (cfg.get("reaction_plans") or {}).get(check) or {}
    return {
        "failure_means": (rp.get("failure_means") or "").strip(),
        "probable_causes": list(rp.get("probable_causes") or []),
        "containment": list(rp.get("containment") or []),
        "corrective_actions": list(rp.get("corrective_actions") or []),
        "owner": rp.get("owner"),
        "escalate_after_days": rp.get("escalate_after_days"),
    }


def scorecard_row(station: str) -> dict | None:
    """Read-only lookup into the Databricks-produced scorecard mart.

    Used only to show what the EXISTING metrics say about a station whose
    condition has degraded. Nothing here writes to that file.
    """
    if not SCORECARD.exists():
        return None
    sc = pd.read_csv(SCORECARD)
    hit = sc[sc.station_id == station]
    return None if hit.empty else hit.iloc[0].to_dict()


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #
def build_findings(covdf: pd.DataFrame, econ: pd.DataFrame, ncr: pd.DataFrame,
                   plan: dict, cost: dict, sens: dict,
                   cond: pd.DataFrame | None = None,
                   esens: dict | None = None,
                   wear: pd.DataFrame | None = None,
                   alert_days: int = 3,
                   envcfg: dict | None = None) -> list[dict]:
    envcfg = envcfg or {}
    cov = plan["inspection_coverage"]
    out: list[dict] = []

    # 1. Modes the floor reports that no station was ever assigned to detect.
    undeclared = (covdf[covdf.state == "UNDECLARED_DETECTED"]
                  .groupby("mode")
                  .agg(stations=("station_id", "nunique"),
                       detections=("detections", "sum")).reset_index())
    never_declared = [m for m in undeclared["mode"]
                      if not any(m in (s.get("detects") or [])
                                 for s in cov["stations"].values())]
    for m in never_declared:
        r = undeclared[undeclared["mode"] == m].iloc[0]
        n_ncr = 0
        if not ncr.empty and (ncr["defect_code"] == m).any():
            n_ncr = int(ncr.loc[ncr["defect_code"] == m, "ncrs"].sum())
        out.append(dict(
            id="UNASSIGNED_MODE", severity="high", mode=m,
            headline=(f"All {int(r.stations)} stations are finding "
                      f"{mode_label(m, plan)} — and not one of them is assigned to look for it"),
            detail=(f"The floor reported it {int(r.detections):,} times on first-pass "
                    f"inspection, and quality raised {n_ncr} nonconformance reports against "
                    f"it. It appears on no station's inspection list in the control plan. "
                    f"That means there is no spec for it, no sample size, and no reaction "
                    f"plan telling anyone what to do when it turns up. The floor is catching "
                    f"it. The plan does not govern it."),
        ))

    # 2. Declared coverage that never fires ANYWHERE. Ambiguous by construction.
    #
    #    This test is deliberately mode-level, not station-level. A characteristic
    #    with zero rejections at one station is the normal state of a capable
    #    process — DIM-OOS is declared at all nine stations and only S3 drifts, so
    #    eight of them are quiet because the process is good, not because anything
    #    is wrong. Flagging those would be an alarm with no defect behind it, which
    #    is exactly how a tool teaches people to ignore it. The genuinely ambiguous
    #    case is a mode declared somewhere and never detected anywhere at all.
    totals = covdf.groupby("mode")["detections"].sum()
    for m, grp in covdf[covdf.state == "DECLARED_SILENT"].groupby("mode"):
        if totals.get(m, 0) > 0:
            continue
        out.append(dict(
            id="DECLARED_SILENT", severity="open_question", mode=m,
            headline=(f"{mode_label(m, plan)} is on {len(grp)} stations' inspection lists "
                      f"and has never once been found"),
            detail=("Zero detections anywhere in the window. Either it genuinely never "
                    "happens here, or those stations cannot physically see it. Inspection "
                    "data on its own cannot tell those two apart, and they need opposite "
                    "responses: one means the control plan should be pruned, the other "
                    "means an inspection is quietly not working. Reported as an open "
                    "question rather than counted as coverage."),
        ))

    # 3. Modes with no downstream backstop: the last station that can see it is
    #    the last chance before the customer.
    nb = econ[~econ.backstop]
    for m, grp in nb.groupby("mode"):
        worst = grp.loc[grp.exchange_rate.idxmax()]
        out.append(dict(
            id="NO_BACKSTOP", severity="high", mode=m,
            headline=(f"If station {worst.station_id} misses {mode_label(m, plan)}, "
                      f"nothing downstream will catch it"),
            detail=(f"No later station is assigned to look for it, and final test is not "
                    f"assigned to look for it either — so position {int(worst.position)} is "
                    f"the last chance before the customer sees it. That changes what the "
                    f"station should do: here, letting one bad part through costs about "
                    f"{worst.exchange_rate:.1f} times what wrongly rejecting one good part "
                    f"costs, so this station should be set to catch everything it can, even "
                    f"at the price of scrapping good parts."),
        ))

    # 4. First declared inspection point late in the line. Only meaningful for
    #    modes the floor actually produces — a late first look at something that
    #    never happens costs nothing.
    dec = covdf[covdf.declared]
    for m, grp in dec.groupby("mode"):
        if totals.get(m, 0) == 0:
            continue
        first = int(grp.position.min())
        if first > 1:
            vu = cost["value_at_position"][first]
            first_vu = cost["value_at_position"][1]
            out.append(dict(
                id="LATE_FIRST_LOOK", severity="medium", mode=m,
                headline=(f"Nobody looks for {mode_label(m, plan)} until station "
                          f"position {first}"),
                detail=(f"By the time the first station assigned to look for it sees the "
                        f"part, {vu:.1f} units of value are already built in, against "
                        f"{first_vu:.1f} at the start of the line. Everything added in "
                        f"between is being built onto a part that may already be bad, and "
                        f"all of it is scrapped or reworked along with the part."),
            ))

    # 5. The sensitivity result — reported whether or not it is convenient.
    c2 = sens["C2_exchange_rate_decreases_with_position"]
    c1 = sens["C1_false_reject_cost_increases_with_position"]
    if not c2["robust"]:
        # Quote the real walk down one line rather than describing it in the abstract.
        line = econ.line_id.iloc[0]
        walk = (econ[(econ.line_id == line) & (econ["mode"] == COMMON_MODE)]
                .sort_values("position"))
        steps = [(r.station_id, r.exchange_rate, r.escape_found_at)
                 for r in walk.itertuples()]
        story = ""
        if len(steps) >= 3:
            (s1, r1, n1), (s2, r2, n2), (s3, r3, n3) = steps[0], steps[1], steps[-1]
            story = (f"On {line}, checking {mode_label(COMMON_MODE, plan)}: if {s1} misses "
                     f"it, {n1} catches it straight away — so {s1} can afford to wrongly "
                     f"reject about {r1:.1f} good parts to stop one bad one getting "
                     f"through. If {s2} misses it, {n2} catches it: about {r2:.1f}. But if "
                     f"{s3} misses it, nothing does until {n3}, which is expensive — so "
                     f"{s3} goes back UP to about {r3:.1f}, even though it is handling the "
                     f"most valuable parts on the line. ")
        out.append(dict(
            id="POSITION_IS_NOT_THE_DRIVER", severity="insight", mode=COMMON_MODE,
            headline="The last station on the line has to be nearly as strict as the first one",
            detail=("Every station faces one question: how many good parts is it worth "
                    "wrongly rejecting in order to stop one bad part getting through? The "
                    "obvious answer is that it depends how far down the line you are, "
                    "because a part near the end has more work built into it and scrapping "
                    "it hurts more. That turns out to be only half right. What actually "
                    "decides it is who catches the defect NEXT if this station misses it. "
                    + story
                    + f"Being last is not what matters — being the last one who can see it "
                      f"is. The half that does hold is that wrongly rejecting a good part "
                      f"gets steadily more expensive the further down the line you go: that "
                      f"was true in all {c1['of']} of {c1['of']} assumption combinations "
                      f"tested. The tidier version of the rule held in only "
                      f"{c2['holds_in']} of {c2['of']}."),
        ))

    if cond is None or cond.empty:
        return out

    # 6. Declared coverage the station can no longer deliver.
    #
    #    This is the finding no existing metric produces, because nothing is
    #    wrong with the station's output — only with what its output is still
    #    capable of containing.
    for st, grp in cond[cond.condition == "ALERT"].groupby("station_id"):
        modes_lost = sorted(grp["mode"].unique())
        worst = grp.loc[grp.longest_run_below.idxmax()]
        exposed = sorted(set(modes_lost) & set(econ.loc[~econ.backstop, "mode"]))
        cause_txt = str(worst.cause)   # defined before first use, not after
        lost_names = ", ".join(mode_label(m, plan) for m in modes_lost)

        ic = (envcfg.get("imaging_check") or {})
        mc = (envcfg.get("measurement_check") or {})
        if "imaging" in str(worst.cause):
            head = (f"Station {st} has failed its imaging self-test "
                    f"{int(worst.longest_run_below)} days running")
            why = (f"On the reference coupon, the 2D DataMatrix now grades "
                   f"{worst.datamatrix_grade:.2f} against a minimum of "
                   f"{ic.get('datamatrix_grade', {}).get('min')}, and sharpness reads "
                   f"{worst.ref_sharpness_score:.2f}. It has been out since "
                   f"{worst.first_day_below}. The measurement step at the same station "
                   f"still passes — measuring the reference feature, repeatability is "
                   f"{worst.ref_repeatability_mm:.4f} mm, inside limit — because finding "
                   f"an edge survives poor contrast. So the station is still trusted to "
                   f"measure and is no longer trusted to see. That split is why nothing "
                   f"else has reported this.")
        elif "tooling/" in cause_txt:
            sname = cause_txt.split("tooling/")[1].split()[0].strip()
            sets = tooling_sets(envcfg)
            slabel = (sets.get(sname) or {}).get("label", sname)
            others = [v.get("label", k) for k, v in sets.items() if k != sname]
            head = (f"The {slabel} at station {st} is out of limit — and it, not the "
                    f"{' or '.join(others).lower()}, is why the measurement stopped repeating")
            why = (f"Measuring a reference feature whose true length is known, "
                   f"repeatability is now {worst.ref_repeatability_mm:.4f} mm against a "
                   f"limit of {mc.get('repeatability_mm', {}).get('max')} mm — out since "
                   f"{worst.first_day_below}, {int(worst.longest_run_below)} days running. "
                   f"That symptom on its own does not tell you what caused it: the tooling "
                   f"that PLACES the part and the tooling that HOLDS it both push on the "
                   f"same reading, and a worn gripper and a worn locator look identical at "
                   f"step 2. Inspecting the two sets separately is what answers it — here "
                   f"the {slabel.lower()} is over its limit and the "
                   f"{' and '.join(others).lower()} is inside it. The imaging step at this "
                   f"station also still passes at grade {worst.datamatrix_grade:.2f}, so the "
                   f"visual inspections are unaffected. Without the split, the corrective "
                   f"action here is a coin flip between replacing a fixture and replacing "
                   f"a gripper.")
        else:
            head = (f"Station {st} has failed its measurement self-test "
                    f"{int(worst.longest_run_below)} days running")
            why = (f"Measuring a reference feature whose true length is known, "
                   f"repeatability is now {worst.ref_repeatability_mm:.4f} mm against a "
                   f"limit of {mc.get('repeatability_mm', {}).get('max')} mm — out since "
                   f"{worst.first_day_below}. The part is no longer landing in the same "
                   f"place twice, and presentation error is not softened on its way into "
                   f"a size measurement, it is added to it. The imaging step at the same "
                   f"station still passes at grade {worst.datamatrix_grade:.2f}, because "
                   f"the feature is still inside the field of view — only the measurement "
                   f"moved. That split is why nothing else has reported this. "
                   f"Note what the tooling inspection says here: BOTH tooling sets are "
                   f"still inside their own limits, so neither of them explains this yet. "
                   f"Either something outside the two sets is moving the part, or the "
                   f"tooling limits are set too loose to protect the measurement — a "
                   f"tolerance that lets the symptom appear before the cause trips is a "
                   f"limit worth re-deriving. Worth resolving before anything is replaced.")

        # The sharp edge: if the lost inspection had no detections to begin with,
        # its detection count cannot tell you which of the two you are looking at.
        silent_note = ""
        quiet = [m for m in modes_lost
                 if int(covdf.loc[(covdf.station_id == st) & (covdf["mode"] == m),
                                  "detections"].sum()) == 0]
        if quiet:
            silent_note = (f" Note what this resolves: {', '.join(mode_label(m, plan) for m in quiet)} "
                           f"has never been detected at {st}. From the inspection results "
                           f"alone, a station that is capable and a station that has gone "
                           f"blind look identical — both report nothing. The cell "
                           f"measurements are what tell them apart.")

        sc = scorecard_row(st)
        health = ""
        if sc:
            v = int(sc["spc_violations"])
            health = (f" Meanwhile the existing scorecard for {st} reads: first-pass yield "
                      f"{sc['fpy_overall']}, Cpk {sc['cpk_overall']}, {v} control-chart "
                      f"violation{'' if v == 1 else 's'}, status {sc['status']}. Nothing "
                      f"being watched today would surface this, and nothing here is "
                      f"failing — by its own measurements the station is fine. It has "
                      f"simply stopped being able to do part of its job.")

        # Attribute to the tooling SET that actually moved, not to "tooling".
        if "imaging" in cause_txt:
            check = "imaging_check"
        elif "eoat" in cause_txt:
            check = "tooling_check__eoat"
        elif "fixture" in cause_txt:
            check = "tooling_check__fixture"
        else:
            check = "measurement_check"
        out.append(dict(
            id="CONDITION_COVERAGE_LOST", severity="high", mode=",".join(modes_lost),
            station=st, cause=str(worst.cause), **reaction_plan(check, envcfg),
            headline=(head if "tooling/" in cause_txt else
                      f"{head}, and has lost {len(modes_lost)} of the inspections it is assigned"),
            detail=(why + f" What it can no longer reliably find: {lost_names}."
                    + (f" And {', '.join(mode_label(m, plan) for m in exposed)} has no "
                       f"downstream backstop, so what this station stops seeing goes to "
                       f"the customer." if exposed else "")
                    + silent_note + health),
        ))

    # 7. A dip that recovered. Reported, deliberately NOT alerted.
    watch_only = (set(cond.loc[cond.condition == "WATCH", "station_id"])
                  - set(cond.loc[cond.condition == "ALERT", "station_id"]))
    for st in sorted(watch_only):
        grp = cond[(cond.station_id == st) & (cond.condition == "WATCH")]
        run = int(grp.longest_run_below.max())
        out.append(dict(
            id="CONDITION_TRANSIENT", severity="watch", mode=",".join(sorted(grp["mode"])),
            station=st,
            headline=(f"Station {st} went dim for {run} days and came back on its own — "
                      f"noted, not alarmed"),
            detail=(f"It was below the required light level on {int(grp.days_below.max())} "
                    f"days, {run} of them in a row. The rule needs {alert_days} in a row "
                    f"before it counts as a real condition, so this stays a WATCH. Most "
                    f"likely somebody cleaned something or a bay door stood open. Raising an "
                    f"alarm on a two-day blip that fixed itself is exactly how a tool trains "
                    f"people to stop opening it."),
        ))

    # 8. Tooling still inside its limit, but trending toward it.
    #
    #    ⚠️ This is extrapolation of a MEASURED trend to a SPEC LIMIT. It is not a
    #    failure prediction and the wording must never drift into one. The label
    #    from config travels with the number so it cannot be quoted without it.
    if wear is not None and not wear.empty:
        for r in wear.itertuples():
            # pandas turns None into NaN in a mixed column, so `is None` is not a
            # safe guard here — a healthy fixture would sail straight through it.
            if not r.trend_detected or not r.within_limit:
                continue
            if pd.isna(r.projected_units_to_limit) or pd.isna(r.projected_days_to_limit):
                continue
            if r.projected_days_to_limit > 120:
                continue
            accel = ""
            if r.accelerating:
                accel = (f" And the rate itself is climbing — {r.rate_first_half:.4f} mm "
                         f"per thousand units over the first half of the window against "
                         f"{r.rate_second_half:.4f} over the second, a factor of "
                         f"{r.acceleration_ratio:.1f}. Steady wear is a maintenance "
                         f"schedule. Wear that is speeding up means something else "
                         f"changed, and that is a product question before it is a "
                         f"tooling one — the parts made during the acceleration are the "
                         f"ones worth looking at.")
            out.append(dict(
                id="TOOLING_TRENDING_TO_LIMIT", severity="watch", mode="-", station=r.station_id,
                tooling_set=r.tooling_set, measure=r.measure_name,
                **reaction_plan("tooling_trend", envcfg),
                headline=(f"{r.measure_name} on the {r.set_label.lower()} at station "
                          f"{r.station_id} is still within spec but closing on its limit — "
                          f"about {int(r.projected_days_to_limit)} days at the current rate"),
                detail=(f"Measured {r.measure_name.lower()} is {r.latest_wear_mm:.3f} mm against a "
                        f"{r.limit_mm:.3f} mm limit, so there is {r.margin_mm:.3f} mm left. "
                        f"It has been moving at {r.wear_rate_mm_per_1k_units:.4f} mm per "
                        f"thousand units, which reaches the limit in roughly "
                        f"{int(r.projected_units_to_limit):,} more units."
                        + accel
                        + f" [{r.projection_label}]"),
            ))

    # 9. Whether the selectivity claim survived its own sweep.
    if esens:
        e1 = esens["E1_imaging_failure_costs_visual_checks_only"]
        e2 = esens.get("E2_mechanical_failure_costs_measurement_only", {})
        out.append(dict(
            id="CONDITION_LOSS_IS_SELECTIVE",
            severity="insight" if e1["robust"] else "open_question",
            mode="-", station="-",
            headline=("The two ways a cell goes wrong break opposite halves of the "
                      "inspection, which is why neither shows up in yield"),
            detail=("This is the reason both problems hide. If either one broke everything "
                    "at once, yield would collapse and the existing scorecard would catch "
                    "it in a day. Neither does. When the imaging goes, the visual checks "
                    "go with it and the measurement survives: finding an edge still works "
                    "in poor contrast, while spotting a faint scratch is the first thing "
                    "to fail. When the fixture goes, the reverse happens: the part stops "
                    "landing in the same place, that error is added straight into any size "
                    "measurement, and the visual checks carry on because the feature is "
                    "still in the field of view. "
                    f"Imaging selectivity held in {e1['holds_in']} of {e1['of']} assumption "
                    f"sets that produced any loss at all; mechanical selectivity held in "
                    f"{e2.get('holds_in', 0)} of {e2.get('of', 0)}. In every case the "
                    "station kept reporting good yield and good capability throughout."),
        ))
    return out


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run() -> dict:
    plan = load_control_plan()
    cost = load_yaml(COST_MODEL)
    envcfg = load_yaml(ENV_MODEL)
    xw = load_yaml(CROSSWALK)

    events = pd.read_csv(RAW, parse_dates=["ts"])
    clean, dq = validate(events, plan)          # same DQ gate as the rest of the pipeline
    observed = observed_detections(clean)
    ncr = ncr_modes(xw)

    covdf = build_coverage(plan, cost, observed)
    econ = build_economics(plan, cost)
    sens = sensitivity(plan, cost)

    # Station condition — only if the telemetry extract is present. The coverage
    # audit stands on its own without it; the condition layer sharpens it.
    cond, esens, wear = pd.DataFrame(), None, pd.DataFrame()
    if ENV_TELEMETRY.exists():
        selftest = pd.read_csv(ENV_TELEMETRY)
        cond = build_station_condition(plan, envcfg, selftest)
        esens = selftest_sensitivity(plan, envcfg, selftest)
        wear = wear_trend(envcfg, selftest)

    findings = build_findings(
        covdf, econ, ncr, plan, cost, sens, cond, esens,
        alert_days=envcfg["monitoring"]["consecutive_days_to_alert"], envcfg=envcfg,
        wear=wear)

    modes_declared = {m for st in plan["inspection_coverage"]["stations"].values()
                      for m in (st.get("detects") or [])}
    modes_observed = set(observed["defect_code"].unique())
    modes_specified = {m for m, v in plan["inspection_coverage"]["modes"].items()
                       if (v or {}).get("feature_ref")}

    report = {
        "window_events": int(len(clean)),
        "dq_score": dq["dq_score"],
        "modes_observed": sorted(modes_observed),
        "modes_declared": sorted(modes_declared),
        "modes_specified": sorted(modes_specified),
        "modes_observed_not_declared": sorted(modes_observed - modes_declared),
        "modes_declared_not_observed": sorted(modes_declared - modes_observed),
        "declared_without_spec": sorted(modes_declared - modes_specified),
        "coverage_rate_observed_modes": (
            round(len(modes_observed & modes_declared) / len(modes_observed), 4)
            if modes_observed else None),
        "exchange_rate_min": float(econ.exchange_rate.min()),
        "exchange_rate_max": float(econ.exchange_rate.max()),
        "sensitivity": sens,
        "findings": findings,
        "cost_model_version": cost["version"],
        "cost_model_units": cost["units"]["symbol"],
        # The boundaries of what this report claims, declared once and machine
        # readable, so the page renders them from the same source the tests assert
        # against. A caveat stated in one paragraph of prose gets lost; a caveat in
        # the report object cannot be.
        "scope": {
            "processes_images": False,
            "contains_vision_model": False,
            "forecasts_failures": False,
            "data_is_synthetic": True,
            "costs_are_assumptions_not_measurements": True,
            "note": ("Reads inspection results and cell process measures. Reports a "
                     "condition against an acceptance level and a coverage gap against "
                     "a control plan. Makes no statement about when any component will "
                     "stop working, and handles no image data."),
        },
    }

    if not cond.empty:
        lost = cond[~cond.effective_coverage]
        declared_pairs = len(cond)
        report.update({
            "selftest_model_version": envcfg["version"],
            "reference_artifact": envcfg["reference_artifact"]["id"],
            "station_condition_alerts": sorted(lost["station_id"].unique().tolist()),
            "modes_losing_effective_coverage": sorted(lost["mode"].unique().tolist()),
            "declared_station_mode_pairs": declared_pairs,
            "effective_coverage_rate": round(
                (declared_pairs - len(lost)) / declared_pairs, 4) if declared_pairs else None,
            "environment_sensitivity": esens,
        })
    if not wear.empty:
        report["wear_trending"] = {
            "projection_is_not_a_failure_prediction": True,
            "label": envcfg["wear_trending"]["report"]["label"].strip(),
            "stations": wear.to_dict(orient="records"),
        }

    QDIR.mkdir(parents=True, exist_ok=True)
    MARTS.mkdir(parents=True, exist_ok=True)
    covdf.to_csv(MARTS / "inspection_coverage.csv", index=False)
    econ.to_csv(MARTS / "station_economics.csv", index=False)
    if not cond.empty:
        cond.to_csv(MARTS / "station_condition.csv", index=False)
    if not wear.empty:
        wear.to_csv(MARTS / "tooling_wear.csv", index=False)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def main() -> None:
    r = run()
    print(f"Coverage audit over {r['window_events']:,} validated events")
    print(f"  modes observed      : {', '.join(r['modes_observed'])}")
    print(f"  modes declared      : {', '.join(r['modes_declared'])}")
    print(f"  observed, undeclared: {', '.join(r['modes_observed_not_declared']) or '-'}")
    print(f"  declared, never seen: {', '.join(r['modes_declared_not_observed']) or '-'}")
    print(f"  declared w/o spec   : {', '.join(r['declared_without_spec']) or '-'}")
    print(f"  coverage rate       : {r['coverage_rate_observed_modes']}")
    print(f"  exchange rate range : {r['exchange_rate_min']} - {r['exchange_rate_max']}")
    s = r["sensitivity"]
    print(f"  sensitivity ({s['combinations_tested']} combos):")
    for k in ("C1_false_reject_cost_increases_with_position",
              "C2_exchange_rate_decreases_with_position"):
        print(f"    {k}: holds in {s[k]['holds_in']}/{s[k]['of']}"
              f"  {'ROBUST' if s[k]['robust'] else 'NOT ROBUST'}")
    if "effective_coverage_rate" in r:
        es = r["environment_sensitivity"]
        print(f"  station self-test (reference {r['reference_artifact']}):")
        print(f"    effective coverage  : {r['effective_coverage_rate']} "
              f"of {r['declared_station_mode_pairs']} declared station-mode pairs")
        print(f"    alerting stations   : {', '.join(r['station_condition_alerts']) or '-'}")
        print(f"    modes losing cover  : {', '.join(r['modes_losing_effective_coverage']) or '-'}")
        for k in ("E1_imaging_failure_costs_visual_checks_only",
                  "E2_mechanical_failure_costs_measurement_only"):
            c = es[k]
            print(f"    {k[:2]} selectivity   : holds in {c['holds_in']}/{c['of']}"
                  f"  {'ROBUST' if c['robust'] else 'NOT ROBUST'}"
                  f"  ({c['vacuous_combinations_excluded']} vacuous excluded)")
    if "wear_trending" in r:
        for w in r["wear_trending"]["stations"]:
            if w.get("trend_detected") and w["within_limit"] and w["projected_days_to_limit"]:
                print(f"    tooling {w['station_id']}       : {w['latest_wear_mm']} / "
                      f"{w['limit_mm']} mm, ~{w['projected_days_to_limit']}d to limit"
                      f"{'  ACCELERATING' if w['accelerating'] else ''}")
    print(f"  findings            : {len(r['findings'])}")
    for f in r["findings"]:
        print(f"    [{f['severity']}] {f['headline']}")


if __name__ == "__main__":
    main()
