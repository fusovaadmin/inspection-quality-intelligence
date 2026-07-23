"""
Control-plan-driven data-quality validation.

Reads config/control_plan.yaml and enforces the SAME document the shop floor runs
to: required fields, unique keys, allowed values, physical value ranges, and
result/defect-code consistency. Emits a per-category violation report so the data
feeding the scorecard is provably trustworthy (JD: 'implement automated
data-quality checks... to ensure our quality data is provably trustworthy').

Returns (clean_df, report). clean_df has exact-duplicate event rows removed; the
report counts every violation by category and yields a 0-1 data-quality score.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "control_plan.yaml"
QDIR = ROOT / "data" / "quality"


def load_control_plan(path: Path = CONFIG) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate(df: pd.DataFrame, plan: dict | None = None, persist: bool = False):
    if plan is None:
        plan = load_control_plan()
    rules = plan["data_quality_rules"]
    viol: list[dict] = []

    def flag(mask: pd.Series, rule: str, detail: str) -> None:
        if mask.any():
            for eid in df.loc[mask, "event_id"].tolist():
                viol.append({"event_id": eid, "rule": rule, "detail": detail})

    # 1. required fields present
    for field in rules["required_fields"]:
        blank = df[field].isna() | (df[field].astype(str).str.strip() == "")
        flag(blank, "missing_required", field)

    # 2. unique keys
    for key in rules["unique_keys"]:
        flag(df[key].duplicated(keep=False), "duplicate_key", key)

    # 3. allowed values
    for col, allowed in rules.get("allowed_values", {}).items():
        flag(df[col].notna() & ~df[col].isin(allowed), "invalid_value", col)

    # 4. physical value ranges (catches sensor glitches)
    for col, rng in rules.get("value_ranges", {}).items():
        bad = df[col].notna() & ((df[col] < rng["min"]) | (df[col] > rng["max"]))
        flag(bad, "out_of_range", col)

    # 5. result / defect-code consistency
    flag((df["vision_result"] == "PASS") & df["defect_code"].notna(),
         "consistency_C1", "defect_code present on PASS")
    flag((df["vision_result"] == "FAIL") & df["defect_code"].isna(),
         "consistency_C2", "defect_code missing on FAIL")

    vdf = pd.DataFrame(viol, columns=["event_id", "rule", "detail"])
    total = len(df)
    offenders = vdf["event_id"].nunique() if not vdf.empty else 0
    report = {
        "total_events": int(total),
        "offending_events": int(offenders),
        "dq_score": round(1.0 - offenders / total, 4) if total else None,
        "by_rule": (vdf.groupby("rule").size().sort_values(ascending=False)
                    .to_dict() if not vdf.empty else {}),
    }

    clean = df.drop_duplicates(subset=["event_id"], keep="first").reset_index(drop=True)

    if persist:
        QDIR.mkdir(parents=True, exist_ok=True)
        vdf.to_csv(QDIR / "violations.csv", index=False)
        with open(QDIR / "dq_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    return clean, report
