# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Silver model
# MAGIC
# MAGIC Turns Apple's string-typed bronze rows into clean, typed, de-duplicated tables:
# MAGIC
# MAGIC | Table | Grain |
# MAGIC |---|---|
# MAGIC | `silver_quantity_samples` | one measurement (steps, heart rate, weight…) from one source |
# MAGIC | `silver_sleep_segments` | one sleep-stage interval, assigned to the night it belongs to |
# MAGIC | `silver_workouts` | one workout, with its nested statistics flattened to columns |
# MAGIC | `silver_activity_summaries` | one day of Activity rings |
# MAGIC
# MAGIC Rules applied here:
# MAGIC * **Timestamps.** Apple writes `2026-03-14 07:12:44 -0400`. We keep both the true instant (UTC)
# MAGIC   and the wall-clock local time, because "which day was this?" must follow the clock on the
# MAGIC   wearer's wrist, not UTC.
# MAGIC * **Units.** Values are normalized to one unit per metric (lb, mi, kcal), whatever the
# MAGIC   phone's region settings were.
# MAGIC * **Re-exports.** Every Health export contains the full history, so landing a second export
# MAGIC   duplicates everything in bronze. Silver keeps one row per natural key, preferring the most
# MAGIC   recently ingested copy.
# MAGIC * **Bad values** are cast with `try_cast` and dropped, never allowed to fail the job.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "health")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
fq = f"`{catalog}`.`{schema}`"

# COMMAND ----------

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType

APPLE_TS = "yyyy-MM-dd HH:mm:ss xx"


def local_ts(col: str):
    """Wall-clock time as written on the device (drops the offset)."""
    return F.to_timestamp(F.substring(F.col(col), 1, 19), "yyyy-MM-dd HH:mm:ss")


def utc_ts(col: str):
    """The true instant, honoring the offset."""
    return F.to_timestamp(F.col(col), APPLE_TS)


def to_double(col: str):
    return F.expr(f"try_cast(`{col}` AS DOUBLE)")


def device_type(col: str):
    name = F.lower(F.col(col))
    return (F.when(name.contains("watch"), "watch")
             .when(name.contains("iphone"), "phone")
             .otherwise("other"))


def latest_per_key(df: DataFrame, keys: list[str]) -> DataFrame:
    w = Window.partitionBy(*keys).orderBy(F.col("_ingested_at").desc())
    return df.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")


def child_value(df: DataFrame, child: str, match_field: str, match: str, field: str) -> str:
    """SQL expression for `field` on the nested <child> element whose `match_field` equals `match`.

    Spark's XML reader infers a repeated child as ARRAY<STRUCT> only when some row has more
    than one; if every row has exactly one, it is a plain STRUCT. Real exports hit both
    shapes, so handle each (and a missing child) instead of assuming.
    """
    if child not in df.columns:
        return "CAST(NULL AS STRING)"
    if isinstance(df.schema[child].dataType, ArrayType):
        return f"try_element_at(filter(`{child}`, m -> m.`{match_field}` = '{match}'), 1).`{field}`"
    return f"CASE WHEN `{child}`.`{match_field}` = '{match}' THEN `{child}`.`{field}` END"


def save(df: DataFrame, table: str, comment: str) -> None:
    (df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fq}.{table}"))
    spark.sql(f"COMMENT ON TABLE {fq}.{table} IS '{comment}'")
    print(f"{table:<28} {spark.table(f'{fq}.{table}').count():>8,} rows")

# COMMAND ----------

# MAGIC %md ## Quantity samples

# COMMAND ----------

# HealthKit type -> (metric name, canonical unit, {source unit: multiplier to canonical})
METRICS = {
    "HKQuantityTypeIdentifierStepCount":                ("steps",          "count",     {"count": 1.0}),
    "HKQuantityTypeIdentifierDistanceWalkingRunning":   ("distance",       "mi",        {"mi": 1.0, "km": 0.621371, "m": 0.000621371}),
    "HKQuantityTypeIdentifierActiveEnergyBurned":       ("active_energy",  "kcal",      {"Cal": 1.0, "kcal": 1.0, "kJ": 0.239006}),
    "HKQuantityTypeIdentifierAppleExerciseTime":        ("exercise_time",  "min",       {"min": 1.0}),
    "HKQuantityTypeIdentifierHeartRate":                ("heart_rate",     "bpm",       {"count/min": 1.0}),
    "HKQuantityTypeIdentifierRestingHeartRate":         ("resting_heart_rate", "bpm",   {"count/min": 1.0}),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv_sdnn",       "ms",        {"ms": 1.0}),
    "HKQuantityTypeIdentifierBodyMass":                 ("body_mass",      "lb",        {"lb": 1.0, "kg": 2.20462}),
    "HKQuantityTypeIdentifierVO2Max":                   ("vo2_max",        "mL/kg/min", {"mL/min·kg": 1.0, "mL/(kg·min)": 1.0}),
}

metric_map = spark.createDataFrame(
    [(hk, m, u, src_unit, mult) for hk, (m, u, conv) in METRICS.items() for src_unit, mult in conv.items()],
    "hk_type STRING, metric STRING, unit STRING, source_unit STRING, multiplier DOUBLE",
)

bronze_records = spark.table(f"{fq}.bronze_records")
motion = F.expr(f"try_cast({child_value(bronze_records, 'MetadataEntry', '_key', 'HKMetadataKeyHeartRateMotionContext', '_value')} AS INT)")

quantity = (
    bronze_records
    .filter(F.col("_type").startswith("HKQuantityTypeIdentifier"))
    .select(
        F.col("_type").alias("hk_type"),
        F.col("_sourceName").alias("source_name"),
        F.col("_unit").alias("source_unit"),
        to_double("_value").alias("raw_value"),
        "_startDate", "_endDate",
        motion.alias("motion_context"),
        "_ingested_at",
    )
    .filter(F.col("raw_value").isNotNull())
)
quantity = latest_per_key(quantity, ["hk_type", "source_name", "_startDate", "_endDate", "raw_value"])

silver_quantity = (
    quantity.join(F.broadcast(metric_map), ["hk_type", "source_unit"], "inner")
    .select(
        "metric",
        (F.col("raw_value") * F.col("multiplier")).alias("value"),
        "unit",
        "source_name",
        device_type("source_name").alias("device_type"),
        local_ts("_startDate").alias("start_local"),
        local_ts("_endDate").alias("end_local"),
        utc_ts("_startDate").alias("start_utc"),
        utc_ts("_endDate").alias("end_utc"),
        "motion_context",
        "hk_type",
    )
    .withColumn("sample_date", F.to_date("start_local"))
    .withColumn("sample_hour", F.hour("start_local"))
)
save(silver_quantity, "silver_quantity_samples", "Silver: typed, unit-normalized, de-duplicated HealthKit quantity samples")

unmapped = (quantity.join(metric_map, ["hk_type", "source_unit"], "left_anti")
            .groupBy("hk_type", "source_unit").count())
if unmapped.count():
    print("Quantity types/units not modeled yet (left in bronze):")
    unmapped.show(truncate=False)

# COMMAND ----------

# MAGIC %md ## Sleep segments
# MAGIC
# MAGIC A segment belongs to the night that **ends** on `night_date`: shifting the start time forward
# MAGIC 12 hours puts 10:30 PM and 3:00 AM segments on the same morning.

# COMMAND ----------

sleep = (
    bronze_records
    .filter(F.col("_type") == "HKCategoryTypeIdentifierSleepAnalysis")
    .select(
        F.regexp_replace("_value", "^HKCategoryValueSleepAnalysis(Asleep)?", "").alias("stage"),
        F.col("_sourceName").alias("source_name"),
        "_startDate", "_endDate", "_ingested_at",
    )
)
sleep = latest_per_key(sleep, ["stage", "source_name", "_startDate", "_endDate"])
silver_sleep = (
    sleep.select(
        F.when(F.col("stage") == "", "Unspecified").otherwise(F.col("stage")).alias("stage"),
        "source_name",
        device_type("source_name").alias("device_type"),
        local_ts("_startDate").alias("start_local"),
        local_ts("_endDate").alias("end_local"),
        utc_ts("_startDate").alias("start_utc"),
        utc_ts("_endDate").alias("end_utc"),
    )
    .withColumn("minutes", (F.unix_timestamp("end_utc") - F.unix_timestamp("start_utc")) / 60.0)
    .withColumn("night_date", F.to_date(F.col("start_local") + F.expr("INTERVAL 12 HOURS")))
    .filter("minutes > 0")
)
save(silver_sleep, "silver_sleep_segments", "Silver: sleep-stage intervals assigned to the night (wake date) they belong to")

# COMMAND ----------

# MAGIC %md ## Workouts
# MAGIC
# MAGIC Current exports put totals in nested `<WorkoutStatistics>` children; older ones used
# MAGIC `totalDistance` / `totalEnergyBurned` attributes. Both are handled.

# COMMAND ----------

ACTIVITY_NAMES = {
    "TraditionalStrengthTraining": "Strength",
    "FunctionalStrengthTraining": "Strength",
    "HighIntensityIntervalTraining": "HIIT",
}

bronze_workouts = spark.table(f"{fq}.bronze_workouts")
cols = set(bronze_workouts.columns)


def stat(hk_suffix: str, field: str):
    sql = child_value(bronze_workouts, "WorkoutStatistics", "_type", f"HKQuantityTypeIdentifier{hk_suffix}", field)
    return F.expr(f"try_cast({sql} AS DOUBLE)")


def legacy(col: str):
    return to_double(col) if col in cols else F.lit(None).cast("double")


indoor = F.expr(child_value(bronze_workouts, "MetadataEntry", "_key", "HKIndoorWorkout", "_value"))

activity_raw = F.regexp_replace("_workoutActivityType", "^HKWorkoutActivityType", "")
activity = F.coalesce(
    F.element_at(F.create_map(*[F.lit(x) for kv in ACTIVITY_NAMES.items() for x in kv]), activity_raw),
    F.regexp_replace(activity_raw, "([a-z])([A-Z])", "$1 $2"),
)

workouts = latest_per_key(bronze_workouts, ["_workoutActivityType", "_startDate", "_endDate", "_sourceName"])
silver_workouts = (
    workouts.select(
        F.sha2(F.concat_ws("|", "_workoutActivityType", "_startDate", "_sourceName"), 256).substr(1, 16).alias("workout_id"),
        activity.alias("activity"),
        F.col("_sourceName").alias("source_name"),
        local_ts("_startDate").alias("start_local"),
        local_ts("_endDate").alias("end_local"),
        utc_ts("_startDate").alias("start_utc"),
        utc_ts("_endDate").alias("end_utc"),
        F.when(F.col("_durationUnit") == "min", to_double("_duration"))
         .when(F.col("_durationUnit") == "hr", to_double("_duration") * 60)
         .when(F.col("_durationUnit") == "s", to_double("_duration") / 60)
         .alias("duration_min"),
        F.coalesce(stat("ActiveEnergyBurned", "_sum"), legacy("_totalEnergyBurned")).alias("energy_kcal"),
        F.coalesce(stat("DistanceWalkingRunning", "_sum"), stat("DistanceCycling", "_sum"),
                   legacy("_totalDistance")).alias("distance_mi"),
        stat("HeartRate", "_average").alias("avg_hr"),
        stat("HeartRate", "_maximum").alias("max_hr"),
        (indoor == "1").alias("is_indoor"),
    )
    .withColumn("workout_date", F.to_date("start_local"))
    .withColumn("pace_min_per_mi",
                F.when(F.col("activity").isin("Running", "Walking", "Hiking") & (F.col("distance_mi") > 0.1),
                       F.col("duration_min") / F.col("distance_mi")))
)
save(silver_workouts, "silver_workouts", "Silver: one row per workout with nested statistics flattened")

# COMMAND ----------

# MAGIC %md ## Activity summaries

# COMMAND ----------

summ = latest_per_key(spark.table(f"{fq}.bronze_activity_summaries"), ["_dateComponents"])
silver_summaries = summ.select(
    F.to_date("_dateComponents").alias("summary_date"),
    to_double("_activeEnergyBurned").alias("move_kcal"),
    to_double("_activeEnergyBurnedGoal").alias("move_goal_kcal"),
    to_double("_appleExerciseTime").alias("exercise_min"),
    to_double("_appleExerciseTimeGoal").alias("exercise_goal_min"),
    to_double("_appleStandHours").alias("stand_hours"),
    to_double("_appleStandHoursGoal").alias("stand_goal_hours"),
).withColumn(
    "rings_closed",
    (F.col("move_kcal") >= F.col("move_goal_kcal")).cast("int")
    + (F.col("exercise_min") >= F.col("exercise_goal_min")).cast("int")
    + (F.col("stand_hours") >= F.col("stand_goal_hours")).cast("int"),
)
save(silver_summaries, "silver_activity_summaries", "Silver: daily Activity ring totals and goals")
