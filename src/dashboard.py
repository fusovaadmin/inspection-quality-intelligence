"""
Interactive, self-contained inspection-quality dashboard (one HTML file, no libs).

  * Line-flow view  — LINE-A (S1->S2->S3) and LINE-B (S4->S5->S6) as a process
    flow; each station node shows a status dot, a defect-rate sparkline, FPY/Cpk,
    and a flag on ALERT/WATCH. Click a node to drill in.
  * Station page    — SPC p-chart, rolling-FPY, the AI root-cause triage panel,
    and a "Run inspection test" simulator that generates a live batch and drops a
    new point on the control chart (red if it blows the limit).

All data (marts + triage) is embedded as JSON; charts are hand-drawn inline SVG in
vanilla JS. Run:  python -m src.dashboard   (after pipeline_pandas + ai_triage)
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.validation import load_control_plan

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "inspection_events.csv"
MARTS = ROOT / "data" / "marts"
OUT = ROOT / "output" / "dashboard.html"
RECENT_DAYS = 14


def _selftest_by_station() -> dict:
    """Per-station cell self-test, condition, wear trend and reaction plan.

    Everything here is produced by src/coverage.py; this only reshapes it for the
    station drill-down. The dashboard is where a quality engineer actually lives,
    so the question "can this station still do its job?" belongs next to the
    control chart rather than on a separate page nobody has open.

    Returns {} if the self-test artefacts are absent, so the dashboard still
    builds when only the SPC half of the pipeline has run.
    """
    from .coverage import (ENV_MODEL, ENV_TELEMETRY, evaluate_selftest, load_yaml,
                           mode_label)
    rep_path = ROOT / "data" / "quality" / "coverage_report.json"
    cond_path = MARTS / "station_condition.csv"
    if not (ENV_TELEMETRY.exists() and cond_path.exists() and rep_path.exists()):
        return {}

    plan = load_control_plan()
    cfg = load_yaml(ENV_MODEL)
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    raw = pd.read_csv(ENV_TELEMETRY).sort_values(["station_id", "production_day"])
    ev = evaluate_selftest(cfg, raw)
    cond = pd.read_csv(cond_path)
    wear_rows = (rep.get("wear_trending") or {}).get("stations", [])
    wear = {}
    for w in wear_rows:
        if w.get("trend_detected"):
            wear.setdefault(w["station_id"], []).append(w)
    plans = {f["station"]: f for f in rep["findings"]
             if f.get("station") and f.get("probable_causes")
             and f["id"] == "CONDITION_COVERAGE_LOST"}
    trend_finding = {f["station"]: f for f in rep["findings"]
                     if f["id"] == "TOOLING_TRENDING_TO_LIMIT"}

    ic, mc, tc = cfg["imaging_check"], cfg["measurement_check"], cfg["tooling_check"]
    limits = {
        "grade": {"col": "datamatrix_grade", "v": ic["datamatrix_grade"]["min"],
                  "above": False, "name": "Reference barcode grade", "unit": ""},
        "sharp": {"col": "ref_sharpness_score", "v": ic["sharpness_score"]["min"],
                  "above": False, "name": "Sharpness on the coupon", "unit": ""},
        "bright": {"col": "ref_brightness_pct",
                   "v": ic["brightness_pct_of_reference"]["min"], "above": False,
                   "name": "Brightness vs reference", "unit": "%"},
        "repeat": {"col": "ref_repeatability_mm", "v": mc["repeatability_mm"]["max"],
                   "above": True, "name": "Measurement repeatability", "unit": " mm"},
    }
    # Every measure of every tooling set gets its own trace, labelled with the set
    # it belongs to. "Tooling wear" as one number would hide which one moved.
    for sname, spec in (tc.get("sets") or {}).items():
        short = "Fixture" if sname == "fixture" else "EOAT"
        for col, m in spec["measures"].items():
            limits[f"{sname}_{col}"] = {
                "col": col, "v": m["max"], "above": True,
                "name": f"{short}: {m.get('name', col)}", "unit": " mm"}

    out = {}
    for st, g in ev.groupby("station_id"):
        c = cond[cond.station_id == st]
        lost = c[~c.effective_coverage]
        worst = (c.loc[c.longest_run_below.idxmax()] if not c.empty else None)
        last = g.iloc[-1]
        ws = wear.get(st) or []
        # Severity order, not alphabetical order — string max() would rank
        # "WATCH" above "OK" above "ALERT", which is exactly backwards.
        rank = {"ALERT": 2, "WATCH": 1, "OK": 0}
        worst_cond = ("OK" if c.empty else
                      max(c.condition.unique(), key=lambda x: rank.get(x, 0)))
        out[st] = {
            "condition": worst_cond,
            "cause": (str(worst.cause) if worst is not None else "-"),
            "toolingSets": {k: v["label"] for k, v in
                            ((cfg.get("tooling_check") or {}).get("sets") or {}).items()},
            "failingSets": ([k for k in ((cfg.get("tooling_check") or {}).get("sets") or {})
                             if f"tooling/{k}" in str(worst.cause)]
                            if worst is not None else []),
            "daysOut": (int(worst.longest_run_below) if worst is not None else 0),
            "firstOut": (None if worst is None or pd.isna(worst.first_day_below)
                         else str(worst.first_day_below)),
            "steps": {"imaging": bool(last.imaging_ok),
                      "measurement": bool(last.measurement_ok),
                      "tooling": bool(last.tooling_ok)},
            "lost": [{"code": m, "label": mode_label(m, plan)}
                     for m in sorted(lost["mode"].unique())],
            "series": {k: [round(float(v), 5) for v in g[spec["col"]].tolist()]
                       for k, spec in limits.items()},
            "limits": {k: {"v": spec["v"], "above": spec["above"],
                           "name": spec["name"], "unit": spec["unit"]}
                       for k, spec in limits.items()},
            "wear": [{"set": w.get("set_label"), "measure": w.get("measure_name"),
                      "latest": w.get("latest_wear_mm"), "limit": w.get("limit_mm"),
                      "rate": w.get("wear_rate_mm_per_1k_units"),
                      "r1": w.get("rate_first_half"), "r2": w.get("rate_second_half"),
                      "accel": bool(w.get("accelerating")),
                      "ratio": w.get("acceleration_ratio"),
                      "units": w.get("projected_units_to_limit"),
                      "days": w.get("projected_days_to_limit"),
                      "label": w.get("projection_label")}
                     for w in ws
                     if w.get("projected_units_to_limit") is not None
                     and w.get("within_limit")] or None,
            "plan": ({k: plans[st][k] for k in
                      ("failure_means", "probable_causes", "containment",
                       "corrective_actions", "owner")} if st in plans else None),
            "trendPlan": ({k: trend_finding[st][k] for k in
                           ("probable_causes", "corrective_actions", "owner")}
                          if st in trend_finding else None),
        }
    return out


def _build_data() -> dict:
    plan = load_control_plan()
    feat = plan["features"][0]
    lsl, usl, nominal, cpk_min = feat["lsl_mm"], feat["usl_mm"], feat["nominal_mm"], feat["cpk_min"]

    raw = pd.read_csv(RAW, parse_dates=["ts"])
    daily = pd.read_csv(MARTS / "daily_fpy.csv", parse_dates=["date"])
    sc = pd.read_csv(MARTS / "station_scorecard.csv")
    pareto = pd.read_csv(MARTS / "pareto.csv")
    triage = json.loads((MARTS / "triage.json").read_text())
    triage_by_id = {t["station"]: t for t in triage["stations"]}

    fp = raw[(raw.inspection_pass == 1) & raw.feature_dim_mm.between(lsl - 5, usl + 5)].copy()
    cutoff = fp.ts.max().floor("D") - pd.Timedelta(days=RECENT_DAYS)

    selftest = _selftest_by_station()

    stations = []
    for r in sc.sort_values("station_id").itertuples():
        g = daily[daily.station_id == r.station_id].sort_values("date")
        recent = fp[(fp.station_id == r.station_id) & (fp.ts >= cutoff)]["feature_dim_mm"]
        stations.append({
            "id": r.station_id, "line": r.line_id,
            "order": int(r.station_id[1:]),
            "n": int(r.n_first_pass), "defects": int(r.defects),
            "fpy": round(float(r.fpy_overall), 4),
            "cpk": round(float(r.cpk_overall), 3), "cpk_recent": round(float(r.cpk_recent), 3),
            "spc": int(r.spc_violations), "status": r.status,
            "sim": {"mean": round(float(recent.mean()), 4),
                    "sd": round(float(recent.std(ddof=1)), 4),
                    "defectRate": round(float(g.tail(RECENT_DAYS)["defect_rate"].mean()), 4)},
            "daily": [{"d": d.strftime("%Y-%m-%d"), "dr": round(float(dr), 4),
                       "ucl": round(float(u), 4), "lcl": round(float(l), 4),
                       "pbar": round(float(p), 4), "fpy": round(float(f), 4),
                       "roll": round(float(rf), 4), "ooc": bool(o)}
                      for d, dr, u, l, p, f, rf, o in zip(
                          g.date, g.defect_rate, g.ucl, g.lcl, g.pbar, g.fpy,
                          g.rolling_fpy, g.out_of_control)],
            "triage": triage_by_id.get(r.station_id, {}),
            "selftest": selftest.get(r.station_id),
        })

    overall_fpy = 1 - sc.defects.sum() / sc.n_first_pass.sum()
    return {
        "meta": {"part": plan["part_number"], "rev": plan["revision"], "program": plan["program"],
                 "lsl": lsl, "usl": usl, "nominal": nominal, "cpk_min": cpk_min,
                 "dateMin": daily.date.min().strftime("%Y-%m-%d"),
                 "dateMax": daily.date.max().strftime("%Y-%m-%d"),
                 "overallFpy": round(float(overall_fpy), 4),
                 "nAlert": int((sc.status == "ALERT").sum()),
                 "dqScore": triage["data_quality"]["dq_score"],
                 "nSelftest": sum(1 for v in selftest.values()
                                  if v["condition"] == "ALERT"),
                 "hasSelftest": bool(selftest)},
        "stations": stations,
        "pareto": [{"code": c, "count": int(n), "cum": float(cp)}
                   for c, n, cp in zip(pareto.defect_code, pareto["count"], pareto.cum_pct)],
        "dataQuality": triage["data_quality"],
    }


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quality Intelligence Dashboard</title>
<style>
:root{--ink:#1a1d21;--muted:#5b6470;--line:#d9dee5;--accent:#0f3d2e;--panel:#f6f8f7;
  --ok:#0f7a4f;--watch:#8a5a00;--alert:#b3261e;}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:var(--ink);margin:0;background:#eef1f3}
.wrap{max-width:1080px;margin:0 auto;background:#fff;min-height:100vh;padding:22px 28px 48px;
  box-shadow:0 2px 18px rgba(0,0,0,.12)}
h1{font-size:21px;margin:0 0 2px}.sub{color:var(--accent);font-weight:600;margin:0 0 2px;font-size:14px}
.meta{color:var(--muted);font-size:12px;margin:0 0 16px}
h2{font-size:14px;color:var(--accent);border-bottom:2px solid var(--accent);padding-bottom:4px;margin:24px 0 12px;
  text-transform:uppercase;letter-spacing:.04em}
.tiles{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.tile{flex:1;min-width:150px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 15px}
.tv{font-size:24px;font-weight:700}.tl{font-size:12px;color:var(--muted)}
.line{margin:12px 0 26px}
.lineLabel{font-size:12px;font-weight:700;color:var(--muted);margin-bottom:10px}
.flow{display:flex;align-items:stretch;gap:10px;row-gap:16px;flex-wrap:wrap}
.node{flex:1 1 0;min-width:180px;background:#fff;border:1px solid var(--line);border-left-width:5px;
  border-radius:8px;padding:12px 14px;cursor:pointer;transition:transform .08s,box-shadow .08s}
.node:hover{transform:translateY(-2px);box-shadow:0 4px 14px rgba(0,0,0,.12)}
.node .top{display:flex;align-items:center;justify-content:space-between}
.node .sid{font-weight:700;font-size:15px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px}
.node .kv{font-size:12px;color:var(--muted);margin-top:4px;display:flex;justify-content:space-between}
.node .kv b{color:var(--ink)}
.flag{font-size:12px;font-weight:700;padding:1px 7px;border-radius:10px;color:#fff}
.arrow{flex:0 0 auto;align-self:center;color:var(--accent);font-size:34px;font-weight:700;
  line-height:1;padding:0 2px;user-select:none}
.topbar{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.prov{display:flex;align-items:flex-start;gap:10px;background:var(--panel);
  border:1px solid var(--line);border-left:5px solid #FF3621;border-radius:0 6px 6px 0;
  padding:10px 14px;margin:0 0 16px;font-size:12px;color:var(--muted);line-height:1.5}
.provbadge{flex:none;background:#FF3621;color:#fff;font-weight:700;font-size:11px;
  letter-spacing:.03em;padding:3px 9px;border-radius:4px}
.prov b{color:var(--ink)}
.prov code{background:#eceff1;padding:1px 5px;border-radius:4px;font-size:11px}
.scorelink{flex:none;color:var(--accent);font-weight:600;font-size:13px;text-decoration:none;
  border:1px solid var(--line);border-radius:6px;padding:7px 13px;white-space:nowrap;background:var(--panel)}
.scorelink:hover,.scorelink:focus{background:#eef5f1;border-color:var(--accent);outline:none}
.tabs{display:flex;gap:4px;margin:8px 0 12px;border-bottom:2px solid var(--line);flex-wrap:wrap}
.tab{background:none;border:0;border-bottom:3px solid transparent;padding:8px 18px;cursor:pointer;font:600 13px inherit;color:var(--muted)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:12px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.back{background:none;border:1px solid var(--line);border-radius:6px;padding:6px 12px;cursor:pointer;font:600 13px inherit}
.pill{color:#fff;padding:2px 10px;border-radius:11px;font-size:11px;font-weight:700}
.sev{display:inline-block;color:#fff;padding:2px 9px;border-radius:6px;font-size:11px;font-weight:700}
button.run{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:9px 16px;font:600 14px inherit;cursor:pointer}
button.run:hover{background:#0b2c21}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:10px}
.chip{font-size:11px;font-weight:700;color:#fff;border-radius:4px;padding:2px 6px}
ul.acts{margin:6px 0 0 0;padding-left:18px}ul.acts li{font-size:13px;margin:3px 0}
svg{max-width:100%;height:auto}.hint{font-size:12px;color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid var(--line);padding:5px 8px;text-align:center}
th{background:var(--accent);color:#fff}
.stbadge{font-size:10px;font-weight:700;color:#fff;border-radius:3px;padding:1px 5px;margin-left:4px}
.steps{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 4px}
.step{flex:1;min-width:118px;border:1px solid var(--line);border-radius:6px;padding:7px 9px;background:#fff}
.step.no{border-color:var(--crit);background:#fdf5f5}
.step .sn{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.step .sv{font-size:12px;font-weight:700;margin-top:2px}
.step.ok .sv{color:var(--good)} .step.no .sv{color:var(--crit)}
.stgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:8px}
@media(max-width:700px){.stgrid{grid-template-columns:1fr 1fr}}
.stc{border:1px solid var(--line);border-radius:6px;padding:6px 7px 2px}
.stc.bad{border-color:var(--crit);background:#fdf5f5}
.stc .sh{display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:1px}
.stc .sh b{font-weight:600}
.stc .sh span{color:var(--muted);font-variant-numeric:tabular-nums}
.rcabox{border-top:1px solid var(--line);margin-top:10px;padding-top:9px}
.rcah{font-size:10px;text-transform:uppercase;letter-spacing:.03em;color:var(--muted);font-weight:700;margin:7px 0 2px}
ol.rca{margin:0;padding-left:18px}ol.rca li{font-size:12.5px;margin:2px 0}
.disc{font-size:11.5px;color:var(--muted);font-style:italic;margin:8px 0 0}
</style></head><body><div class="wrap">
<div class="topbar">
  <div class="titleblock">
    <h1>Quality Intelligence Dashboard</h1>
    <p class="sub" id="subhead"></p>
    <p class="meta" id="metahead"></p>
  </div>
  <a class="scorelink" href="scorecard.html">&#8592; Back to Scorecard</a>
</div>
<div class="prov"><span class="provbadge">Databricks</span>
  <span>Yield, SPC and capability figures on this page are rendered directly from
  <b>Databricks</b> Delta output &mdash; <code>quality.daily_fpy</code> and
  <code>quality.station_scorecard</code> &mdash; produced by a <b>PySpark</b> lakehouse pipeline
  (7-day rolling FPY via a window function; p-chart limits frozen on a 21-day baseline),
  verified cell-for-cell against the tested reference implementation.
  Defect Pareto and data-quality counts come from the same pipeline's validation stage.</span></div>
<div id="app"></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const M = D.meta, COLOR = {OK:'#0f7a4f',WATCH:'#8a5a00',ALERT:'#b3261e',none:'#0f7a4f',
  low:'#0f7a4f',medium:'#8a5a00',high:'#b3261e'};
const sim = {};  // appended test-run points per station
document.getElementById('subhead').textContent =
  `Part ${M.part} rev ${M.rev} · ${M.program} · automated vision inspection`;
document.getElementById('metahead').textContent =
  `Window ${M.dateMin} to ${M.dateMax} · live line-flow, SPC & AI root-cause triage · synthetic demo data`;
const pct = x => (x*100).toFixed(1)+'%';
const byId = id => D.stations.find(s=>s.id===id);

// ---- tiny SVG helpers -------------------------------------------------------
function sparkline(st){
  const w=150,h=34,p=3,arr=st.daily.map(x=>x.dr),n=arr.length;
  const mx=Math.max(...arr,0.001);
  const pts=arr.map((v,i)=>`${p+i*(w-2*p)/(n-1)},${h-p-(v/mx)*(h-2*p)}`).join(' ');
  const flags=st.daily.map((x,i)=>x.ooc?`<circle cx="${p+i*(w-2*p)/(n-1)}" cy="${h-p-(x.dr/mx)*(h-2*p)}" r="2.4" fill="${COLOR.ALERT}"/>`:'').join('');
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="${COLOR[st.status]}" stroke-width="1.4"/>${flags}</svg>`;
}
function pchart(st){
  const w=620,h=250,pl=44,pr=12,pt=14,pb=26;
  const base=st.daily.slice(), extra=(sim[st.id]||[]);
  const all=base.map(x=>x.dr).concat(extra.map(x=>x.dr));
  const ymax=Math.max(...all,...base.map(x=>x.ucl))*1.15, n=base.length+extra.length;
  const X=i=>pl+i*(w-pl-pr)/(n-1), Y=v=>h-pb-(v/ymax)*(h-pt-pb);
  const line=(key,color,dash='')=>`<polyline points="${base.map((x,i)=>`${X(i)},${Y(x[key])}`).join(' ')}" fill="none" stroke="${color}" stroke-width="1.3" ${dash?`stroke-dasharray="${dash}"`:''}/>`;
  const drLine=`<polyline points="${base.map((x,i)=>`${X(i)},${Y(x.dr)}`).join(' ')}" fill="none" stroke="${COLOR.OK}" stroke-width="1.4"/>`;
  const ooc=base.map((x,i)=>x.ooc?`<circle cx="${X(i)}" cy="${Y(x.dr)}" r="3.2" fill="${COLOR.ALERT}"/>`:'').join('');
  const sims=extra.map((x,i)=>{const xi=base.length+i;const bad=x.dr>base[base.length-1].ucl;
    return `<rect x="${X(xi)-3}" y="${Y(x.dr)-3}" width="6" height="6" transform="rotate(45 ${X(xi)} ${Y(x.dr)})" fill="${bad?COLOR.ALERT:COLOR.WATCH}"/>`;}).join('');
  const yt=[0,ymax/2,ymax].map(v=>`<text x="${pl-6}" y="${Y(v)+3}" font-size="9" text-anchor="end" fill="#5b6470">${(v*100).toFixed(1)}%</text><line x1="${pl}" y1="${Y(v)}" x2="${w-pr}" y2="${Y(v)}" stroke="#eee"/>`).join('');
  const xt=[0,Math.floor(base.length/2),base.length-1].map(i=>`<text x="${X(i)}" y="${h-8}" font-size="9" text-anchor="middle" fill="#5b6470">${base[i].d.slice(5)}</text>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">${yt}${xt}
    ${line('ucl',COLOR.ALERT,'4 3')}${line('lcl',COLOR.ALERT,'4 3')}${line('pbar','#888')}
    ${drLine}${ooc}${sims}</svg>`;
}
function barCpk(){
  const w=300,h=200,pl=30,pb=22,pt=10,pr=8,ss=D.stations,mx=Math.max(...ss.map(s=>s.cpk),M.cpk_min)*1.1;
  const bw=(w-pl-pr)/ss.length*0.6;
  const bars=ss.map((s,i)=>{const x=pl+i*(w-pl-pr)/ss.length+((w-pl-pr)/ss.length-bw)/2,y=h-pb-(s.cpk/mx)*(h-pt-pb);
    return `<rect x="${x}" y="${y}" width="${bw}" height="${h-pb-y}" fill="${COLOR[s.status]}"/><text x="${x+bw/2}" y="${h-8}" font-size="9" text-anchor="middle">${s.id}</text>`;}).join('');
  const yl=h-pb-(M.cpk_min/mx)*(h-pt-pb);
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">${bars}<line x1="${pl}" y1="${yl}" x2="${w-pr}" y2="${yl}" stroke="#1a1d21" stroke-dasharray="4 3"/><text x="${w-pr}" y="${yl-3}" font-size="9" text-anchor="end">Cpk ${M.cpk_min}</text></svg>`;
}
function barPareto(){
  const w=300,h=200,pl=30,pb=34,pt=10,pr=8,pa=D.pareto,mx=Math.max(...pa.map(p=>p.count))*1.1;
  const bw=(w-pl-pr)/pa.length*0.6;
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">`+pa.map((p,i)=>{const x=pl+i*(w-pl-pr)/pa.length+((w-pl-pr)/pa.length-bw)/2,y=h-pb-(p.count/mx)*(h-pt-pb);
    return `<rect x="${x}" y="${y}" width="${bw}" height="${h-pb-y}" fill="${COLOR2(i)}"/><text x="${x+bw/2}" y="${h-pb+11}" font-size="8" text-anchor="middle" transform="rotate(12 ${x+bw/2} ${h-pb+11})">${p.code}</text>`;}).join('')+`</svg>`;
}
const COLOR2=i=>['#0f3d2e','#2a6f52','#5a9b7f','#8ac0a8'][i%4];

// ---- views ------------------------------------------------------------------
function node(st){
  const flag = st.status!=='OK'?`<span class="flag" style="background:${COLOR[st.status]}">${st.status==='ALERT'?'🚩 ALERT':'⚠ WATCH'}</span>`:'';
  return `<div class="node" style="border-left-color:${COLOR[st.status]}" onclick="go('${st.line}/${st.id}')">
    <div class="top"><span class="sid"><span class="dot" style="background:${COLOR[st.status]}"></span>Station ${st.id}</span>${flag}${stBadge(st)}</div>
    ${sparkline(st)}
    <div class="kv"><span>FPY</span><b>${pct(st.fpy)}</b></div>
    <div class="kv"><span>Cpk (recent)</span><b>${st.cpk_recent}</b></div>
    <div class="kv"><span>SPC pts</span><b>${st.spc}</b></div></div>`;
}

/* ---- cell self-test --------------------------------------------------------
   A robot checks its home position before it trusts its own coordinates. Same
   question here: can this station still do the job it was accepted to do? It
   lives next to the control chart because that is where the engineer already
   is, not on a separate page nobody has open. ----------------------------- */
function stSpark(vals,limit,aboveBad,w,h){
  w=w||150;h=h||38;if(!vals||!vals.length)return '';
  let lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);
  if(limit!=null){lo=Math.min(lo,limit);hi=Math.max(hi,limit);}
  const pad=(hi-lo)*0.15||0.01;lo-=pad;hi+=pad;
  const n=vals.length,sx=i=>2+i*(w-4)/Math.max(1,n-1),sy=v=>h-2-(v-lo)*(h-4)/(hi-lo);
  let segs=[],cur=[],bc=null;
  vals.forEach(function(v,i){
    const bad=limit!=null&&(aboveBad?v>limit:v<limit);
    if(bc===null||bad===bc){cur.push([sx(i),sy(v)]);}
    else{segs.push([bc,cur.concat([[sx(i),sy(v)]])]);cur=[[sx(i),sy(v)]];}
    bc=bad;});
  segs.push([bc,cur]);
  const paths=segs.filter(x=>x[1].length>1).map(function(pair){
    const pts=pair[1].map(q=>q[0].toFixed(1)+','+q[1].toFixed(1)).join(' ');
    return '<polyline points="'+pts+'" fill="none" stroke="'+(pair[0]?COLOR.ALERT:COLOR.OK)+'" stroke-width="1.6"/>';
  }).join('');
  const ly=limit==null?'':'<line x1="0" y1="'+sy(limit).toFixed(1)+'" x2="'+w+'" y2="'+sy(limit).toFixed(1)+'" stroke="'+COLOR.ALERT+'" stroke-width="1" stroke-dasharray="3,3" opacity=".75"/>';
  return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" height="'+h+'" preserveAspectRatio="none">'+ly+paths+'</svg>';
}
function stBadge(st){
  const q=st.selftest;
  if(!q||q.condition==='OK')return '';
  const c=q.condition==='ALERT'?COLOR.ALERT:COLOR.WATCH;
  return '<span class="stbadge" style="background:'+c+'" title="Cell self-test: '+q.cause+'">SELF-TEST '+q.condition+'</span>';
}
function stepStrip(q){
  function step(k,n){
    const ok=q.steps[k];
    return '<div class="step '+(ok?'ok':'no')+'"><div class="sn">'+n+'</div><div class="sv">'+(ok?'GO':'NO-GO')+'</div></div>';
  }
  return '<div class="steps">'+step('imaging','1 - Imaging')+step('measurement','2 - Measurement')+
         step('tooling','3 - Tooling')+
         '<div class="step ok"><div class="sn">4 - Production</div><div class="sv">judged</div></div></div>';
}
function stCharts(q){
  return '<div class="stgrid">'+Object.keys(q.series).map(function(k){
    const L=q.limits[k],v=q.series[k],last=v[v.length-1];
    const bad=L.above?last>L.v:last<L.v;
    return '<div class="stc'+(bad?' bad':'')+'"><div class="sh"><b>'+L.name+'</b><span>'+
      Number(last).toPrecision(3)+L.unit+'</span></div>'+stSpark(v,L.v,L.above)+'</div>';
  }).join('')+'</div>';
}
function rcaList(t,items){
  if(!items||!items.length)return '';
  return '<div class="rcah">'+t+'</div><ol class="rca">'+items.map(i=>'<li>'+i+'</li>').join('')+'</ol>';
}
function selftestCard(s){
  const q=s.selftest;
  if(!q)return '';
  const c=q.condition==='ALERT'?COLOR.ALERT:(q.condition==='WATCH'?COLOR.WATCH:COLOR.OK);
  const fs=q.failingSets||[];
  const attribution=fs.length
    ? '<p style="margin:8px 0 0;padding:8px 10px;background:#fdf5f5;border-left:3px solid '+
      COLOR.ALERT+';border-radius:0 4px 4px 0"><b>Attribution:</b> the <b>'+
      fs.map(k=>q.toolingSets[k]).join(' and ')+'</b> is out of limit — and the '+
      Object.keys(q.toolingSets).filter(k=>fs.indexOf(k)<0).map(k=>q.toolingSets[k]).join(' and ')+
      ' is inside it. Both push on the same reading at step 2, so a repeatability failure on '+
      'its own cannot say which one moved. Inspecting them as separate sets is what turns the '+
      'symptom into something you can go and fix.</p>'
    : '';
  const lost=q.lost.length
    ? '<p style="margin:8px 0 0"><b>Cannot currently be trusted to find:</b> '+
      q.lost.map(m=>m.label).join(', ')+'. Failed step: <b>'+q.cause+'</b>, '+q.daysOut+
      ' day(s) in a row'+(q.firstOut?', first out '+q.firstOut:'')+'.</p>'
    : '';
  const p=q.plan;
  const rca=p
    ? '<div class="rcabox"><div class="rcah">Root-cause attribution and corrective action'+
      (p.owner?' - owned by '+p.owner:'')+'</div>'+
      (p.failure_means?'<p style="font-size:12.5px;margin:0 0 6px">'+p.failure_means+'</p>':'')+
      rcaList('Probable causes, most likely first',p.probable_causes)+
      rcaList('Contain now',p.containment)+
      rcaList('Corrective action',p.corrective_actions)+
      '<p class="disc">Attribution is a lookup against a reviewed list in '+
      'config/station_selftest.yaml - deterministic, versioned, owned by a person. '+
      'No model chooses the cause. It is where an investigation starts, not where it ends.</p></div>'
    : '';
  const ws=q.wear||[];
  const wear=ws.length
    ? '<div class="rcabox"><div class="rcah">Tooling wear by set - inside spec, and where it is heading</div>'+
      ws.map(function(w){
        return '<p style="font-size:12.5px;margin:0 0 7px"><b>'+w.set+'</b> - '+w.measure+
          ' is <b>'+w.latest+' mm</b> against a <b>'+w.limit+' mm</b> limit, moving at '+w.rate+
          ' mm per thousand units - about <b>'+Number(w.units).toLocaleString()+
          ' more units</b> (~'+w.days+' days) to the limit.'+
          (w.accel?' <b style="color:'+COLOR.ALERT+'">The rate itself is climbing</b> - '+w.r1+
            ' then '+w.r2+' mm per thousand units, a factor of '+w.ratio+
            '. Steady wear is a maintenance schedule; wear that speeds up means something '+
            'else changed, and the parts made during the acceleration are the ones worth '+
            'reviewing.':' The rate is steady, so this is a scheduling signal.')+'</p>';
      }).join('')+
      (q.trendPlan?rcaList('Corrective action',q.trendPlan.corrective_actions):'')+
      '<p class="disc">'+ws[0].label+'</p></div>'
    : '';
  return '<div class="card"><b>Cell self-test</b> <span class="pill" style="background:'+c+'">'+
    q.condition+'</span><p class="hint">Before judging any production unit the station measures a '+
    'known reference coupon and inspects its own fixture. Because the answer is known in advance, '+
    'bias and repeatability are measured rather than inferred. Which step fails decides which '+
    'inspections are lost: an imaging failure costs the visual checks and spares the measurement; '+
    'a measurement or tooling failure does the reverse.</p>'+
    stepStrip(q)+lost+attribution+stCharts(q)+rca+wear+'</div>';
}
function lineFlow(lineId){
  const ss=D.stations.filter(s=>s.line===lineId).sort((a,b)=>a.order-b.order);
  return `<div class="line"><div class="lineLabel">${lineId} — material flow →</div><div class="flow">`+
    ss.map((s,i)=>node(s)+(i<ss.length-1?'<span class="arrow">→</span>':'')).join('')+`</div></div>`;
}
const LINES=[...new Set(D.stations.map(s=>s.line))].sort();

/* ---- hash routing ----------------------------------------------------------
   The URL hash is the single source of truth for what's on screen. scorecard.html
   deep-links in two forms, mirroring the Line -> Station hierarchy:
       dashboard.html#LINE-B       -> line overview, LINE-B tab active
       dashboard.html#LINE-B/S5    -> Station S5 detail page
   Every navigation just sets the hash; hashchange re-renders. ------------------ */
function parseHash(){
  const parts=decodeURIComponent((location.hash||'').replace(/^#/,'')).split('/');
  const l=LINES.indexOf(parts[0])>=0?parts[0]:null;
  const s=(parts[1]&&byId(parts[1]))?parts[1]:null;
  return {line:l, station:s};
}
let curLine=parseHash().line||LINES[0];
let curView=null;                       // hash string currently rendered
function go(h){                         // navigate; re-render if hash is unchanged
  if((location.hash||'').replace(/^#/,'')===h){curView=null;render();}
  else location.hash=h;
}
function render(){
  const {line,station}=parseHash();
  const want=station?(byId(station).line+'/'+station):(line||LINES[0]);
  if(want===curView) return;
  curView=want;
  if(station){curLine=byId(station).line;showStation(station);}
  else {curLine=line||LINES[0];overview();}
}
window.addEventListener('hashchange',render);
function lineTabs(){return `<div class="tabs">`+LINES.map(l=>`<button class="tab${l===curLine?' active':''}" onclick="go('${l}')">${l}</button>`).join('')+`</div>`;}
function overview(){
  document.getElementById('app').innerHTML = `
  <div class="tiles">
    <div class="tile"><div class="tv">${pct(M.overallFpy)}</div><div class="tl">Overall first-pass yield</div></div>
    <div class="tile"><div class="tv" style="color:${M.nAlert?COLOR.ALERT:COLOR.OK}">${M.nAlert}</div><div class="tl">Stations in ALERT</div></div>
    <div class="tile"><div class="tv" style="color:${M.dqScore<0.99?COLOR.WATCH:COLOR.OK}">${pct(M.dqScore)}</div><div class="tl">Data-quality score</div></div>
    ${M.hasSelftest?`<div class="tile"><div class="tv" style="color:${M.nSelftest?COLOR.ALERT:COLOR.OK}">${M.nSelftest}</div><div class="tl">Stations failing cell self-test</div></div>`:''}
  </div>
  <h2>Production line flow</h2>
  <p class="hint">One tab per line. Each node is a vision-inspection station in material-flow order; color &amp; flag show live status. Click a station to drill in and run a test.</p>
  ${lineTabs()}<div id="flowbox">${lineFlow(curLine)}</div>
  <h2>Fleet analytics</h2>
  <div class="grid2">
    <div class="card"><b>Process capability (Cpk) by station</b>${barCpk()}</div>
    <div class="card"><b>Defect Pareto (first-pass fails)</b>${barPareto()}</div>
  </div>
  <div class="card"><b>AI data-quality triage.</b> ${D.dataQuality.hypothesis}</div>`;
}
function showStation(id){
  const s=byId(id), t=s.triage||{};
  const causes=(t.likely_causes||[]).map(c=>`<span class="chip" style="background:${COLOR[t.severity]||'#888'}">${c}</span>`).join('');
  const acts=(t.recommended_actions||[]).map(a=>`<li>${a}</li>`).join('');
  document.getElementById('app').innerHTML=`
  <button class="back" onclick="go(curLine)">← All stations on ${s.line}</button>
  <h2>${s.line} · Station ${s.id} <span class="pill" style="background:${COLOR[s.status]}">${s.status}</span></h2>
  <div class="tiles">
    <div class="tile"><div class="tv">${pct(s.fpy)}</div><div class="tl">First-pass yield</div></div>
    <div class="tile"><div class="tv" style="color:${s.cpk_recent<M.cpk_min?COLOR.ALERT:COLOR.OK}">${s.cpk_recent}</div><div class="tl">Cpk (recent)</div></div>
    <div class="tile"><div class="tv">${s.defects}</div><div class="tl">Defects</div></div>
    <div class="tile"><div class="tv">${s.spc}</div><div class="tl">SPC points out</div></div>
  </div>
  <div class="card"><b>SPC p-chart — first-pass defect rate</b><div id="pc">${pchart(s)}</div>
    <p class="hint">Green = daily defect rate · red dashed = 3σ limits · red dots = out-of-control · diamonds = your simulated test batches.</p></div>
  ${selftestCard(s)}
  ${t.hypothesis?`<div class="card"><span class="sev" style="background:${COLOR[t.severity]}">${(t.severity||'').toUpperCase()} · ${(t.pattern||'').replace(/_/g,' ')}</span>
     <p><b>AI root-cause triage:</b> ${t.hypothesis}</p>
     ${causes?`<div><b>Likely causes</b><div class="chips">${causes}</div></div>`:''}
     ${acts?`<div style="margin-top:8px"><b>Recommended actions</b><ul class="acts">${acts}</ul></div>`:''}</div>`:''}
  <div class="card"><b>Run inspection test</b>
    <p class="hint">Simulates a live batch of 20 units at this station's current process, appends the batch to the control chart, and shows pass/fail.</p>
    <button class="run" onclick="runTest('${s.id}')">▶ Run inspection test (20 units)</button>
    <div id="result"></div></div>`;
}

// ---- the "run a test" simulator --------------------------------------------
let seed=1234567; function rnd(){seed=(seed*48271)%2147483647;return seed/2147483647;}
function gauss(m,sd){let u=0,v=0;while(!u)u=rnd();while(!v)v=rnd();return m+sd*Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);}
function runTest(id){
  const s=byId(id),N=20;let fail=0,chips='';
  for(let i=0;i<N;i++){
    const dim=gauss(s.sim.mean,s.sim.sd||0.05);
    const oos=dim<M.lsl||dim>M.usl, cos=rnd()<s.sim.defectRate, bad=oos||cos;
    if(bad)fail++;
    const reason=oos?`OOS ${dim.toFixed(2)}`:(cos?'defect':'');
    chips+=`<span class="chip" style="background:${bad?COLOR.ALERT:COLOR.OK}">${bad?'FAIL '+reason:'PASS'}</span>`;
  }
  const dr=fail/N;(sim[id]=sim[id]||[]).push({dr});
  const ucl=s.daily[s.daily.length-1].ucl, ooc=dr>ucl;
  document.getElementById('pc').innerHTML=pchart(s);
  document.getElementById('result').innerHTML=
    `<p style="margin-top:10px"><b>Batch result:</b> ${N-fail}/${N} pass · FPY ${pct(1-dr)} · defect rate ${pct(dr)}
     ${ooc?`<span class="flag" style="background:${COLOR.ALERT}">🚩 OUT OF CONTROL (> UCL ${pct(ucl)})</span>`:`<span class="flag" style="background:${COLOR.OK}">in control</span>`}</p>
     <div class="chips">${chips}</div>`;
}
render();          // boot from the URL hash (deep link) or default to the first line
</script></body></html>"""


def build() -> Path:
    data = _build_data()
    html = HTML.replace("__DATA__", json.dumps(data))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    return OUT


def main() -> None:
    out = build()
    print(f"Wrote interactive dashboard -> {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
