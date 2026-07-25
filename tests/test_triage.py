"""The AI triage must correctly read the two engineered failure signatures and the
data-quality problem — end to end, from generation through the pipeline."""
from __future__ import annotations

from src import ai_triage, generate_data, pipeline_pandas


def _run():
    generate_data.generate()
    pipeline_pandas.run()      # writes the marts + findings the triage reads
    return ai_triage.run()


def test_triage_reads_drift_and_shift():
    t = _run()
    by = {s["station"]: s for s in t["stations"]}
    # S3 = gradual dimensional drift (capability loss)
    assert by["S3"]["pattern"] == "dimensional_drift"
    assert by["S3"]["severity"] == "high"
    assert by["S3"]["evidence"]["dim_trend"]["toward"] == "USL"
    # S5 = discrete process shift (SPC, capability intact)
    assert by["S5"]["pattern"] == "process_shift"
    assert by["S5"]["severity"] == "high"
    # LINE-C is healthy — no high-severity station there
    assert all(by[s]["severity"] != "high" for s in ("S7", "S8", "S9"))


def test_triage_flags_data_quality():
    t = _run()
    dq = t["data_quality"]
    assert dq["dq_score"] < 1.0
    assert dq["recommended_actions"]           # non-empty
