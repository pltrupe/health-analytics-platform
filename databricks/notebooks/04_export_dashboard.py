# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Export for the dashboard
# MAGIC
# MAGIC The React dashboard on Vercel is a static site: it reads small JSON snapshots of the gold
# MAGIC tables instead of querying a warehouse on every page view (fast, free to host, and no
# MAGIC credentials in the browser). This notebook writes those snapshots to the `exports` volume;
# MAGIC `scripts/pull_dashboard_data.sh` copies them into `dashboard/public/data/`.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "health")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
fq = f"`{catalog}`.`{schema}`"
out_dir = f"/Volumes/{catalog}/{schema}/exports/dashboard"

# COMMAND ----------

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal

from pyspark.sql import functions as F

os.makedirs(out_dir, exist_ok=True)


def _default(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    raise TypeError(type(v))


def write_json(name: str, payload) -> None:
    path = os.path.join(out_dir, f"{name}.json")
    with open(path, "w") as f:
        json.dump(payload, f, default=_default, separators=(",", ":"))
    print(f"{name + '.json':<26} {os.path.getsize(path) / 1024:8.1f} KB")


def rows(table: str, order: str, columns: list[str] | None = None) -> list[dict]:
    df = spark.table(f"{fq}.{table}")
    if columns:
        df = df.select(*columns)
    return [r.asDict() for r in df.orderBy(*order.split(",")).collect()]

# COMMAND ----------

daily = rows("gold_daily_metrics", "date", [
    "date", "day_of_week", "steps", "naive_steps", "steps_7d", "steps_28d", "distance_mi", "active_kcal",
    "exercise_min", "resting_hr", "resting_hr_7d", "avg_hr", "hrv_ms", "hrv_ms_7d", "sleep_hours",
    "sleep_hours_7d", "deep_min", "core_min", "rem_min", "awake_min", "bedtime", "wake_time",
    "sleep_efficiency", "weight_lb", "weight_lb_filled", "vo2_max", "vo2_max_filled", "workouts",
    "workout_min", "run_mi", "rings_closed",
])
write_json("daily", daily)
write_json("weekly", rows("gold_weekly_summary", "week_start"))
write_json("workouts", rows("gold_workouts", "workout_date,start_time"))
write_json("hr_zones", rows("gold_hr_zone_minutes", "month,zone"))
write_json("step_profile", rows("gold_hourly_step_profile", "weekday_num,hour"))

# COMMAND ----------

# MAGIC %md ## Headline KPIs
# MAGIC Last 30 days vs the 30 days before, plus year-to-date running totals.

# COMMAND ----------

d = spark.table(f"{fq}.gold_daily_metrics")
last_day = d.agg(F.max("date")).first()[0]


def window_avg(col: str, start_offset: int, end_offset: int):
    return (d.filter(F.col("date").between(F.date_sub(F.lit(last_day), start_offset), F.date_sub(F.lit(last_day), end_offset)))
             .agg(F.avg(col)).first()[0])


kpis = {}
for col in ["steps", "resting_hr", "hrv_ms", "sleep_hours", "active_kcal"]:
    kpis[col] = {"last_30": window_avg(col, 29, 0), "prior_30": window_avg(col, 59, 30)}

w = spark.table(f"{fq}.gold_workouts")
runs = w.filter("activity = 'Running'")
fastest = (runs.filter("distance_mi >= 3").orderBy("pace_min_per_mi")
           .select("workout_date", "distance_mi", "pace_min_per_mi").first())
longest = runs.orderBy(F.col("distance_mi").desc()).select("workout_date", "distance_mi", "duration_min").first()

counts = {t: spark.table(f"{fq}.{t}").count() for t in [
    "bronze_records", "bronze_workouts", "bronze_activity_summaries",
    "silver_quantity_samples", "silver_sleep_segments", "silver_workouts",
    "gold_daily_metrics", "gold_workouts",
]}

write_json("meta", {
    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "source": f"{catalog}.{schema}",
    "first_date": d.agg(F.min("date")).first()[0],
    "last_date": last_day,
    "kpis": kpis,
    "totals": {
        "workouts": w.count(),
        "runs": runs.count(),
        "run_miles": runs.agg(F.round(F.sum("distance_mi"), 1)).first()[0],
        "workout_hours": w.agg(F.round(F.sum("duration_min") / 60, 1)).first()[0],
    },
    "records": {
        "fastest_run": fastest.asDict() if fastest else None,
        "longest_run": longest.asDict() if longest else None,
    },
    "row_counts": counts,
})
