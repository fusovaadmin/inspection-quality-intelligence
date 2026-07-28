"""
Renders output/coverage.html — inspection coverage and operating-point economics.

Every number on the page is read from data/quality/coverage_report.json and the
marts src/coverage.py wrote. Nothing is hardcoded, so the page cannot drift away
from what the audit actually found — including the parts that are inconvenient.

Run:  python -m src.coverage && python -m src.coverage_page
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
QDIR = ROOT / "data" / "quality"
MARTS = ROOT / "data" / "marts"
OUT = ROOT / "output" / "coverage.html"

CSS = """
:root{--ink:#1a1d21;--muted:#5b6470;--line:#d9dee5;--accent:#0f3d2e;--panel:#f6f8f7;
      --crit:#d03b3b;--warn:#fab219;--good:#0ca30c}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:var(--ink);
  margin:0;background:#eef1f3;line-height:1.55}
.wrap{max-width:960px;margin:0 auto;background:#fff;min-height:100vh;
  padding:34px 30px 56px;box-shadow:0 2px 18px rgba(0,0,0,.12)}
h1{font-size:24px;margin:0 0 4px}
.sub{color:var(--accent);font-weight:600;margin:0 0 4px;font-size:14px}
.meta{color:var(--muted);font-size:12px;margin:0 0 18px}
h2{font-size:14px;color:var(--accent);border-bottom:2px solid var(--accent);
  padding-bottom:4px;margin:32px 0 14px;text-transform:uppercase;letter-spacing:.04em}
h3{font-size:13px;margin:22px 0 6px;color:var(--ink)}
.back{float:right;font-size:12px;font-weight:600;color:var(--accent);text-decoration:none}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:6px 0 4px}
@media(max-width:720px){.tiles{grid-template-columns:1fr 1fr}}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.tile .v{font-size:26px;font-weight:700;line-height:1.1;color:var(--accent)}
.tile .v.bad{color:var(--crit)}
.tile .v.warn{color:var(--warn)}
.tile .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;
  margin-top:5px}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:6px 0 4px}
th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{background:var(--panel);color:var(--accent);font-size:11px;text-transform:uppercase;
  letter-spacing:.03em}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11.5px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;margin:8px 0 4px}
.scroll table{margin:0;border:0}
.find{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--crit);
  border-radius:0 8px 8px 0;padding:12px 16px;margin:10px 0;font-size:13px}
.find b{color:var(--crit)}
.find.q{border-left-color:var(--warn)} .find.q b{color:var(--warn)}
.find.i{border-left-color:var(--accent)} .find.i b{color:var(--accent)}
.find .where{color:var(--muted);font-size:12px;display:block;margin-top:3px}
.prov{display:flex;align-items:flex-start;gap:10px;background:var(--panel);
  border:1px solid var(--line);border-left:5px solid var(--accent);border-radius:0 6px 6px 0;
  padding:11px 15px;margin:18px 0;font-size:12px;color:var(--muted)}
.badge{flex:none;background:var(--accent);color:#fff;font-weight:700;font-size:11px;
  padding:3px 9px;border-radius:4px}
code{background:#f0f0f0;padding:1px 5px;border-radius:4px;font-size:11.5px}
ul{font-size:13.5px;padding-left:20px;margin:8px 0}
li{margin:6px 0}
.note{font-size:12px;color:var(--muted);border-top:1px solid var(--line);
  margin-top:30px;padding-top:14px}
.mx td,.mx th{text-align:center;padding:6px 4px}
.mx td.lbl,.mx th.lbl{text-align:left;white-space:nowrap;font-weight:600}
.mx .grp{background:var(--panel);font-size:10px;color:var(--muted)}
.dot{display:inline-block;width:15px;height:15px;line-height:15px;border-radius:50%;
  font-size:10px;font-weight:700;color:#fff}
.dot.cov{background:var(--good)} .dot.und{background:var(--crit)}
.dot.sil{background:var(--warn)} .dot.na{background:#dfe4ea;color:#9aa4af}
.dot.lost{background:var(--crit);box-shadow:0 0 0 3px rgba(208,59,59,.28)}
.key{font-size:12px;color:var(--muted);margin:10px 0 0}
.key span{margin-right:14px;white-space:nowrap}
.scope{background:#f4f7fa;border:1px solid var(--line);border-radius:8px;padding:14px 18px;
  font-size:12.5px;margin:10px 0}
.scope li{margin:4px 0;font-size:12.5px}
h4{font-size:12.5px;margin:18px 0 8px;color:var(--ink)}
h4 .sub-note{font-weight:400;color:var(--muted);font-size:11.5px}
.smgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
@media(max-width:640px){.smgrid{grid-template-columns:1fr 1fr}}
.sm{border:1px solid var(--line);border-radius:7px;padding:7px 8px 2px;background:#fff}
.sm.bad{border-color:var(--crit);background:#fdf5f5}
.sm-h{display:flex;justify-content:space-between;font-size:11px;margin-bottom:2px}
.sm-h b{color:var(--accent)} .sm.bad .sm-h b{color:var(--crit)}
.sm-h span{color:var(--muted);font-variant-numeric:tabular-nums}
.loop{margin:10px -16px -12px;padding:11px 16px 12px;background:#fff;
  border-top:1px solid var(--line);border-radius:0 0 8px 0}
.rp-t{font-size:11px;text-transform:uppercase;letter-spacing:.04em;font-weight:700;
  color:var(--accent);margin-bottom:6px}
.rp-m{font-size:12.5px;margin:0 0 8px;color:var(--ink)}
.rp-h{font-size:11px;text-transform:uppercase;letter-spacing:.03em;color:var(--muted);
  margin:8px 0 2px;font-weight:600}
ol.rp{margin:0;padding-left:20px} ol.rp li{font-size:12.5px;margin:2px 0}
.rp-n{font-size:11.5px;color:var(--muted);margin:10px 0 0;font-style:italic}
.la{margin-top:9px;padding:9px 11px;border-radius:6px;background:#f4f7fa;
  border-left:4px solid var(--accent);font-size:12.5px}
.la.sys{border-left-color:var(--crit);background:#fdf5f5}
.la b.v{display:inline-block;font-size:10px;letter-spacing:.04em;color:#fff;
  background:var(--accent);border-radius:3px;padding:1px 6px;margin-right:6px}
.la.sys b.v{background:var(--crit)}
"""

STATE_DOT = {"COVERED": ("cov", "●"), "UNDECLARED_DETECTED": ("und", "▲"),
             "DECLARED_SILENT": ("sil", "○"), "NOT_APPLICABLE": ("na", "·")}
SEV_CLASS = {"high": "", "open_question": "q", "watch": "q", "insight": "i", "medium": "q"}

# Populated at render time from config/control_plan.yaml. Codes are how the
# machines talk; a reader who has to decode an abbreviation to follow a finding
# stops following the finding.
MODE_LABELS: dict[str, str] = {}
BASIS_LABELS = {
    "geometry": "Size / position measurement",
    "appearance_high_contrast": "Obvious visual check",
    "appearance_low_contrast": "Fine visual check",
}
BASIS_PLAIN = {
    "geometry": "measures a dimension by finding an edge",
    "appearance_high_contrast": "spots something obvious — a part missing, solder bridged",
    "appearance_low_contrast": "spots something faint — a light scratch, a narrow gap",
}


def _table(df: pd.DataFrame, numeric: set[str] | None = None) -> str:
    numeric = numeric or set()
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    rows = []
    for r in df.itertuples(index=False):
        cells = []
        for c, v in zip(df.columns, r):
            s = "" if pd.isna(v) else html.escape(str(v))
            cells.append(f'<td class="{"n" if c in numeric else ""} mono">{s}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table><tr>{head}</tr>{"".join(rows)}</table></div>'


def _matrix(cov: pd.DataFrame, cond: pd.DataFrame) -> str:
    """Mode x station coverage grid, with lost effective coverage ringed."""
    stations = (cov[["line_id", "station_id", "position"]].drop_duplicates()
                .sort_values(["line_id", "position"]))
    order = stations["station_id"].tolist()
    lost = set()
    if not cond.empty:
        lost = {(r.station_id, r.mode)
                for r in cond[~cond.effective_coverage].itertuples()}

    grp_cells, hdr_cells = [], ['<th class="lbl">Failure mode</th>']
    for line, block in stations.groupby("line_id", sort=False):
        grp_cells.append(f'<th class="grp" colspan="{len(block)}">{html.escape(line)}</th>')
        hdr_cells += [f"<th>{html.escape(s)}</th>" for s in block["station_id"]]

    rows = []
    for mode in sorted(cov["mode"].unique()):
        sub = cov[cov["mode"] == mode].set_index("station_id")
        name = MODE_LABELS.get(mode, mode)
        cells = [f'<td class="lbl">{html.escape(name)}<br>'
                 f'<span class="mono" style="font-weight:400;color:var(--muted)">'
                 f'{html.escape(mode)}</span></td>']
        for st in order:
            state = sub.loc[st, "state"] if st in sub.index else "NOT_APPLICABLE"
            cls, glyph = STATE_DOT[state]
            if (st, mode) in lost:
                cls, glyph = "lost", "✕"
            cells.append(f'<td><span class="dot {cls}" title="{state}">{glyph}</span></td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    return (f'<div class="scroll"><table class="mx">'
            f'<tr><th class="lbl"></th>{"".join(grp_cells)}</tr>'
            f'<tr>{"".join(hdr_cells)}</tr>{"".join(rows)}</table></div>'
            '<p class="key">'
            '<span><span class="dot cov">●</span> covered — declared and detecting</span>'
            '<span><span class="dot und">▲</span> detected, assigned to nobody</span>'
            '<span><span class="dot sil">○</span> declared, never detected</span>'
            '<span><span class="dot lost">✕</span> declared, station can no longer see it</span>'
            '<span><span class="dot na">·</span> not applicable</span></p>')


def _look_across(f: dict) -> str:
    """Extent of condition, rendered with the finding it belongs to.

    A finding at one station is half an answer. This is the other half, and it
    goes here rather than on its own page because the moment somebody wants it is
    the moment they have just read the finding.
    """
    la = f.get("look_across")
    if not la:
        return ""
    sys_cls = " sys" if la["verdict"] == "SYSTEMIC" else ""
    peers = la.get("peer_stations") or ""
    peer_html = ""
    if peers and la["verdict"] != "SYSTEMIC":
        peer_html = (f' <b>Also exposed:</b> {html.escape(peers.replace(";", ", "))}'
                     f' (via {html.escape(str(la["propagates_with"]).replace("_", " "))}'
                     f' {html.escape(str(la["asset_id"]))}).')
    return (f'<div class="la{sys_cls}"><b class="v">{html.escape(la["verdict"].replace("_", " "))}</b>'
            f'<b>Look across —</b> {html.escape(la["summary"])}{peer_html}</div>')


def _findings(reps: list[dict]) -> str:
    """A finding with no action attached is a gate, not a system.

    Where a reaction plan exists it is rendered with the finding, not on some other
    page — the cause, what to contain, what to do, and who owns it.
    """
    def lst(title, items):
        if not items:
            return ""
        li = "".join(f"<li>{html.escape(str(i))}</li>" for i in items)
        return f'<div class="rp-h">{title}</div><ol class="rp">{li}</ol>'

    out = []
    for f in reps:
        cls = SEV_CLASS.get(f["severity"], "")
        where = f.get("station") or f.get("mode") or ""
        loop = ""
        if f.get("probable_causes") or f.get("corrective_actions"):
            loop = (
                '<div class="loop">'
                f'<div class="rp-t">Root-cause attribution &amp; corrective action'
                f'{" · owned by " + html.escape(str(f["owner"])) if f.get("owner") else ""}'
                f'{" · escalate after " + str(f["escalate_after_days"]) + "d" if f.get("escalate_after_days") else ""}'
                '</div>'
                + (f'<p class="rp-m">{html.escape(f.get("failure_means", ""))}</p>'
                   if f.get("failure_means") else "")
                + lst("Probable causes, most likely first", f.get("probable_causes"))
                + lst("Contain now", f.get("containment"))
                + lst("Corrective action", f.get("corrective_actions"))
                + '<p class="rp-n">Attribution is a lookup against a reviewed list in '
                  '<code>config/station_selftest.yaml</code> — deterministic, versioned, '
                  'owned by a person. No model chooses the cause. It is where an '
                  'investigation starts, not where it ends.</p></div>')
        out.append(f'<div class="find {cls}"><b>{html.escape(f["headline"])}</b>'
                   f'<span class="where">{html.escape(f["severity"].replace("_", " "))}'
                   f'{" · " + html.escape(str(where)) if where and where != "-" else ""}</span>'
                   f'<div style="margin-top:6px">{html.escape(f["detail"])}</div>'
                   f'{_look_across(f)}{loop}</div>')
    return "".join(out)


def _spark(vals: list[float], limit: float | None, above_is_bad: bool,
           w: int = 190, h: int = 46) -> str:
    """Tiny inline SVG trend with the limit drawn on it. No JS, no libraries.

    The limit line is the point: a trend without the threshold it is heading for
    is decoration. Segments on the wrong side of it are drawn in the alert colour,
    so the eye lands on when it went out rather than on the shape of the curve.
    """
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if limit is not None:
        lo, hi = min(lo, limit), max(hi, limit)
    pad = (hi - lo) * 0.15 or 0.01
    lo, hi = lo - pad, hi + pad
    n = len(vals)
    sx = lambda i: 2 + i * (w - 4) / max(1, n - 1)
    sy = lambda v: h - 2 - (v - lo) * (h - 4) / (hi - lo)

    segs, cur, bad_cur = [], [], None
    for i, v in enumerate(vals):
        bad = limit is not None and ((v > limit) if above_is_bad else (v < limit))
        if bad_cur is None or bad == bad_cur:
            cur.append((sx(i), sy(v)))
        else:
            segs.append((bad_cur, cur + [(sx(i), sy(v))]))
            cur = [(sx(i), sy(v))]
        bad_cur = bad
    segs.append((bad_cur, cur))

    paths = "".join(
        f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" '
        f'fill="none" stroke="{"#d03b3b" if bad else "#0f3d2e"}" stroke-width="1.6"/>'
        for bad, pts in segs if len(pts) > 1)
    lim = ""
    if limit is not None:
        ly = sy(limit)
        lim = (f'<line x1="0" y1="{ly:.1f}" x2="{w}" y2="{ly:.1f}" stroke="#d03b3b" '
               f'stroke-width="1" stroke-dasharray="3,3" opacity=".75"/>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
            f'preserveAspectRatio="none" role="img">{lim}{paths}</svg>')


def _small_multiples(df: pd.DataFrame, value: str, limit: float | None,
                     above_is_bad: bool, title: str, unit: str) -> str:
    """One tiny chart per station, same scale rules, ordered by line."""
    cells = []
    for st, g in df.groupby("station_id"):
        vals = g.sort_values("production_day")[value].tolist()
        last = vals[-1]
        bad = limit is not None and ((last > limit) if above_is_bad else (last < limit))
        cells.append(
            f'<div class="sm{" bad" if bad else ""}">'
            f'<div class="sm-h"><b>{html.escape(st)}</b>'
            f'<span>{last:.3g}{html.escape(unit)}</span></div>'
            f'{_spark(vals, limit, above_is_bad)}</div>')
    lim_txt = "" if limit is None else (
        f' · dashed line = {"maximum" if above_is_bad else "minimum"} {limit}{unit}')
    return (f'<h4>{html.escape(title)}<span class="sub-note">{lim_txt}</span></h4>'
            f'<div class="smgrid">{"".join(cells)}</div>')


def _glossary(modes: dict) -> str:
    """Spell out every code before the reader meets it in a chart."""
    rows = "".join(
        f'<tr><td class="mono"><b>{html.escape(c)}</b></td>'
        f'<td>{html.escape(v.get("label") or c)}</td>'
        f'<td>{html.escape(BASIS_LABELS.get(v.get("detection_basis"), "—"))}</td>'
        f'<td style="color:var(--muted)">{html.escape(BASIS_PLAIN.get(v.get("detection_basis"), ""))}</td>'
        f'<td>{"specified" if v.get("feature_ref") else "<i>no spec, no reaction plan</i>"}</td></tr>'
        for c, v in modes.items())
    return (f'<div class="scroll"><table><tr><th>Code</th><th>What it is</th>'
            f'<th>How it is found</th><th>Meaning</th>'
            f'<th>In the control plan</th></tr>{rows}</table></div>')


def render() -> str:
    import yaml
    plan = yaml.safe_load(
        (ROOT / "config" / "control_plan.yaml").read_text(encoding="utf-8"))
    modes_cfg = plan["inspection_coverage"]["modes"]
    MODE_LABELS.update({c: (v.get("label") or c) for c, v in modes_cfg.items()})

    rep = json.loads((QDIR / "coverage_report.json").read_text(encoding="utf-8"))
    cov = pd.read_csv(MARTS / "inspection_coverage.csv")
    econ = pd.read_csv(MARTS / "station_economics.csv")
    cond_path = MARTS / "station_condition.csv"
    cond = pd.read_csv(cond_path) if cond_path.exists() else pd.DataFrame()

    sens = rep["sensitivity"]
    c1 = sens["C1_false_reject_cost_increases_with_position"]
    c2 = sens["C2_exchange_rate_decreases_with_position"]
    eff = rep.get("effective_coverage_rate")
    _es = rep.get("environment_sensitivity") or {}
    e1 = _es.get("E1_imaging_failure_costs_visual_checks_only", {})
    e2 = _es.get("E2_mechanical_failure_costs_measurement_only", {})
    esens = e1
    scope = rep["scope"]
    wear_label = (rep.get("wear_trending") or {}).get("label", "")

    by_id = {}
    for f in rep["findings"]:
        by_id.setdefault(f["id"], []).append(f)

    cover_findings = (by_id.get("UNASSIGNED_MODE", []) + by_id.get("DECLARED_SILENT", [])
                      + by_id.get("LATE_FIRST_LOOK", []))
    cond_findings = (by_id.get("CONDITION_COVERAGE_LOST", [])
                     + by_id.get("CONDITION_TRANSIENT", [])
                     + by_id.get("CONDITION_LOSS_IS_SELECTIVE", []))
    wear_findings = by_id.get("TOOLING_TRENDING_TO_LIMIT", [])
    econ_findings = (by_id.get("NO_BACKSTOP", [])
                     + by_id.get("POSITION_IS_NOT_THE_DRIVER", []))

    # One line, one mode, three positions — position is the only thing varying.
    walk = econ[(econ.line_id == econ.line_id.iloc[0]) & (econ["mode"] == "DIM-OOS")][
        ["station_id", "position", "false_reject_cost_vu", "escape_cost_vu",
         "escape_found_at", "exchange_rate"]].rename(columns={
            "station_id": "Station", "position": "Position on line",
            "false_reject_cost_vu": "Cost of scrapping a good part",
            "escape_cost_vu": "Cost if a bad part gets past",
            "escape_found_at": "Who catches it next",
            "exchange_rate": "Good parts worth rejecting to stop one bad one"})

    exposed = econ[~econ.backstop].copy()
    exposed["Inspection"] = exposed["mode"].map(lambda m: f"{MODE_LABELS.get(m, m)} ({m})")
    exposed = exposed[["station_id", "Inspection", "escape_found_at",
                       "escape_cost_vu", "exchange_rate"]].rename(columns={
            "station_id": "Station", "escape_found_at": "Who catches it next",
            "escape_cost_vu": "Cost if a bad part gets past",
            "exchange_rate": "Good parts worth rejecting to stop one bad one"})

    cond_show = pd.DataFrame()
    if not cond.empty:
        c = cond[cond.condition != "OK"].copy()
        c["Inspection at risk"] = c["mode"].map(
            lambda m: f"{MODE_LABELS.get(m, m)} ({m})")
        c["Kind of check"] = c["detection_basis"].map(lambda b: BASIS_LABELS.get(b, b))
        c["Barcode grade"] = c["datamatrix_grade"].map(lambda v: f"{v:.2f}")
        c["Repeatability mm"] = c["ref_repeatability_mm"].map(lambda v: f"{v:.4f}")
        cond_show = c[["station_id", "Inspection at risk", "Kind of check", "gated_by",
                       "cause", "Barcode grade", "Repeatability mm",
                       "longest_run_below", "condition"]].rename(columns={
            "station_id": "Station", "gated_by": "Self-test step that gates it",
            "cause": "Step that failed", "longest_run_below": "Days failed in a row",
            "condition": "Status"})

    st_path = ROOT / "data" / "raw" / "systems" / "station_selftest.csv"
    stdf = pd.read_csv(st_path) if st_path.exists() else pd.DataFrame()
    scfg = yaml.safe_load(
        (ROOT / "config" / "station_selftest.yaml").read_text(encoding="utf-8"))

    wear_path = MARTS / "tooling_wear.csv"
    wear_show = pd.DataFrame()
    if wear_path.exists():
        w = pd.read_csv(wear_path)
        w = w[w.trend_detected]
        if not w.empty:
            w = w.assign(**{
                "Measured wear / limit (mm)": w.apply(
                    lambda r: f"{r.latest_wear_mm:.3f} / {r.limit_mm:.3f}", axis=1),
                "Wear rate (mm per 1k units)": w.wear_rate_mm_per_1k_units.map("{:.4f}".format),
                "Rate 1st half → 2nd half": w.apply(
                    lambda r: f"{r.rate_first_half:.4f} → {r.rate_second_half:.4f}", axis=1),
                "Rate climbing?": w.accelerating.map(
                    lambda b: f"yes, {'' if pd.isna(b) else ''}" if b else "no"),
                "Units to reach limit": w.projected_units_to_limit.map(
                    lambda v: "—" if pd.isna(v) else f"{int(v):,}"),
                "Days to reach limit": w.projected_days_to_limit.map(
                    lambda v: "—" if pd.isna(v) else f"{int(v):,}")})
            wear_show = w[["station_id", "set_label", "measure_name",
                           "Measured wear / limit (mm)",
                           "Wear rate (mm per 1k units)", "Rate 1st half → 2nd half",
                           "Rate climbing?", "Units to reach limit",
                           "Days to reach limit"]].rename(columns={
                "station_id": "Station", "set_label": "Tooling set",
                "measure_name": "What was measured"})

    ic = scfg["imaging_check"]; mc = scfg["measurement_check"]; tc = scfg["tooling_check"]
    _charts_imaging = ""
    if not stdf.empty:
        _charts_imaging = (
            _small_multiples(stdf, "datamatrix_grade", ic["datamatrix_grade"]["min"],
                             False, "Reference 2D barcode grade — the best single summary "
                             "of imaging health", "")
            + _small_multiples(stdf, "ref_sharpness_score", ic["sharpness_score"]["min"],
                               False, "Sharpness on the reference coupon", "")
            + _small_multiples(stdf, "ref_brightness_pct",
                               ic["brightness_pct_of_reference"]["min"], False,
                               "Brightness against the reference", "%")
            + _small_multiples(stdf, "ref_repeatability_mm",
                               mc["repeatability_mm"]["max"], True,
                               "Measurement repeatability on a feature of known length", " mm"))
    # One chart block per tooling SET. The gripper places the part and the fixture
    # holds it; they wear against different things at different rates, so blending
    # them into one "tooling" number would hide both.
    _charts_wear = ""
    if not stdf.empty:
        blocks = []
        for sname, spec in (tc.get("sets") or {}).items():
            blocks.append(f'<p style="font-size:13px;margin:16px 0 2px"><b>'
                          f'{html.escape(spec["label"])}</b> — wears against '
                          f'{html.escape(spec.get("wears_against", ""))}.</p>')
            for col, m in spec["measures"].items():
                blocks.append(_small_multiples(stdf, col, m["max"], True,
                                               m.get("name", col), " mm"))
        _charts_wear = "".join(blocks)

    n_unassigned = len(rep["modes_observed_not_declared"])
    n_alert = len(rep.get("station_condition_alerts", []))

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Coverage &amp; Operating Point — Inspection Quality Intelligence</title>
<style>{CSS}</style></head><body><div class="wrap">

<a class="back" href="../index.html">← Back to overview</a>
<h1>Coverage &amp; Operating Point</h1>
<p class="sub">Is the plan looking for the right things, in the right places, at the right sensitivity?</p>
<p class="meta">{rep['window_events']:,} validated first-pass events ·
  data-quality score {rep['dq_score']} ·
  cost model v{rep['cost_model_version']} ({rep['cost_model_units']}) ·
  <strong>synthetic demonstration data</strong></p>

<div class="prov"><span class="badge">Local stage</span>
  <span>Rendered from <code>src/coverage.py</code> →
  <code>data/quality/coverage_report.json</code>. Every figure is read from that report at
  render time. The cost and acceptance figures are <b>assumptions in versioned config</b>
  (<code>config/cost_model.yaml</code>, <code>config/station_environment.yaml</code>), not
  measurements — so every conclusion below is also swept across the assumption ranges, and
  the sweep result is reported whether or not it is convenient.</span></div>

<div class="tiles">
  <div class="tile"><div class="v">{rep['coverage_rate_observed_modes']:.0%}</div>
    <div class="k">observed modes assigned to a station</div></div>
  <div class="tile"><div class="v {'warn' if eff and eff < 1 else ''}">{eff:.1%}</div>
    <div class="k">effective coverage of {rep['declared_station_mode_pairs']} station-mode pairs</div></div>
  <div class="tile"><div class="v bad">{n_unassigned}</div>
    <div class="k">mode detected and assigned to nobody</div></div>
  <div class="tile"><div class="v bad">{n_alert}</div>
    <div class="k">station below its accepted condition</div></div>
</div>

<h2>1 · Coverage — what is the plan assigned to find?</h2>
<p style="font-size:13.5px">A control plan lists the characteristics somebody thought to
write down. The floor produces whatever it produces. The gap between those two sets is
invisible unless something computes it, because a failure mode nobody is assigned to detect
generates no alert, no chart, and no absence anyone notices.</p>

<h3>The six failure modes, in plain terms</h3>
<p style="font-size:13px;color:var(--muted);margin:0 0 8px">Every code used on this page,
spelled out. The right-hand column is the one that matters: a mode can be inspected for and
still have no spec and no reaction plan behind it, which means finding it changes nothing.</p>
{_glossary(modes_cfg)}
{_matrix(cov, cond)}
{_findings(cover_findings)}
<p style="font-size:13px;color:var(--muted)">Of the
{len(rep['modes_declared'])} declared modes, <b>{len(rep['modes_specified'])}</b> has a spec
window, a capability floor and a reaction plan in <code>config/control_plan.yaml</code>. The
other {len(rep['declared_without_spec'])} are detected and then nothing happens — an event is
written and no action is defined. Detection without a reaction plan is a gate, not a system.</p>

<h2>2 · Condition — can the station still see what it was assigned to see?</h2>
<p style="font-size:13.5px">A robot does a home-position check before it trusts its own
coordinates. An inspection cell should do the same, for the same reason: everything downstream
is worthless if the instrument has quietly moved. So before it judges any production part, the
station measures a <b>known reference coupon</b> ({html.escape(rep.get('reference_artifact', ''))})
and inspects its own fixture. Because the answer is known in advance, this is not a proxy for
the station's condition — bias and repeatability are measured directly.</p>
<div class="scope" style="background:#f6f8f7">
<b>Four steps, each go / no-go, in this order:</b>
<ol style="margin:6px 0 0;padding-left:20px">
<li><b>Imaging</b> — brightness and sharpness on the coupon, and the grade of a reference 2D
  DataMatrix. Barcode grade is the best single summary of imaging health: it degrades with
  light, focus, contrast and optics all at once, on a published scale.</li>
<li><b>Measurement</b> — measure a feature of known length. The gap from truth is bias; the
  spread over repeated reads is repeatability.</li>
<li><b>Tooling</b> — the station inspects the cell's own fixture: locator wear, clamp position.
  A vision system is perfectly capable of looking at the tooling that holds the part.</li>
<li><b>Production</b> — only now does it judge real units against the control plan.</li>
</ol>
<p style="margin:10px 0 0"><b>Which step fails decides which inspections you lose, and they
point opposite ways.</b> A failed <b>imaging</b> step costs the visual checks and spares the
measurement — finding an edge survives poor contrast. A failed <b>measurement or tooling</b>
step costs the dimensional work and spares the visual checks — the part stops landing in the
same place, and that error is added straight into a size reading, but the feature is still in
the field of view. Neither shows up in yield or capability, because in both cases nothing is
<em>failing</em>. The station has stopped being able to do part of its job.</p>
</div>
{_table(cond_show, {"min_illumination_ratio", "latest_illumination_ratio", "margin",
                    "longest_run_below"}) if not cond_show.empty else ""}
{_findings(cond_findings)}

<h3>What each station's self-test has been reporting</h3>
<p style="font-size:13.5px">Every station, every day, measured against the same reference
coupon. Red means the reading is on the wrong side of the limit — the point is not the shape
of the curve, it is when it crossed.</p>
{_charts_imaging}

<h3>Tooling — still inside spec, and where it is heading</h3>
{_charts_wear}
<p style="font-size:13.5px">Two tooling sets are inspected separately, and that is the point: the end-of-arm tooling <b>places</b> the part and the fixture <b>holds</b> it. Both push on the same reading at step 2, so a repeatability failure on its own cannot tell you which one moved. The tooling step also produces a number the go / no-go throws away: how fast the wear is moving. Only stations whose wear has actually risen are shown — on a
healthy fixture the "rate" is measurement noise, and projecting noise produces a
confident-looking number built on nothing.</p>
{_table(wear_show) if not wear_show.empty else '<p style="font-size:13px;color:var(--muted)">No station shows a measurable wear trend.</p>'}
<div class="scope" style="border-left:5px solid var(--warn)">
<b>⚠️ What that projection is, and what it is not.</b>
<p style="margin:6px 0 0">{html.escape(wear_label)}</p>
<p style="margin:8px 0 0">It is arithmetic on a measured characteristic against a published
limit — the same thing tool-wear compensation has done on a control chart for decades. There
is no failure history here, no reliability model, and no claim about when anything will stop
working.</p>
<p style="margin:8px 0 0">The <b>rate climbing</b> column is the one worth acting on. Steady
wear is a maintenance schedule. Wear that is speeding up means something else changed — and
that is a product question before it is a tooling one, because the parts made during the
acceleration are the ones worth going back and looking at.</p>
</div>
{_findings(wear_findings)}

<h2>3 · Operating point — what does being wrong cost here?</h2>
<div class="scope" style="background:#f6f8f7">
<b>There are only two ways an inspection can be wrong, and they cost different amounts.</b>
<ul style="margin:6px 0 0">
<li><b>It rejects a good part.</b> You scrap or rework something that was fine. That gets more
  expensive the further down the line you are, because more work has gone into the part.</li>
<li><b>It passes a bad part.</b> The defect moves on. That costs whatever it takes to find and
  fix it wherever it <em>does</em> get caught — the next station, final test, or the customer.</li>
</ul>
<p style="margin:8px 0 0">So every station faces one question: <b>how many good parts is it
worth wrongly rejecting in order to stop one bad part getting through?</b> That number is the
last column in both tables below. A big number means set the station to catch everything, even
at the price of scrapping good parts. A number near 1 means being trigger-happy costs about as
much as letting things through, so it has to be tuned carefully both ways.</p>
<p style="margin:8px 0 0;color:var(--muted)">Costs are in <b>relative value units</b> — a part
at the first station is worth 1. No dollar figures appear anywhere, because that would imply a
costing study nobody has done.</p>
</div>
<h3>Walking down one line, checking one thing — only the position changes</h3>
{_table(walk, set(walk.columns) - {"Station", "Who catches it next"})}
<h3>Inspections with nothing downstream to catch them — the last station is the last chance</h3>
{_table(exposed, set(exposed.columns) - {"Station", "Inspection", "Who catches it next"})}
{_findings(econ_findings)}

<h2>4 · Which conclusions survived being stress-tested</h2>
<p style="font-size:13.5px">Every cost and acceptance figure on this page is an assumption,
not a measurement. So no conclusion is reported from one set of numbers. Each claim is
re-tested against every combination in a range of plausible assumptions, and what is reported
is how often it held — including when it did not.</p>
<div class="scroll"><table>
<tr><th>Claim in plain terms</th><th>Held in</th><th>Verdict</th></tr>
<tr><td>Scrapping a good part gets more expensive the further down the line you are</td>
    <td class="n mono">{c1['holds_in']} / {c1['of']}</td>
    <td><b style="color:var(--good)">HOLDS</b></td></tr>
<tr><td>The further down the line, the less aggressive a station should be</td>
    <td class="n mono">{c2['holds_in']} / {c2['of']}</td>
    <td><b style="color:var(--crit)">DOES NOT HOLD</b></td></tr>
<tr><td>A failed imaging self-test costs the visual checks and spares the measurement</td>
    <td class="n mono">{e1.get('holds_in', 0)} / {e1.get('of', 0)}</td>
    <td><b style="color:{'var(--good)' if e1.get('robust') else 'var(--crit)'}">
        {'HOLDS' if e1.get('robust') else 'DOES NOT HOLD'}</b></td></tr>
<tr><td>A failed measurement or tooling self-test costs the size checks and spares the visual ones</td>
    <td class="n mono">{e2.get('holds_in', 0)} / {e2.get('of', 0)}</td>
    <td><b style="color:{'var(--good)' if e2.get('robust') else 'var(--crit)'}">
        {'HOLDS' if e2.get('robust') else 'DOES NOT HOLD'}</b></td></tr>
</table></div>
<p style="font-size:13px;color:var(--muted)">The second row is the interesting one, and it is
the reason this section exists. The intuitive rule is that a station near the end of the line
should be less trigger-happy, because the parts it is scrapping are worth more. Half of that
is right — scrapping a good part really does get more expensive every step down the line, in
every single combination tested. But the other half does not survive: what actually decides
how aggressive a station should be is <b>who catches the defect next if this one misses it</b>.
A station sitting last, right before an expensive final test, has to be nearly as strict as
the first station on the line. A test in <code>tests/test_coverage.py</code> asserts this
claim <em>fails</em>, so quietly tuning the assumptions until the tidy story holds turns the
suite red instead of going unnoticed.<br><br>
The last two rows exclude combinations where the limit was set so loose that nothing was lost
at all — {e1.get('vacuous_combinations_excluded', 0)} and
{e2.get('vacuous_combinations_excluded', 0)} respectively. Those are vacuous, not
counterexamples, and counting them as failures would understate a claim that is actually
holding.</p>

<h2>5 · What this does not claim</h2>
<div class="scope">
<p style="margin:0 0 8px">{html.escape(scope['note'])}</p>
<ul>
<li><b>No image data and no vision model.</b> This reads inspection <em>results</em> and cell
  process measures — illumination, exposure, focus score, fixture repeatability. Scalars, not
  pixels.</li>
<li><b>No failure prediction.</b> It reports a condition against an acceptance level. Saying
  when a lamp will stop working is a prognostics claim and nothing here supports one — there
  is no failure history and no model fitted to failures. A test asserts that no finding
  contains forecast language.</li>
<li><b>The cost and acceptance figures are assumptions, not measurements.</b> They are marked
  PLACEHOLDER in config with the study that would earn them named alongside. That is why the
  sweep exists.</li>
<li><b>The data is synthetic and generated in this repository</b> with the signals injected
  on purpose, so the checks have something real to catch and the tests can assert on known
  numbers.</li>
</ul>
</div>

<p class="note">Coverage audit and cost model in <code>src/coverage.py</code>; station
telemetry generated by <code>src/generate_environment.py</code>; assumptions in
<code>config/cost_model.yaml</code> and <code>config/station_environment.yaml</code>; marts in
<code>data/marts/inspection_coverage.csv</code>,
<code>station_economics.csv</code> and <code>station_condition.csv</code>. The station
health quoted in the condition finding is read from the Databricks-produced
<code>station_scorecard.csv</code>, which this stage never writes to — a test asserts it.
<a href="../index.html" style="color:var(--accent);font-weight:600">← Back to overview</a></p>

</div></body></html>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
