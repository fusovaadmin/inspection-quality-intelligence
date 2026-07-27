"""
Renders the station quality scorecard as a single self-contained HTML file
(charts embedded as base64 PNGs -- no external assets, opens anywhere).

    KPI tiles  |  station status table  |  SPC p-chart  |  dimensional drift
               |  Cpk-by-station  |  rolling-FPY trend  |  defect Pareto  |  findings

Run:  python -m src.scorecard   (after src.pipeline_pandas)
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src.validation import load_control_plan

ROOT = Path(__file__).resolve().parents[1]
MARTS = ROOT / "data" / "marts"
RAW = ROOT / "data" / "raw" / "inspection_events.csv"
OUT = ROOT / "output" / "scorecard.html"

INK, ACCENT, ALERT, WATCH, OK = "#1a1d21", "#0f3d2e", "#b3261e", "#8a5a00", "#0f7a4f"
SHIFT_DATE = pd.Timestamp("2026-05-11")  # S5 engineered process shift (see generate_data)


def _img(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f'<img src="data:image/png;base64,{b64}" alt="chart">'


def _pchart(daily: pd.DataFrame, station: str) -> str:
    g = daily[daily.station_id == station].sort_values("date")
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ax.plot(g.date, g.defect_rate, "-o", ms=3, color=ACCENT, lw=1, label="daily defect rate")
    ax.plot(g.date, g.ucl, "--", color=ALERT, lw=1, label="UCL / LCL (3σ)")
    ax.plot(g.date, g.lcl, "--", color=ALERT, lw=1)
    ax.plot(g.date, g.pbar, "-", color="#888", lw=1, label="center (p̄)")
    oc = g[g.out_of_control]
    ax.scatter(oc.date, oc.defect_rate, color=ALERT, s=40, zorder=5, label="out of control")
    ax.axvline(SHIFT_DATE, color=WATCH, ls=":", lw=1.2)
    ax.annotate("process shift", (SHIFT_DATE, ax.get_ylim()[1]), color=WATCH,
                fontsize=8, ha="left", va="top")
    ax.set_title(f"SPC p-chart — station {station} (first-pass defect rate)", fontsize=10)
    ax.set_ylabel("defect rate"); ax.legend(fontsize=7, loc="upper left")
    ax.tick_params(labelsize=7); fig.autofmt_xdate()
    return _img(fig)


def _drift(raw: pd.DataFrame, station: str, plan: dict) -> str:
    f = plan["features"][0]
    fp = raw[(raw.inspection_pass == 1) & (raw.station_id == station)].copy()
    fp = fp[fp.feature_dim_mm.between(f["lsl_mm"] - 5, f["usl_mm"] + 5)]
    fp["date"] = fp.ts.dt.floor("D")
    daily_mean = fp.groupby("date")["feature_dim_mm"].mean()
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ax.plot(daily_mean.index, daily_mean.values, "-o", ms=3, color=ACCENT, lw=1,
            label="daily mean")
    ax.axhline(f["usl_mm"], color=ALERT, ls="--", lw=1, label="USL / LSL")
    ax.axhline(f["lsl_mm"], color=ALERT, ls="--", lw=1)
    ax.axhline(f["nominal_mm"], color="#888", ls="-", lw=0.8, label="nominal")
    ax.set_title(f"Dimensional drift — station {station} ({f['name']})", fontsize=10)
    ax.set_ylabel(f"{f['name']} (mm)"); ax.legend(fontsize=7, loc="lower right")
    ax.tick_params(labelsize=7); fig.autofmt_xdate()
    return _img(fig)


def _cpk_bar(sc: pd.DataFrame, cpk_min: float) -> str:
    color = {"ALERT": ALERT, "WATCH": WATCH, "OK": OK}
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ax.bar(sc.station_id, sc.cpk_overall, color=[color[s] for s in sc.status])
    ax.axhline(cpk_min, color=INK, ls="--", lw=1, label=f"Cpk min = {cpk_min}")
    ax.set_title("Process capability (Cpk) by station", fontsize=10)
    ax.set_ylabel("Cpk"); ax.legend(fontsize=7); ax.tick_params(labelsize=8)
    return _img(fig)


def _fpy_trend(daily: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    for st, g in daily.groupby("station_id"):
        g = g.sort_values("date")
        ax.plot(g.date, g.rolling_fpy, lw=1.2, label=st)
    ax.set_title("Rolling first-pass yield (7-day) by station", fontsize=10)
    ax.set_ylabel("rolling FPY"); ax.legend(fontsize=7, ncol=6, loc="lower left")
    ax.tick_params(labelsize=7); fig.autofmt_xdate()
    return _img(fig)


def _pareto(pareto: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ax.bar(pareto.defect_code, pareto["count"], color=ACCENT)
    ax.set_ylabel("count"); ax.tick_params(axis="x", labelrotation=30, labelsize=8)
    ax2 = ax.twinx()
    ax2.plot(pareto.defect_code, pareto.cum_pct, "-o", ms=3, color=WATCH)
    ax2.set_ylabel("cumulative %"); ax2.set_ylim(0, 105)
    ax.set_title("Defect Pareto (first-pass fails)", fontsize=10)
    return _img(fig)


def _tile(label: str, value: str, color: str = INK) -> str:
    return (f'<div class="tile"><div class="tv" style="color:{color}">{value}</div>'
            f'<div class="tl">{label}</div></div>')


def build() -> Path:
    plan = load_control_plan()
    cpk_min = plan["features"][0]["cpk_min"]
    daily = pd.read_csv(MARTS / "daily_fpy.csv", parse_dates=["date"])
    sc = pd.read_csv(MARTS / "station_scorecard.csv")
    pareto = pd.read_csv(MARTS / "pareto.csv")
    raw = pd.read_csv(RAW, parse_dates=["ts"])
    findings = json.loads((MARTS / "findings.json").read_text())

    overall_fpy = 1 - sc.defects.sum() / sc.n_first_pass.sum()
    n_alert = int((sc.status == "ALERT").sum())
    dq = findings["data_quality"]
    worst_cpk = sc.loc[sc.cpk_recent.idxmin()]

    color = {"ALERT": ALERT, "WATCH": WATCH, "OK": OK}
    rows = "".join(
        f'<tr><td><a class="linelink" href="dashboard.html#{r.line_id}"'
        f' title="Open {r.line_id} in the line-flow dashboard">{r.line_id}</a></td>'
        f'<td><a class="linelink" href="dashboard.html#{r.line_id}/{r.station_id}"'
        f' title="Open {r.line_id} · Station {r.station_id} in the dashboard">{r.station_id}</a></td>'
        f'<td>{r.n_first_pass:,}</td>'
        f'<td>{r.fpy_overall:.3f}</td><td>{r.rolling_fpy_latest:.3f}</td>'
        f'<td>{r.cpk_overall:.2f}</td><td>{r.cpk_recent:.2f}</td><td>{r.defects}</td>'
        f'<td>{r.spc_violations}</td>'
        f'<td><span class="pill" style="background:{color[r.status]}">{r.status}</span></td></tr>'
        for r in sc.itertuples())

    spc_txt = "; ".join(f"{a['station']} ({a['n_violations']} pts from {a['first_violation']})"
                        for a in findings["spc_alerts"]) or "none"
    cap_txt = "; ".join(f"{a['station']} Cpk {a['cpk_recent']} (min {a['cpk_min']})"
                        for a in findings["capability_alerts"]) or "none"

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inspection Quality Scorecard — {plan['part_number']}</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:{INK};
    margin:0;background:#eef1f3;line-height:1.5}}
  .wrap{{max-width:1040px;margin:0 auto;background:#fff;padding:26px 30px 40px;
    box-shadow:0 2px 18px rgba(0,0,0,.12)}}
  h1{{font-size:22px;margin:0 0 2px}} .sub{{color:{ACCENT};font-weight:600;margin:0 0 2px}}
  .meta{{color:#5b6470;font-size:12px;margin:0 0 18px}}
  h2{{font-size:15px;color:{ACCENT};border-bottom:2px solid {ACCENT};padding-bottom:4px;
    margin:26px 0 12px}}
  .tiles{{display:flex;gap:12px;flex-wrap:wrap}}
  .tile{{flex:1;min-width:150px;background:#f6f8f7;border:1px solid #d9dee5;border-radius:8px;
    padding:14px 16px}}
  .tv{{font-size:26px;font-weight:700}} .tl{{font-size:12px;color:#5b6470}}
  table{{border-collapse:collapse;width:100%;font-size:13px}}
  th,td{{border:1px solid #d9dee5;padding:6px 9px;text-align:center}}
  th{{background:{ACCENT};color:#fff;font-weight:600}} td:nth-child(2){{font-weight:700}}
  .pill{{color:#fff;padding:2px 9px;border-radius:11px;font-size:11px;font-weight:700}}
  .linelink{{color:{ACCENT};font-weight:600;text-decoration:none;
    border-bottom:1px dotted {ACCENT}}}
  .linelink:hover,.linelink:focus{{background:#eef5f1;border-bottom-style:solid;outline:none}}
  .prov{{display:flex;align-items:flex-start;gap:10px;background:#f6f8f7;
    border:1px solid #d9dee5;border-left:5px solid #FF3621;border-radius:0 6px 6px 0;
    padding:10px 14px;margin:0 0 18px;font-size:12px;color:#5b6470;line-height:1.5}}
  .provbadge{{flex:none;background:#FF3621;color:#fff;font-weight:700;font-size:11px;
    letter-spacing:.03em;padding:3px 9px;border-radius:4px}}
  .prov b{{color:#1a1d21}}
  .charts{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  .charts img{{width:100%;border:1px solid #eee;border-radius:6px}}
  .callout{{background:#eef5f1;border-left:5px solid {ACCENT};padding:11px 15px;
    border-radius:0 6px 6px 0;font-size:13px;margin:8px 0}}
  code{{background:#f0f0f0;padding:1px 5px;border-radius:4px}}
</style></head><body><div class="wrap">
  <h1>Inspection Quality Scorecard</h1>
  <p class="sub">Part {plan['part_number']} rev {plan['revision']} · {plan['program']} · automated vision inspection</p>
  <p class="meta">Synthetic demonstration data · window {daily.date.min():%Y-%m-%d} to {daily.date.max():%Y-%m-%d}
    · generated by a control-plan-driven pipeline (validation → SPC → capability)</p>

  <div class="prov"><span class="provbadge">Databricks</span>
    <span>Yield, SPC and capability figures on this page are rendered directly from
    <b>Databricks</b> Delta output — <code>quality.daily_fpy</code> and
    <code>quality.station_scorecard</code> — produced by a <b>PySpark</b> lakehouse pipeline
    (7-day rolling FPY via a window function; p-chart limits frozen on a 21-day baseline),
    verified cell-for-cell against the tested reference implementation.
    Defect Pareto and data-quality counts come from the same pipeline's validation stage.</span></div>

  <div class="tiles">
    {_tile("Overall first-pass yield", f"{overall_fpy:.1%}")}
    {_tile("Stations in ALERT", str(n_alert), ALERT if n_alert else OK)}
    {_tile("Data-quality score", f"{dq['dq_score']:.1%}", WATCH if dq['dq_score']<0.99 else OK)}
    {_tile(f"Lowest recent Cpk ({worst_cpk.station_id})", f"{worst_cpk.cpk_recent:.2f}",
           ALERT if worst_cpk.cpk_recent<cpk_min else OK)}
  </div>

  <h2>Station status</h2>
  <table><tr><th>Line</th><th>Station</th><th>First-pass n</th><th>FPY</th>
    <th>Rolling FPY</th><th>Cpk</th><th>Cpk (recent)</th><th>Defects</th>
    <th>SPC pts</th><th>Status</th></tr>{rows}</table>
  <div class="callout"><b>Automated findings.</b> SPC special-cause signals: {spc_txt}.
    Capability below plan minimum ({cpk_min}): {cap_txt}.
    Data quality: caught <b>{dq['offending_events']:,}</b> bad events of {dq['total_events']:,}
    ({', '.join(f'{k}={v}' for k,v in dq['by_rule'].items())}) before they reached the metrics.</div>

  <h2>Charts</h2>
  <div class="charts">
    {_pchart(daily, "S5")}
    {_drift(raw, "S3", plan)}
    {_cpk_bar(sc, cpk_min)}
    {_fpy_trend(daily)}
    {_pareto(pareto)}
  </div>
</div></body></html>"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    return OUT


def main() -> None:
    out = build()
    print(f"Wrote scorecard -> {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
