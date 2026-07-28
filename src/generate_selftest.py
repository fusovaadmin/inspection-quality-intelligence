"""
Synthetic station self-test records — the cell proving it can still do its job.

A robot does a home-position check before it trusts its own coordinates. An
inspection cell should do the same, for the same reason: everything downstream is
worthless if the instrument has quietly moved.

Each row is one scheduled self-test: the station measures a KNOWN reference
coupon and inspects its own fixture, and reports what it got. Because the answer
is known in advance, bias and repeatability are measured directly rather than
inferred — this is not a proxy for the station's condition.

Emitted per station per production day:
  * imaging     — brightness and sharpness against the reference coupon, and the
                  grade of a reference 2D DataMatrix. Barcode grade is the best
                  single summary of imaging health: it degrades with light, focus,
                  contrast and optics all at once.
  * measurement — bias and repeatability against a reference feature of known
                  length. Truth is known, so error is measurable.
  * tooling     — the station inspects the cell's OWN fixture: locator wear and
                  clamp offset.
  * throughput  — units processed, because tooling wears with throughput and not
                  with the calendar.

THREE SIGNALS ARE INJECTED, and the first two fail in OPPOSITE directions —
which is the whole reason a single health light is not enough:

  * S9 — the lamp dims. Barcode grade and sharpness fall past their limits, so
    the IMAGING check fails and the station loses its appearance inspections.
    Its measurement check stays perfect: finding an edge survives poor light.

  * S2 — the fixture wears. Presentation scatter grows, so the MEASUREMENT check
    fails on repeatability and the station loses its dimensional inspection. Its
    imaging check stays perfect. Note the ordering, which is the realistic part:
    the measurement goes out of tolerance BEFORE the locator reaches its own wear
    limit — the locator is still inside spec and trending toward it.

  * S6 — a two-day imaging dip that recovers on its own. A dirty coupon, or
    somebody left a light on. Must produce a WATCH and NOT an alert.

Deterministic: fixed seed and start date, identical on every run.
Run:  python -m src.generate_selftest
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "station_selftest.yaml"
OUT = ROOT / "data" / "raw" / "systems" / "station_selftest.csv"

SEED = 42
START_DATE = "2026-04-01"
N_DAYS = 60
STATIONS = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]
LINE_OF = {"S1": "LINE-A", "S2": "LINE-A", "S3": "LINE-A",
           "S4": "LINE-B", "S5": "LINE-B", "S6": "LINE-B",
           "S7": "LINE-C", "S8": "LINE-C", "S9": "LINE-C"}

# Healthy baselines.
BASE_BRIGHTNESS = 100.0
BASE_SHARPNESS = 0.94
BASE_GRADE = 3.8          # 0-4 DataMatrix grade
BASE_BIAS = 0.002         # mm against the known reference feature
BASE_REPEAT = 0.006       # mm spread over repeated reads
BASE_LOCATOR = 0.020      # mm measured wear — fixture
BASE_CLAMP = 0.015        # mm — fixture
BASE_GRIPPER = 0.040      # mm — end-of-arm tooling, pads contact the part every cycle
BASE_TCP = 0.020          # mm — end-of-arm tooling, tool centre point offset
BASE_JAW = 0.015          # mm — end-of-arm tooling, jaw parallelism
UNITS_PER_DAY = 180

# --- Injected signals --------------------------------------------------------
IMAGING_STATION = "S9"        # lamp dims -> imaging fails, measurement unaffected
GRADE_END = 1.4               # crosses the 2.0 minimum partway through

TOOLING_STATION = "S2"        # fixture wears -> measurement fails, imaging fine
REPEAT_END = 0.024            # crosses the 0.015 limit partway through
LOCATOR_END = 0.128           # approaches the 0.150 limit WITHOUT crossing it
LOCATOR_QUAD = 0.00003        # wear accelerates: rate itself is increasing

TRANSIENT_STATION = "S6"
TRANSIENT_DAYS = (30, 31)     # two days only — deliberately under the alert rule
TRANSIENT_GRADE = 1.7

# End-of-arm tooling. S8's gripper pads wear past their limit, which produces the
# SAME repeatability symptom as S2's worn fixture from a completely different
# cause — that is the whole reason the two tooling sets are inspected separately.
EOAT_FAIL_STATION = "S8"
GRIPPER_END_MM = 0.295        # crosses the 0.250 limit late in the window
S8_REPEAT_END = 0.021         # the symptom EOAT wear produces at step 2

# S5's tool centre point drifts STEADILY and stays inside its limit — the contrast
# case. Steady wear is a maintenance schedule; S2's accelerating wear is not.
EOAT_TREND_STATION = "S5"
TCP_END_MM = 0.104            # against a 0.120 limit — approaches, never crosses


def _frac(day: int) -> float:
    return day / (N_DAYS - 1)


def generate() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    dates = pd.date_range(START_DATE, periods=N_DAYS, freq="D")
    rows = []
    for di, day_ts in enumerate(dates):
        f = _frac(di)
        for st in STATIONS:
            brightness = BASE_BRIGHTNESS + float(rng.normal(0, 0.5))
            sharpness = BASE_SHARPNESS + float(rng.normal(0, 0.004))
            grade = BASE_GRADE + float(rng.normal(0, 0.03))
            bias = BASE_BIAS + float(rng.normal(0, 0.0004))
            repeat = BASE_REPEAT + float(abs(rng.normal(0, 0.0003)))
            locator = BASE_LOCATOR + float(abs(rng.normal(0, 0.0007)))
            clamp = BASE_CLAMP + float(abs(rng.normal(0, 0.0015)))
            gripper = BASE_GRIPPER + float(abs(rng.normal(0, 0.0012)))
            tcp = BASE_TCP + float(abs(rng.normal(0, 0.0010)))
            jaw = BASE_JAW + float(abs(rng.normal(0, 0.0009)))

            if st == IMAGING_STATION:
                # The lamp fades. Everything that depends on contrast fades with it;
                # the geometry measurement does not care.
                grade = BASE_GRADE - f * (BASE_GRADE - GRADE_END) + float(rng.normal(0, 0.03))
                brightness = BASE_BRIGHTNESS - f * 22.0 + float(rng.normal(0, 0.5))
                sharpness = BASE_SHARPNESS - f * 0.10 + float(rng.normal(0, 0.004))

            if st == TOOLING_STATION:
                # The fixture wears. The part stops landing in the same place, so
                # repeatability degrades; imaging is untouched. Wear accelerates.
                repeat = (BASE_REPEAT + f * (REPEAT_END - BASE_REPEAT)
                          + float(abs(rng.normal(0, 0.0003))))
                lin = (LOCATOR_END - BASE_LOCATOR - LOCATOR_QUAD * (N_DAYS - 1) ** 2) / (N_DAYS - 1)
                locator = (BASE_LOCATOR + lin * di + LOCATOR_QUAD * di ** 2
                           + float(abs(rng.normal(0, 0.0007))))
                clamp = BASE_CLAMP + f * 0.020 + float(abs(rng.normal(0, 0.0015)))

            if st == EOAT_FAIL_STATION:
                # Gripper pads contact the part on every cycle, so they are the
                # fastest-wearing thing in the cell. The robot stops placing the
                # part in the same spot, and step 2 sees exactly what a worn
                # fixture looks like — from a different tooling set entirely.
                gripper = (BASE_GRIPPER + f * (GRIPPER_END_MM - BASE_GRIPPER)
                           + float(abs(rng.normal(0, 0.0012))))
                repeat = (BASE_REPEAT + f * (S8_REPEAT_END - BASE_REPEAT)
                          + float(abs(rng.normal(0, 0.0003))))

            if st == EOAT_TREND_STATION:
                # A TCP that walks steadily and stays in spec. Steady wear is a
                # maintenance schedule — the contrast against S2, where the rate
                # itself is climbing and that is a product question.
                tcp = (BASE_TCP + f * (TCP_END_MM - BASE_TCP)
                       + float(abs(rng.normal(0, 0.0010))))

            if st == TRANSIENT_STATION and di in TRANSIENT_DAYS:
                grade = TRANSIENT_GRADE + float(rng.normal(0, 0.03))
                sharpness = 0.82 + float(rng.normal(0, 0.004))

            rows.append(dict(
                production_day=day_ts.date().isoformat(),
                line_id=LINE_OF[st],
                station_id=st,
                units_processed=int((di + 1) * UNITS_PER_DAY),
                ref_brightness_pct=round(brightness, 2),
                ref_sharpness_score=round(sharpness, 4),
                datamatrix_grade=round(max(0.0, grade), 2),
                ref_bias_mm=round(abs(bias), 5),
                ref_repeatability_mm=round(repeat, 5),
                locator_wear_mm=round(locator, 5),
                clamp_offset_mm=round(clamp, 5),
                gripper_pad_wear_mm=round(gripper, 5),
                tcp_offset_mm=round(tcp, 5),
                jaw_parallelism_mm=round(jaw, 5),
            ))
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    return df


def main() -> None:
    df = generate()
    with open(CONFIG, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    print(f"Wrote {len(df):,} self-test records -> {OUT}")
    last = df[df.production_day == df.production_day.max()].sort_values("station_id")
    gmin = cfg["imaging_check"]["datamatrix_grade"]["min"]
    rmax = cfg["measurement_check"]["repeatability_mm"]["max"]
    sets = cfg["tooling_check"]["sets"]
    print(f"  final day — imaging (grade min {gmin}) · measurement (repeat max {rmax}) · "
          f"tooling by set:")
    for r in last.itertuples():
        row = r._asdict()
        flags = []
        if r.datamatrix_grade < gmin:
            flags.append("IMAGING")
        if r.ref_repeatability_mm > rmax:
            flags.append("MEASUREMENT")
        for sname, spec in sets.items():
            if any(row[col] > m["max"] for col, m in spec["measures"].items()):
                flags.append(f"TOOLING/{sname.upper()}")
        print(f"    {r.station_id}  grade {r.datamatrix_grade:4.2f} · "
              f"repeat {r.ref_repeatability_mm:.5f} · locator {r.locator_wear_mm:.5f} · "
              f"gripper {r.gripper_pad_wear_mm:.5f} · tcp {r.tcp_offset_mm:.5f}"
              f"   {' + '.join(flags) or 'GO'}")


if __name__ == "__main__":
    main()
