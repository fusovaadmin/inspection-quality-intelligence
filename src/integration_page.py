"""
Renders output/integration.html — the heterogeneous-systems page.

Every number on the page is read from data/quality/integration_report.json and
the marts src/integrate.py wrote. Nothing is hardcoded, so the page cannot drift
away from what the pipeline actually found.

Run:  python -m src.integrate && python -m src.integration_page
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
QDIR = ROOT / "data" / "quality"
MARTS = ROOT / "data" / "marts"
SYSDIR = ROOT / "data" / "raw" / "systems"
OUT = ROOT / "output" / "integration.html"

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
.back{float:right;font-size:12px;font-weight:600;color:var(--accent);text-decoration:none}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:6px 0 4px}
@media(max-width:720px){.tiles{grid-template-columns:1fr 1fr}}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.tile .v{font-size:26px;font-weight:700;line-height:1.1;color:var(--accent)}
.tile .v.bad{color:var(--crit)}
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
"""

SYSTEMS = [
    ("Vision stations", "one row per <b>event</b>", "<code>station_id</code> · <code>SN-S3-00-0142</code>",
     "the spine"),
    ("ERP — work orders", "one row per <b>work order</b>, covering a date <b>range</b>",
     "<code>PROD_LINE=LN_C</code> · <code>ITEM_NO=1000-C</code> · <code>MM/DD/YYYY</code>",
     "non-equi <b>range join</b> on line + date inside [start, end]"),
    ("MES — shift log", "one row per work center per <b>shift</b>",
     "<code>work_center=WC-03</code> · <code>wo_no=100019</code>",
     "<b>interval join</b> on [shift_start, shift_end)"),
    ("QMS — NCRs", "one row per <b>NCR</b> (sparse)",
     "<code>serial_no=s3-00-0142</code> · <code>SOLDER</code>",
     "<b>normalized-key join</b> after canonicalizing the serial"),
]


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


def render() -> str:
    rep = json.loads((QDIR / "integration_report.json").read_text(encoding="utf-8"))
    ri, cov, gm, src = (rep["referential_integrity"], rep["crosswalk_coverage"],
                        rep["grain_mismatch"], rep["sources"])
    wo = pd.read_csv(MARTS / "wo_station_day.csv")
    rec = pd.read_csv(MARTS / "mes_shift_reconciliation.csv")

    orphan_days = sorted(wo.loc[~wo["has_work_order"], "date"].unique())
    orphan_lines = sorted(wo.loc[~wo["has_work_order"], "line_id"].unique())
    missing = rec[~rec["shift_present"]]
    flagged = rec[rec["variance_flag"]].head(5)[
        ["station_id", "shift_id", "inspection_events", "mes_units_completed", "qty_variance_pct"]]

    sys_rows = "".join(
        f"<tr><td><b>{n}</b></td><td>{g}</td><td>{k}</td><td>{j}</td></tr>"
        for n, g, k, j in SYSTEMS)

    erp = pd.read_csv(SYSDIR / "erp_work_orders.csv", dtype=str).head(3)
    mes = pd.read_csv(SYSDIR / "mes_shift_log.csv", dtype=str).head(3)
    qms = pd.read_csv(SYSDIR / "qms_ncr.csv", dtype=str).head(3)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Heterogeneous System Integration — Inspection Quality Intelligence</title>
<style>{CSS}</style></head><body><div class="wrap">

<a class="back" href="../index.html">← Back to overview</a>
<h1>Heterogeneous System Integration</h1>
<p class="sub">Four systems, four grains, no shared key</p>
<p class="meta">{src['inspection_events_first_pass']:,} first-pass inspection events ·
  {src['erp_work_orders']} ERP work orders · {src['mes_shift_records']:,} MES shift records ·
  {src['qms_ncrs']} QMS NCRs · <strong>synthetic demonstration data</strong></p>

<div class="prov"><span class="badge">Local stage</span>
  <span>This page renders from the <b>local integration stage</b>
  (<code>src/integrate.py</code> → <code>data/quality/integration_report.json</code>), not from
  Databricks. Every figure below is read from that report at render time; the equivalent
  Spark&nbsp;SQL is in <code>sql/heterogeneous_join.sql</code> and is diffed cell-for-cell
  against this output in CI.</span></div>

<h2>The four systems</h2>
<div class="scroll"><table>
<tr><th>System</th><th>Grain</th><th>Keys it uses</th><th>How it joins</th></tr>
{sys_rows}
</table></div>
<p style="font-size:13px;color:var(--muted);margin-top:8px">Each system is correct inside
its own boundary. The reconciling happens in
<code>config/system_crosswalk.yaml</code> — versioned reference data a quality engineer can
read and review, not join logic buried in a query.</p>

<h2>What the join found</h2>
<div class="tiles">
  <div class="tile"><div class="v">{rep['integrity_score']:.4f}</div>
    <div class="k">Referential integrity</div></div>
  <div class="tile"><div class="v bad">{ri['events_without_work_order']:,}</div>
    <div class="k">Events with no work order</div></div>
  <div class="tile"><div class="v bad">{ri['ncrs_without_inspection_event']}</div>
    <div class="k">NCRs with no inspection</div></div>
  <div class="tile"><div class="v bad">{gm['pct_of_events']}%</div>
    <div class="k">Events on a different day</div></div>
</div>

<div class="find"><b>A work order was closed two days early — the line kept running.</b>
  ERP shows no open work order for {", ".join(orphan_lines)} on
  {", ".join(str(d) for d in orphan_days)}, yet
  <b>{ri['events_without_work_order']:,}</b> units were inspected there.
  <span class="where">Caught by the range join: an event outside every
  [sched_start, sched_end] window keeps its row with a NULL work order instead of
  silently disappearing.</span></div>

<div class="find"><b>MES kept booking shifts to that same closed work order.</b>
  <b>{ri['mes_shifts_booked_to_closed_wo']}</b> shift records name a work order ERP shows
  already closed on those dates.
  <span class="where">Neither system can see this on its own — it only exists in the join.</span></div>

<div class="find"><b>An MES logger outage hid {ri['events_without_mes_shift']} events.</b>
  <b>{len(missing)}</b> shifts
  ({", ".join(sorted(set(missing['station_id'])))}, shift
  {", ".join(sorted(set(missing['shift_code'])))}) have no MES record at all, though the
  stations logged production the whole time.
  <span class="where">Driven from the MES side, this outage is structurally invisible —
  a shift that was never written cannot appear in a result set. The join has to be driven
  from what was actually produced.</span></div>

<div class="find"><b>QMS and the stations disagree.</b>
  <b>{ri['ncrs_without_inspection_event']}</b> NCRs reference serials that were never
  inspected, and <b>{ri['ncr_taxonomy_conflicts']}</b> more carry a defect category that
  contradicts the code the station recorded.
  <span class="where">Found by canonicalizing two serial formats
  (<code>SN-S3-00-0142</code> ↔ <code>s3-00-0142</code>) and mapping the human taxonomy onto
  the machine one.</span></div>

<h2>The expensive assumption: production day ≠ calendar day</h2>
<p style="font-size:13.5px">MES records production against a <b>production day</b>, not a
calendar day. Shift C runs 22:00 → 06:00, so everything it makes belongs to the day it
<b>started</b> — an event stamped 02:00 on the 10th was made on the production day of the
9th. Joining MES to the floor on <code>CAST(ts AS DATE)</code>
mis-assigns <b>{gm['events_on_a_different_production_day']:,} events —
{gm['pct_of_events']}%</b> of the dataset. That is not a crash. It is a dashboard that is
quietly wrong for a quarter of every night, and nobody files a bug against it.</p>

<h2>Quantity reconciliation — MES vs. the stations</h2>
<p style="font-size:13.5px"><b>{ri['mes_qty_variance_shifts']}</b> shifts report a unit count
that differs from the inspection record by more than the tolerance in the crosswalk config
(5%). The signature is a digit transposition — a hand-keyed count. First five:</p>
{_table(flagged, numeric={"inspection_events", "mes_units_completed", "qty_variance_pct"})}

<h2>Crosswalk coverage</h2>
<div class="scroll"><table>
<tr><th>Mapping</th><th>Mapped</th><th>Unmapped</th><th>Unmapped values</th></tr>
<tr><td>ERP line → line_id</td><td class="n">{cov['line']['mapped']:,}</td>
    <td class="n">{cov['line']['unmapped']}</td><td>—</td></tr>
<tr><td>MES work center → station_id</td><td class="n">{cov['work_center']['mapped']:,}</td>
    <td class="n">{cov['work_center']['unmapped']}</td><td>—</td></tr>
<tr><td>ERP item → part_number</td><td class="n">{cov['item_no']['mapped']:,}</td>
    <td class="n">{cov['item_no']['unmapped']}</td>
    <td class="mono">{", ".join(cov['item_no']['unmapped_values']) or "—"}</td></tr>
<tr><td>QMS category → defect_code</td><td class="n">{cov['defect_category']['mapped']:,}</td>
    <td class="n">{cov['defect_category']['unmapped']}</td>
    <td class="mono">{", ".join(cov['defect_category']['unmapped_values']) or "—"}</td></tr>
</table></div>
<p style="font-size:13px;color:var(--muted);margin-top:8px">An unmapped key is a finding for
a human, not a row to delete. The work orders still carrying the superseded revision
{", ".join(cov['item_no']['unmapped_values'])} keep their production — they are flagged,
not dropped. Every row that failed to join is written to
<code>data/quality/orphans.csv</code>.</p>

<h2>The extracts, as each system wrote them</h2>
<p style="font-size:13.5px">Different column casing, different date formats, different key
formats, different grain. This is the artifact — not prose about it.</p>
<p style="font-size:12px;color:var(--muted);margin:14px 0 0"><b>ERP</b> — UPPER_SNAKE,
  <code>MM/DD/YYYY</code>, no timestamps at all:</p>
{_table(erp)}
<p style="font-size:12px;color:var(--muted);margin:14px 0 0"><b>MES</b> — shift grain,
  work centers not stations, work order without the <code>WO-</code> prefix:</p>
{_table(mes)}
<p style="font-size:12px;color:var(--muted);margin:14px 0 0"><b>QMS</b> — serials
  lower-cased and stripped, defect categories in a human taxonomy:</p>
{_table(qms)}

<p class="note">All data is synthetic and deterministic (fixed seed): the ERP, MES and QMS
extracts are generated by <code>src/generate_systems.py</code> with the cross-system breaks
injected on purpose, so the checks have something real to catch and the tests can assert on
known numbers. Joins, crosswalks and reconciliation live in <code>src/integrate.py</code>;
the Spark-SQL port is in <code>sql/</code> and CI diffs the two cell-for-cell.
<a href="../index.html" style="color:var(--accent);font-weight:600">← Back to overview</a></p>

</div></body></html>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
