# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Gold model
# MAGIC
# MAGIC Analytical tables the dashboard reads:
# MAGIC
# MAGIC | Table | Grain | Answers |
# MAGIC |---|---|---|
# MAGIC | `gold_daily_metrics` | one row per calendar day (gap-free date spine) | activity, heart, sleep and body trends, with 7- and 28-day rolling averages |
# MAGIC | `gold_weekly_summary` | one row per ISO week | training load and recovery week over week |
# MAGIC | `gold_workouts` | one row per workout | what was done, how hard, how fast |
# MAGIC | `gold_hr_zone_minutes` | month × heart-rate zone | how training intensity was distributed |
# MAGIC | `gold_hourly_step_profile` | weekday × hour | when in the week movement happens |
# MAGIC
# MAGIC **Double counting.** An iPhone in a pocket and an Apple Watch on a wrist both record the same
# MAGIC walk. Summing every sample over-counts steps (by about 37% on the sample data). Apple's own app resolves this by
# MAGIC source priority, and so do we: within each hour, an additive metric comes from the
# MAGIC highest-priority device that recorded it (watch → phone → anything else).
# MAGIC
# MAGIC The notebook ends with data-quality checks that fail the job rather than publish bad numbers.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "health")
dbutils.widgets.text("max_heart_rate", "183")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
max_hr = float(dbutils.widgets.get("max_heart_rate"))
fq = f"`{catalog}`.`{schema}`"

# COMMAND ----------

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

quantity = spark.table(f"{fq}.silver_quantity_samples")
sleep = spark.table(f"{fq}.silver_sleep_segments")
workouts = spark.table(f"{fq}.silver_workouts")
summaries = spark.table(f"{fq}.silver_activity_summaries")

DEVICE_PRIORITY = F.when(F.col("device_type") == "watch", 1).when(F.col("device_type") == "phone", 2).otherwise(3)


def save(df: DataFrame, table: str, comment: str) -> None:
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fq}.{table}")
    spark.sql(f"COMMENT ON TABLE {fq}.{table} IS '{comment}'")
    print(f"{table:<28} {spark.table(f'{fq}.{table}').count():>8,} rows")

# COMMAND ----------

# MAGIC %md ## Additive metrics, de-duplicated by source priority

# COMMAND ----------

ADDITIVE = ["steps", "distance", "active_energy", "exercise_time"]

hourly_by_device = (
    quantity.filter(F.col("metric").isin(ADDITIVE))
    .groupBy("metric", "sample_date", "sample_hour", "device_type")
    .agg(F.sum("value").alias("value"))
    .withColumn("priority", DEVICE_PRIORITY)
)
w = Window.partitionBy("metric", "sample_date", "sample_hour").orderBy("priority")
hourly = (hourly_by_device.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1")
          .select("metric", "sample_date", "sample_hour", "device_type", "value"))

daily_additive = (
    hourly.groupBy("sample_date").pivot("metric", ADDITIVE).agg(F.sum("value"))
    .select(
        F.col("sample_date").alias("date"),
        F.round("steps").cast("int").alias("steps"),
        F.round("distance", 2).alias("distance_mi"),
        F.round("active_energy").alias("active_kcal"),
        F.round("exercise_time").alias("exercise_min"),
    )
)

naive_steps = quantity.filter("metric = 'steps'").groupBy("sample_date").agg(F.sum("value").alias("naive_steps"))

# COMMAND ----------

# MAGIC %md ## Heart and body

# COMMAND ----------

daily_heart_body = (
    quantity.filter(F.col("metric").isin("heart_rate", "resting_heart_rate", "hrv_sdnn", "body_mass", "vo2_max"))
    .groupBy(F.col("sample_date").alias("date"))
    .agg(
        F.round(F.avg(F.when(F.col("metric") == "resting_heart_rate", F.col("value"))), 1).alias("resting_hr"),
        F.round(F.avg(F.when(F.col("metric") == "heart_rate", F.col("value"))), 1).alias("avg_hr"),
        F.max(F.when(F.col("metric") == "heart_rate", F.col("value"))).alias("max_hr"),
        F.round(F.avg(F.when(F.col("metric") == "hrv_sdnn", F.col("value"))), 1).alias("hrv_ms"),
        F.round(F.avg(F.when(F.col("metric") == "body_mass", F.col("value"))), 1).alias("weight_lb"),
        F.round(F.max(F.when(F.col("metric") == "vo2_max", F.col("value"))), 1).alias("vo2_max"),
    )
)

# COMMAND ----------

# MAGIC %md ## Sleep
# MAGIC
# MAGIC Stages come from the best device that recorded stages that night; "in bed" comes from the
# MAGIC iPhone's bedtime schedule when present, otherwise from the first-to-last stage span.

# COMMAND ----------

ASLEEP = ["Core", "Deep", "REM", "Unspecified"]

stages = sleep.filter(F.col("stage") != "InBed").withColumn("priority", DEVICE_PRIORITY)
best = stages.groupBy("night_date").agg(F.min("priority").alias("priority"))
stages = stages.join(best, ["night_date", "priority"])

nightly = (
    stages.groupBy("night_date")
    .agg(
        F.sum(F.when(F.col("stage").isin(ASLEEP), F.col("minutes"))).alias("asleep_min"),
        F.sum(F.when(F.col("stage") == "Deep", F.col("minutes"))).alias("deep_min"),
        F.sum(F.when(F.col("stage") == "Core", F.col("minutes"))).alias("core_min"),
        F.sum(F.when(F.col("stage") == "REM", F.col("minutes"))).alias("rem_min"),
        F.sum(F.when(F.col("stage") == "Awake", F.col("minutes"))).alias("awake_min"),
        F.min("start_local").alias("sleep_start"),
        F.max("end_local").alias("sleep_end"),
    )
)
in_bed = (sleep.filter("stage = 'InBed'").groupBy("night_date")
          .agg(F.sum("minutes").alias("in_bed_min"), F.min("start_local").alias("bedtime")))

daily_sleep = (
    nightly.join(in_bed, "night_date", "left")
    .select(
        F.col("night_date").alias("date"),
        F.round(F.col("asleep_min") / 60, 2).alias("sleep_hours"),
        F.round("deep_min").alias("deep_min"),
        F.round("core_min").alias("core_min"),
        F.round("rem_min").alias("rem_min"),
        F.round("awake_min").alias("awake_min"),
        F.date_format(F.coalesce("bedtime", "sleep_start"), "HH:mm").alias("bedtime"),
        F.date_format("sleep_end", "HH:mm").alias("wake_time"),
        F.round(F.col("asleep_min") / F.coalesce(
            "in_bed_min", (F.unix_timestamp("sleep_end") - F.unix_timestamp("sleep_start")) / 60), 3
        ).alias("sleep_efficiency"),
    )
)

# COMMAND ----------

# MAGIC %md ## Workouts per day

# COMMAND ----------

daily_workouts = workouts.groupBy(F.col("workout_date").alias("date")).agg(
    F.count("*").alias("workouts"),
    F.round(F.sum("duration_min")).alias("workout_min"),
    F.round(F.sum(F.when(F.col("activity") == "Running", F.col("distance_mi"))), 2).alias("run_mi"),
)

# COMMAND ----------

# MAGIC %md ## gold_daily_metrics: everything on a gap-free date spine

# COMMAND ----------

bounds = daily_additive.agg(F.min("date").alias("lo"), F.max("date").alias("hi"))
spine = bounds.select(F.explode(F.sequence("lo", "hi")).alias("date"))

daily = (
    spine
    .join(daily_additive, "date", "left")
    .join(naive_steps.withColumnRenamed("sample_date", "date"), "date", "left")
    .join(daily_heart_body, "date", "left")
    .join(daily_sleep, "date", "left")
    .join(daily_workouts, "date", "left")
    .join(summaries.select(F.col("summary_date").alias("date"), "rings_closed", "stand_hours"), "date", "left")
    .fillna(0, subset=["workouts", "workout_min", "run_mi"])
    .withColumn("day_of_week", F.date_format("date", "E"))
    .withColumn("is_weekend", F.dayofweek("date").isin(1, 7))
    .withColumn("naive_steps", F.round("naive_steps").cast("int"))
)

trailing7 = Window.orderBy("date").rowsBetween(-6, 0)
trailing28 = Window.orderBy("date").rowsBetween(-27, 0)
for col, digits in [("steps", 0), ("resting_hr", 1), ("hrv_ms", 1), ("sleep_hours", 2), ("active_kcal", 0)]:
    daily = daily.withColumn(f"{col}_7d", F.round(F.avg(col).over(trailing7), digits))
daily = daily.withColumn("steps_28d", F.round(F.avg("steps").over(trailing28), 0))
# carry the last weigh-in / VO2 max estimate forward so trend lines have no holes
carry = Window.orderBy("date").rowsBetween(Window.unboundedPreceding, 0)
daily = (daily
         .withColumn("weight_lb_filled", F.last("weight_lb", ignorenulls=True).over(carry))
         .withColumn("vo2_max_filled", F.last("vo2_max", ignorenulls=True).over(carry)))

save(daily.orderBy("date"), "gold_daily_metrics", "Gold: one row per day of activity, heart, sleep and body metrics with rolling averages")

# COMMAND ----------

# MAGIC %md ## gold_weekly_summary

# COMMAND ----------

weekly = (
    spark.table(f"{fq}.gold_daily_metrics")
    .withColumn("week_start", F.date_trunc("week", "date").cast("date"))
    .groupBy("week_start")
    .agg(
        F.count("*").alias("days"),
        F.round(F.avg("steps")).cast("int").alias("avg_steps"),
        F.round(F.sum("distance_mi"), 1).alias("distance_mi"),
        F.round(F.sum("run_mi"), 1).alias("run_mi"),
        F.sum("workouts").alias("workouts"),
        F.round(F.sum("workout_min")).alias("workout_min"),
        F.round(F.avg("sleep_hours"), 2).alias("avg_sleep_hours"),
        F.round(F.avg("resting_hr"), 1).alias("avg_resting_hr"),
        F.round(F.avg("hrv_ms"), 1).alias("avg_hrv_ms"),
        F.round(F.avg("weight_lb"), 1).alias("avg_weight_lb"),
    )
)
save(weekly.orderBy("week_start"), "gold_weekly_summary", "Gold: weekly training and recovery summary (weeks start Monday)")

# COMMAND ----------

# MAGIC %md ## gold_workouts and gold_hr_zone_minutes
# MAGIC
# MAGIC Heart-rate samples are range-joined onto the workout they fall inside. Each sample counts
# MAGIC for the time until the next one (capped at 5 minutes, so watch dropouts don't inflate a zone).
# MAGIC Zones are the common five-zone model on percent of max heart rate
# MAGIC (`max_heart_rate` job parameter, default 183).

# COMMAND ----------

hr = quantity.filter("metric = 'heart_rate'").select("start_utc", F.col("value").alias("bpm"))
w_hr = workouts.select("workout_id", "activity", "workout_date", "start_utc", "end_utc")
in_workout = (
    hr.alias("h").join(w_hr.alias("w"),
                       (F.col("h.start_utc") >= F.col("w.start_utc")) & (F.col("h.start_utc") < F.col("w.end_utc")))
    .select("w.workout_id", "w.activity", "w.workout_date", F.col("h.start_utc").alias("t"), "h.bpm",
            F.col("w.end_utc").alias("workout_end"))
)
nxt = Window.partitionBy("workout_id").orderBy("t")
in_workout = in_workout.withColumn(
    "minutes",
    F.least(F.lit(5.0),
            (F.unix_timestamp(F.coalesce(F.lead("t").over(nxt), F.col("workout_end"))) - F.unix_timestamp("t")) / 60.0),
)
pct = F.col("bpm") / F.lit(max_hr)
in_workout = in_workout.withColumn(
    "zone",
    F.when(pct < 0.6, "Z1 Recovery").when(pct < 0.7, "Z2 Endurance").when(pct < 0.8, "Z3 Tempo")
     .when(pct < 0.9, "Z4 Threshold").otherwise("Z5 Max"),
)

zone_by_workout = (in_workout.groupBy("workout_id").pivot("zone", ["Z1 Recovery", "Z2 Endurance", "Z3 Tempo", "Z4 Threshold", "Z5 Max"])
                   .agg(F.round(F.sum("minutes"), 1)))
zone_by_workout = zone_by_workout.toDF("workout_id", "z1_min", "z2_min", "z3_min", "z4_min", "z5_min")

gold_workouts = (
    workouts.join(zone_by_workout, "workout_id", "left")
    .select(
        "workout_id", "workout_date", "activity",
        F.date_format("start_local", "HH:mm").alias("start_time"),
        F.round("duration_min", 1).alias("duration_min"),
        F.round("distance_mi", 2).alias("distance_mi"),
        F.round("energy_kcal").alias("energy_kcal"),
        F.round("avg_hr").alias("avg_hr"),
        F.round("max_hr").alias("max_hr"),
        F.round("pace_min_per_mi", 2).alias("pace_min_per_mi"),
        "is_indoor", "z1_min", "z2_min", "z3_min", "z4_min", "z5_min",
    )
)
save(gold_workouts.orderBy("workout_date", "start_time"), "gold_workouts", "Gold: one row per workout with pace and time in heart-rate zones")

zones_monthly = (
    in_workout.withColumn("month", F.date_trunc("month", "workout_date").cast("date"))
    .groupBy("month", "zone").agg(F.round(F.sum("minutes"), 1).alias("minutes"))
)
save(zones_monthly.orderBy("month", "zone"), "gold_hr_zone_minutes", "Gold: workout minutes per heart-rate zone per month")

# COMMAND ----------

# MAGIC %md ## gold_hourly_step_profile
# MAGIC
# MAGIC Average steps for each weekday × hour, dividing by the number of those weekdays in the data
# MAGIC (so an hour with no samples counts as zero, not as missing).

# COMMAND ----------

steps_hourly = hourly.filter("metric = 'steps'").withColumn("dow", F.dayofweek("sample_date"))
days_per_dow = spark.table(f"{fq}.gold_daily_metrics").groupBy(F.dayofweek("date").alias("dow")).agg(F.count("*").alias("n_days"))
profile = (
    steps_hourly.groupBy("dow", "sample_hour").agg(F.sum("value").alias("total"))
    .join(days_per_dow, "dow")
    .select(
        # 1 = Monday ... 7 = Sunday
        (((F.col("dow") + 5) % 7) + 1).alias("weekday_num"),
        F.date_format(F.make_date(F.lit(2024), F.lit(1), (((F.col("dow") + 5) % 7) + 1)), "E").alias("weekday"),
        F.col("sample_hour").alias("hour"),
        F.round(F.col("total") / F.col("n_days")).cast("int").alias("avg_steps"),
    )
)
save(profile.orderBy("weekday_num", "hour"), "gold_hourly_step_profile", "Gold: average steps by weekday and hour of day")

# COMMAND ----------

# MAGIC %md ## Data-quality gates
# MAGIC
# MAGIC Any failure raises, which fails the job before stale or wrong numbers reach the dashboard.

# COMMAND ----------

d = spark.table(f"{fq}.gold_daily_metrics")
checks = {
    "one row per date": d.groupBy("date").count().filter("count > 1").count() == 0,
    "date spine has no gaps": d.count() == d.agg(F.datediff(F.max("date"), F.min("date")) + 1).first()[0],
    "steps are never negative": d.filter("steps < 0").count() == 0,
    "steps recorded on >= 90% of days": d.filter("steps > 0").count() >= 0.9 * d.count(),
    "de-duplicated steps never exceed naive sum": d.filter("steps > naive_steps").count() == 0,
    "sleep hours within 0-16": d.filter("sleep_hours < 0 OR sleep_hours > 16").count() == 0,
    "resting HR within 30-120 bpm": d.filter("resting_hr < 30 OR resting_hr > 120").count() == 0,
    "workout zone minutes reconcile to duration (±10%)": spark.table(f"{fq}.gold_workouts").filter(
        "z1_min IS NOT NULL AND abs(coalesce(z1_min,0)+coalesce(z2_min,0)+coalesce(z3_min,0)+coalesce(z4_min,0)+coalesce(z5_min,0) - duration_min) > 0.1 * duration_min"
    ).count() == 0,
}
for name, ok in checks.items():
    print(("PASS  " if ok else "FAIL  ") + name)
failed = [n for n, ok in checks.items() if not ok]
assert not failed, f"Data-quality checks failed: {failed}"
