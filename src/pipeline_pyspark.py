"""
Databricks port of the reference pipeline (src/metrics.py + src/pipeline_pandas.py).

Runs on Databricks Free Edition (Spark is provided). It is a 1:1 port of the
TESTED pandas logic: first-pass yield, 7-day rolling FPY via a window function,
and p-chart control limits with per-subgroup 3-sigma limits. This is the
"build and maintain pipelines in a cloud lakehouse" capability, in the vendor's
own dialect.

How to run on Databricks Free Edition (free, no cloud account/card):
  1. Sign up:  https://www.databricks.com/signup/free-edition   (see runbooks/)
  2. Upload data/raw/inspection_events.csv to a Volume (or DBFS).
  3. New notebook -> paste this file -> set RAW_PATH -> Run all.
  4. Outputs Delta tables quality.daily_fpy and quality.station_scorecard,
     and displays the scorecard. Compare to the pandas run — they match.
"""
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

RAW_PATH = "/Volumes/main/default/inspection/inspection_events.csv"  # <-- set after upload
ROLLING_DAYS = 7
SIGMA = 3

spark = SparkSession.builder.getOrCreate()

events = spark.read.option("header", True).option("inferSchema", True).csv(RAW_PATH)

first_pass = (
    events.filter(F.col("inspection_pass") == 1)
    .withColumn("d", F.to_date("ts"))
    .withColumn("is_fail", (F.col("vision_result") == "FAIL").cast("int"))
)

daily = (
    first_pass.groupBy("station_id", "d")
    .agg(F.count("*").alias("n"), F.sum("is_fail").alias("fails"))
    .withColumn("defect_rate", F.col("fails") / F.col("n"))
    .withColumn("fpy", F.lit(1.0) - F.col("defect_rate"))
)

roll = Window.partitionBy("station_id").orderBy("d").rowsBetween(-(ROLLING_DAYS - 1), 0)
per_station = Window.partitionBy("station_id")

marts = (
    daily.withColumn("rolling_fpy", F.avg("fpy").over(roll))
    .withColumn("pbar", F.sum("fails").over(per_station) / F.sum("n").over(per_station))
    .withColumn("se", F.sqrt(F.col("pbar") * (F.lit(1.0) - F.col("pbar")) / F.col("n")))
    .withColumn("ucl", F.least(F.col("pbar") + SIGMA * F.col("se"), F.lit(1.0)))
    .withColumn("lcl", F.greatest(F.col("pbar") - SIGMA * F.col("se"), F.lit(0.0)))
    .withColumn(
        "out_of_control",
        (F.col("defect_rate") > F.col("ucl")) | (F.col("defect_rate") < F.col("lcl")),
    )
)

scorecard = (
    marts.groupBy("station_id")
    .agg(
        F.sum("n").alias("n_first_pass"),
        F.sum("fails").alias("defects"),
        F.sum(F.col("out_of_control").cast("int")).alias("spc_violations"),
    )
    .withColumn("fpy_overall", F.lit(1.0) - F.col("defects") / F.col("n_first_pass"))
)

marts.write.mode("overwrite").saveAsTable("quality.daily_fpy")
scorecard.write.mode("overwrite").saveAsTable("quality.station_scorecard")

try:
    display(scorecard.orderBy("station_id"))  # noqa: F821  (Databricks builtin)
except NameError:
    scorecard.orderBy("station_id").show()
