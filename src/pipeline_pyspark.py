"""
Databricks port of the reference pipeline (src/metrics.py + src/pipeline_pandas.py).

Runs on Databricks Free Edition (Spark is provided). It is a faithful port of the
TESTED pandas logic, and reproduces data/marts/*.csv column-for-column:

  dedupe on event_id (the control-plan DQ gate)
    -> first-pass events only
    -> daily n / fails / defect_rate / fpy per station
    -> 7-day rolling FPY via a window function
    -> p-chart limits frozen on the FIRST `BASELINE_DAYS` subgroups per station
    -> Cpk (overall + recent) against the control-plan spec window
    -> station scorecard with triage status

Why the baseline is frozen (config/control_plan.yaml: spc.baseline_days = 21):
a sustained shift must not be allowed to inflate its own control limits, which
would mask the very shift you are trying to catch. Computing pbar over the whole
window instead of the baseline drops total violations from 26 to 10 on this data
-- station S5 falls from 13 to 2 -- because the shift raises its own UCL.

Parity: the identical logic was executed as SQL window functions against
data/raw/inspection_events.csv and diffed against data/marts/daily_fpy.csv and
data/marts/station_scorecard.csv -- exact match on all 19 columns.

How to run on Databricks Free Edition (free, no cloud account/card):
  1. Sign up:  https://www.databricks.com/signup/free-edition   (see runbooks/)
  2. Upload data/raw/inspection_events.csv to a Volume (Free Edition has Unity
     Catalog on and DBFS off, so it must be a Volume, not /dbfs).
  3. New notebook -> paste this file -> set RAW_PATH / OUT_VOLUME -> Run all.
  4. It writes Delta tables quality.daily_fpy and quality.station_scorecard AND
     two CSVs to OUT_VOLUME. Download those CSVs into data/marts/ and rerun
     `python -m src.scorecard && python -m src.dashboard` -- the published pages
     then render Databricks output directly.
"""
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

# --- paths -------------------------------------------------------------------
RAW_PATH = "/Volumes/workspace/quality/inspection/inspection_events.csv"  # <-- set after upload
OUT_VOLUME = "/Volumes/workspace/quality/inspection"                      # <-- CSVs land here

# --- control plan constants (config/control_plan.yaml) ------------------------
ROLLING_DAYS = 7        # spc.rolling_window_days
SIGMA = 3               # spc.sigma
BASELINE_DAYS = 21      # spc.baseline_days -- frozen Phase I baseline
RECENT_DAYS = 14        # trailing window for cpk_recent
LSL, USL = 24.80, 25.20  # features[0].lsl_mm / usl_mm
CPK_MIN = 1.33          # features[0].cpk_min
DIM_SLACK = 5.0         # capability uses measurements within spec +/- this

spark = SparkSession.builder.getOrCreate()

events = spark.read.option("header", True).option("inferSchema", True).csv(RAW_PATH)

# --- 1. DQ gate: drop duplicate event_id, keeping the first occurrence --------
# Mirrors validation.py: clean = df.drop_duplicates(subset=["event_id"], keep="first").
# On this dataset the 294 duplicate pairs are exact row copies, so "first" is
# unambiguous; the row_number below makes the choice deterministic regardless.
ordered = events.withColumn("_rn", F.monotonically_increasing_id())
clean = (
    ordered.withColumn("_k", F.row_number().over(Window.partitionBy("event_id").orderBy("_rn")))
    .filter(F.col("_k") == 1)
    .drop("_rn", "_k")
)

first_pass = (
    clean.filter(F.col("inspection_pass") == 1)
    .withColumn("date", F.to_date("ts"))
    .withColumn("is_fail", (F.col("vision_result") == "FAIL").cast("int"))
)

# --- 2. daily yield ----------------------------------------------------------
daily = (
    first_pass.groupBy("station_id", "date")
    .agg(F.count("*").alias("n"), F.sum("is_fail").alias("fails"))
    .withColumn("defect_rate", F.col("fails") / F.col("n"))
    .withColumn("fpy", F.lit(1.0) - F.col("defect_rate"))
)

roll = Window.partitionBy("station_id").orderBy("date").rowsBetween(-(ROLLING_DAYS - 1), 0)
seq = Window.partitionBy("station_id").orderBy("date")

daily = (
    daily.withColumn("rolling_fpy", F.avg("fpy").over(roll))
    .withColumn("rn", F.row_number().over(seq))
)

# --- 3. p-chart limits FROZEN on the first BASELINE_DAYS subgroups -----------
baseline = (
    daily.filter(F.col("rn") <= BASELINE_DAYS)
    .groupBy("station_id")
    .agg((F.sum("fails") / F.sum("n")).alias("pbar"))
)

marts = (
    daily.join(baseline, "station_id")
    .withColumn("se", F.sqrt(F.col("pbar") * (F.lit(1.0) - F.col("pbar")) / F.col("n")))
    .withColumn("ucl", F.least(F.col("pbar") + SIGMA * F.col("se"), F.lit(1.0)))
    .withColumn("lcl", F.greatest(F.col("pbar") - SIGMA * F.col("se"), F.lit(0.0)))
    .withColumn(
        "out_of_control",
        (F.col("defect_rate") > F.col("ucl")) | (F.col("defect_rate") < F.col("lcl")),
    )
    .select("station_id", "date", "n", "fails", "defect_rate", "fpy",
            "rolling_fpy", "pbar", "ucl", "lcl", "out_of_control")
)

# --- 4. capability (Cpk) on in-range first-pass measurements -----------------
# Cpk = min((USL-mu)/3s, (mu-LSL)/3s), sample stddev (ddof=1) == stddev_samp.
valid_dim = first_pass.filter(F.col("feature_dim_mm").between(LSL - DIM_SLACK, USL + DIM_SLACK))
# cutoff comes from ALL first-pass events (pipeline_pandas.py: fp["ts"].max()),
# not just the in-range ones -- otherwise a glitchy final day would move it.
cutoff = first_pass.select(F.date_sub(F.max("date"), RECENT_DAYS).alias("c")).first()["c"]


def _cpk(df, alias):
    mu, sd = F.avg("feature_dim_mm"), F.stddev_samp("feature_dim_mm")
    return df.groupBy("station_id").agg(
        F.round(F.least((F.lit(USL) - mu) / (3 * sd), (mu - F.lit(LSL)) / (3 * sd)), 3).alias(alias)
    )


cpk_overall = _cpk(valid_dim, "cpk_overall")
cpk_recent = _cpk(valid_dim.filter(F.col("date") >= F.lit(cutoff)), "cpk_recent")

# --- 5. station scorecard ----------------------------------------------------
last_day = Window.partitionBy("station_id").orderBy(F.col("date").desc())

agg = (
    marts.withColumn("_last", F.row_number().over(last_day))
    .groupBy("station_id")
    .agg(
        F.sum("n").alias("n_first_pass"),
        F.sum("fails").alias("defects"),
        F.sum(F.col("out_of_control").cast("int")).alias("spc_violations"),
        F.min(F.when(F.col("out_of_control"), F.col("date"))).alias("spc_first_violation"),
        F.round(F.max(F.when(F.col("_last") == 1, F.col("rolling_fpy"))), 4).alias("rolling_fpy_latest"),
        F.round(F.lit(1.0) - F.sum("fails") / F.sum("n"), 4).alias("fpy_overall"),
    )
)

lines = first_pass.groupBy("station_id").agg(F.min("line_id").alias("line_id"))

scorecard = (
    agg.join(lines, "station_id").join(cpk_overall, "station_id").join(cpk_recent, "station_id")
    .withColumn(
        "status",
        F.when((F.col("cpk_recent") < CPK_MIN) | (F.col("spc_violations") >= 3), "ALERT")
        .when(F.col("spc_violations") >= 1, "WATCH")
        .otherwise("OK"),
    )
    .select("station_id", "line_id", "n_first_pass", "fpy_overall", "rolling_fpy_latest",
            "cpk_overall", "cpk_recent", "defects", "spc_violations",
            "spc_first_violation", "status")
)

# --- 6. parity check ---------------------------------------------------------
# Expected from the tested pandas run: S3 ALERT (cpk_overall 0.678, cpk_recent
# 0.514, 11 violations) and S5 ALERT (cpk 1.478, 13 violations); S2 and S7 WATCH
# with 1 each; 26 violations total. If these do not appear, stop -- do not export.
try:
    display(scorecard.orderBy("station_id"))   # noqa: F821  (Databricks builtin)
    display(marts.orderBy("station_id", "date"))  # noqa: F821
except NameError:
    scorecard.orderBy("station_id").show(truncate=False)
    marts.orderBy("station_id", "date").show(20, truncate=False)

print("TOTAL spc_violations =",
      scorecard.agg(F.sum("spc_violations")).first()[0], "(expected 26)")

# --- 7. persist: Delta tables + CSVs shaped exactly like data/marts/*.csv -----
try:
    spark.sql("CREATE SCHEMA IF NOT EXISTS workspace.quality")
    # Drop first: an earlier build of this file wrote `d`/`se` columns, and Delta
    # refuses a plain overwrite when the schema differs (DELTA_METADATA_MISMATCH).
    spark.sql("DROP TABLE IF EXISTS workspace.quality.daily_fpy")
    spark.sql("DROP TABLE IF EXISTS workspace.quality.station_scorecard")
    (marts.write.mode("overwrite").option("overwriteSchema", "true")
     .saveAsTable("workspace.quality.daily_fpy"))
    (scorecard.write.mode("overwrite").option("overwriteSchema", "true")
     .saveAsTable("workspace.quality.station_scorecard"))
    print("Wrote Delta tables: workspace.quality.daily_fpy, workspace.quality.station_scorecard")
except Exception as exc:
    print("Table write skipped (the displayed tables above are the result):", exc)

# Single-file CSVs via pandas: 540 + 9 rows is trivial, and it avoids Spark's
# part-file directory so the download is one file with one header row.
try:
    (marts.orderBy("station_id", "date").toPandas()
     .to_csv(f"{OUT_VOLUME}/daily_fpy.csv", index=False))
    (scorecard.orderBy("station_id").toPandas()
     .to_csv(f"{OUT_VOLUME}/station_scorecard.csv", index=False))
    print(f"Wrote CSVs to {OUT_VOLUME}/ -- download into data/marts/ and rerun the generators.")
except Exception as exc:
    print("CSV write skipped:", exc)
