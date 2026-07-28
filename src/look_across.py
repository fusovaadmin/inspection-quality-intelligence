"""
Look-across — extent of condition for every confirmed finding.

THE PROBLEM THIS SOLVES
-----------------------
Every finding this platform produces so far is about one station. That is half an
answer. The other half is the question a quality engineer asks next and that no
per-station dashboard ever answers: WHERE ELSE DOES THIS ALREADY EXIST?

Skipping it is how a plant fixes the same problem four times. It is D6/D7 of an
8D — you have not closed anything until you know the extent of the condition, and
"we replaced the gripper on S8" is not a closure, it is an instance.

WHY IT CANNOT BE READ OUT OF THE INSPECTION DATA
------------------------------------------------
Because the thing that propagates a fault is usually not the station. A robot arm
serving three stations carries its tool-centre-point error to all three. An
illuminator model from a bad batch dims everywhere it was fitted. A fixture design
reused down a line wears the same way on every copy.

So a finding at S8 does not mean "check the stations near S8". It means "check
every station that shares the asset this fault travels with", and that mapping has
to be declared. It lives in config/station_selftest.yaml under `asset_topology`.

THREE ANSWERS, AND THEY ARE DIFFERENT PROBLEMS
----------------------------------------------
  LOCAL          nothing else shares the asset, and no peer is near the limit.
                 Fix the instance.
  SHARED_ASSET   other stations run the same robot / illuminator / fixture design.
                 The fault travels with the asset, so they are exposed whether or
                 not they have tripped yet. Inspect them before they do.
  SYSTEMIC       the condition is present everywhere. That is not N local
                 problems, it is one problem with the plan or the design, and
                 fixing it station by station is the expensive way to never
                 finish.

Run:  python -m src.look_across   (after src.coverage)
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .coverage import ENV_MODEL, ENV_TELEMETRY, MARTS, QDIR, REPORT, load_yaml

OUT = MARTS / "look_across.csv"

# Which self-test check each finding id is attributed to, so the right
# propagation rule is used. A coverage gap is a control-plan question and
# propagates with the plan, not with any physical asset.
CHECK_FOR_CAUSE = {
    "imaging": "imaging_check",
    "tooling/eoat": "tooling_check__eoat",
    "tooling/fixture": "tooling_check__fixture",
    "measurement": "measurement_check",
}


def check_for(cause: str) -> str:
    """Most specific attribution wins: a named tooling set beats 'measurement'."""
    for key in ("tooling/eoat", "tooling/fixture", "imaging"):
        if key in (cause or ""):
            return CHECK_FOR_CAUSE[key]
    return CHECK_FOR_CAUSE["measurement"]


def peers_sharing_asset(station: str, check: str, cfg: dict) -> tuple[str, str, list[str]]:
    """(asset kind, asset id, the other stations that share it)."""
    topo = cfg["asset_topology"]
    kind = topo["propagates_with"].get(check)
    if not kind:
        return "", "", []
    mine = (topo["stations"].get(station) or {}).get(kind)
    if not mine:
        return kind, "", []
    peers = sorted(s for s, a in topo["stations"].items()
                   if s != station and a.get(kind) == mine)
    return kind, mine, peers


def measure_for(check: str, cfg: dict) -> tuple[str, float, bool] | None:
    """The single measure that best represents this check, and its limit.

    Returns (column, limit, above_is_bad). Used to rank every other station by how
    close it already is to the same limit — a peer at 80% of the limit has not
    tripped, and is still the next one to.
    """
    if check == "imaging_check":
        return "datamatrix_grade", cfg["imaging_check"]["datamatrix_grade"]["min"], False
    if check == "measurement_check":
        return ("ref_repeatability_mm",
                cfg["measurement_check"]["repeatability_mm"]["max"], True)
    sets = (cfg.get("tooling_check") or {}).get("sets") or {}
    sname = "eoat" if check.endswith("eoat") else "fixture"
    measures = (sets.get(sname) or {}).get("measures") or {}
    tracked = [(c, m) for c, m in measures.items() if m.get("trend")]
    if not tracked:
        return None
    col, m = tracked[0]
    return col, m["max"], True


def rank_peers(latest: pd.DataFrame, station: str, col: str, limit: float,
               above_is_bad: bool, approaching: float) -> list[dict]:
    """Every other station on the same measure, worst first.

    Reported whether or not they have tripped. A station at 80% of its limit is
    the answer to "who is next", and it is invisible on a per-station view.
    """
    rows = []
    for r in latest.itertuples():
        if r.station_id == station:
            continue
        v = float(getattr(r, col))
        if above_is_bad:
            frac = v / limit if limit else 0.0
            breached = v > limit
        else:
            frac = (limit / v) if v else float("inf")
            breached = v < limit
        state = ("BREACHED" if breached
                 else "APPROACHING" if frac >= approaching else "OK")
        rows.append(dict(station_id=r.station_id, value=round(v, 5), limit=limit,
                         fraction_of_limit=round(frac, 3), state=state))
    order = {"BREACHED": 0, "APPROACHING": 1, "OK": 2}
    return sorted(rows, key=lambda x: (order[x["state"]], -x["fraction_of_limit"]))


def units_in_window(first_out: str | None, telem: pd.DataFrame, station: str) -> dict:
    """Containment scope: what was produced while the condition existed.

    A finding without this is not actionable — somebody still has to work out
    which units are suspect, and that is the part that decides whether anything
    gets held.
    """
    if not first_out:
        return {"from": None, "to": None, "units": None}
    g = telem[(telem.station_id == station)
              & (telem.production_day >= first_out)].sort_values("production_day")
    if g.empty:
        return {"from": first_out, "to": None, "units": None}
    start = float(g.units_processed.iloc[0])
    end = float(g.units_processed.iloc[-1])
    return {"from": str(g.production_day.iloc[0]), "to": str(g.production_day.iloc[-1]),
            "units": int(round(end - start))}


def build(report: dict, cfg: dict, cond: pd.DataFrame, cov: pd.DataFrame,
          telem: pd.DataFrame) -> list[dict]:
    la = cfg["look_across"]
    approaching = la["approaching_fraction_of_limit"]
    latest = telem.sort_values("production_day").groupby("station_id").tail(1)
    n_stations = telem.station_id.nunique()
    out = []

    for f in report["findings"]:
        # --- control-plan gaps: does the same hole exist everywhere? ----------
        if f["id"] in ("UNASSIGNED_MODE", "DECLARED_SILENT", "LATE_FIRST_LOOK"):
            mode = f["mode"]
            rows = cov[cov["mode"] == mode]
            if f["id"] == "UNASSIGNED_MODE":
                affected = sorted(rows.loc[rows.state == "UNDECLARED_DETECTED",
                                           "station_id"].unique())
            else:
                affected = sorted(rows.loc[rows.declared, "station_id"].unique())
            frac = len(affected) / n_stations if n_stations else 0
            systemic = frac >= la["systemic_if_fraction_of_stations"]
            out.append(dict(
                finding_id=f["id"], station=f.get("station") or "-", mode=mode,
                verdict="SYSTEMIC" if systemic else "SHARED_ASSET" if len(affected) > 1 else "LOCAL",
                propagates_with="control plan", asset_id="-",
                peer_stations=";".join(affected),
                n_affected=len(affected), n_stations=n_stations,
                summary=(f"Present at {len(affected)} of {n_stations} stations. "
                         + ("This is one problem with the control plan, not "
                            f"{len(affected)} local ones — fixing it station by station "
                            "is the expensive way to never finish."
                            if systemic else
                            "Confined to part of the floor, so the plan is inconsistent "
                            "rather than uniformly wrong — worth knowing which is which.")),
                containment_from=None, containment_to=None, units_in_window=None))
            continue

        # --- condition findings: what shares the asset this travels with? -----
        if f["id"] not in ("CONDITION_COVERAGE_LOST", "TOOLING_TRENDING_TO_LIMIT"):
            continue
        st = f.get("station")
        if not st:
            continue
        cause = f.get("cause") or ("tooling/" + f["tooling_set"]
                                   if f.get("tooling_set") else "measurement")
        check = check_for(cause)
        kind, asset, peers = peers_sharing_asset(st, check, cfg)

        spec = measure_for(check, cfg)
        ranked = rank_peers(latest, st, *spec, approaching) if spec else []
        exposed = [p for p in ranked if p["station_id"] in peers]
        hot = [p for p in ranked if p["state"] != "OK"]

        c = cond[(cond.station_id == st) & (cond.condition != "OK")]
        first_out = (str(c.first_day_below.iloc[0])
                     if not c.empty and pd.notna(c.first_day_below.iloc[0]) else None)
        scope = units_in_window(first_out, telem, st)

        verdict = "SHARED_ASSET" if peers else "LOCAL"
        bits = []
        if peers:
            bits.append(f"This travels with the {kind.replace('_', ' ')} "
                        f"({asset}), not with the station — {', '.join(peers)} "
                        f"{'share' if len(peers) > 1 else 'shares'} it and "
                        f"{'are' if len(peers) > 1 else 'is'} exposed whether or not "
                        f"{'they have' if len(peers) > 1 else 'it has'} tripped yet.")
        else:
            bits.append(f"Nothing else shares the {kind.replace('_', ' ')} ({asset}), "
                        f"so the asset itself does not spread this.")
        if hot:
            worst = hot[0]
            bits.append(f"On the same measure, {worst['station_id']} is at "
                        f"{worst['fraction_of_limit']:.0%} of the limit "
                        f"({worst['state'].lower()}) — that is the next one, and it is "
                        f"invisible from a single-station view.")
        else:
            bits.append("No other station is near the same limit today.")
        if scope["units"]:
            bits.append(f"Containment scope: roughly {scope['units']:,} units through "
                        f"{st} between {scope['from']} and {scope['to']}, the window in "
                        f"which the condition existed.")

        out.append(dict(
            finding_id=f["id"], station=st, mode=f.get("mode", "-"),
            verdict=verdict, propagates_with=kind, asset_id=asset,
            peer_stations=";".join(peers), n_affected=len(peers) + 1,
            n_stations=n_stations, summary=" ".join(bits),
            containment_from=scope["from"], containment_to=scope["to"],
            units_in_window=scope["units"]))
    return out


def run() -> list[dict]:
    cfg = load_yaml(ENV_MODEL)
    if not cfg.get("look_across", {}).get("enabled"):
        return []
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    cond = pd.read_csv(MARTS / "station_condition.csv")
    cov = pd.read_csv(MARTS / "inspection_coverage.csv")
    telem = pd.read_csv(ENV_TELEMETRY)

    rows = build(report, cfg, cond, cov, telem)
    df = pd.DataFrame(rows)
    MARTS.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)

    # Fold it back into the report so the pages render it with the finding.
    by_key = {}
    for r in rows:
        by_key.setdefault((r["finding_id"], r["station"], r["mode"]), r)
    for f in report["findings"]:
        hit = by_key.get((f["id"], f.get("station") or "-", f.get("mode", "-")))
        if hit:
            f["look_across"] = {k: hit[k] for k in
                                ("verdict", "propagates_with", "asset_id",
                                 "peer_stations", "n_affected", "n_stations",
                                 "summary", "containment_from", "containment_to",
                                 "units_in_window")}
    report["look_across"] = {
        "note": ("Extent of condition. A finding at one station is half an answer; "
                 "the other half is where else the same condition already exists. "
                 "What propagates a fault is usually the shared asset, not the "
                 "station, so the topology is declared in config."),
        "verdicts": pd.Series([r["verdict"] for r in rows]).value_counts().to_dict(),
    }
    QDIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return rows


def main() -> None:
    rows = run()
    print(f"Look-across over {len(rows)} findings -> {OUT}")
    for r in rows:
        print(f"  [{r['verdict']:13}] {r['finding_id']:26} {r['station']:3} "
              f"{r['mode'][:22]:22} via {r['propagates_with']} {r['asset_id']}")
        if r["peer_stations"]:
            print(f"                  also exposed: {r['peer_stations'].replace(';', ', ')}")


if __name__ == "__main__":
    main()
